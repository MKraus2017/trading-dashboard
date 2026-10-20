"""Bounded, GET-only paper-research access; never imports a trading runtime.

The token authorizes only this blueprint, for the fixed existing owner 1. It is
not a login, broker credential or strategy-control credential. Snapshot contents
are frozen in bounded process RAM; expiry/restart requires a fresh capture.
"""
import base64
import bisect
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from contextlib import closing
from pathlib import Path

from flask import Blueprint, Response, request

PREFIX = "/api/astra/research/v1"
OWNER = 1
MAX_SNAPSHOTS = 2
SNAPSHOT_TTL = 600
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_ROW_BYTES = 256 * 1024
MAX_HISTORY_ROWS = 40000
MAX_QUERY_SECONDS = 8
SNAPSHOT_INTERVAL = 30
REQUESTS_PER_MINUTE = 120


class ResearchError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def fields(names):
    return dict.fromkeys(names.split())


RULES = fields("bar_seconds warmup_bars entry_channel exit_channel ema_period atr_period "
               "stop_atr risk_pct max_open_risk_pct max_positions max_notional_pct leverage "
               "max_hold_hours fee_pct slippage_pct direction signal_history_bars entry_extension "
               "additional_entry_channel ema_slope_lag rvol_period rvol_min")
RULES["symbols"] = [None]
SIGNAL = fields("bar_end bar_seconds close ema200 atr14 upper24 lower12 upper12 ema200_lag6 "
                "quote_volume rvol20 volume_valid trend_rising baseline_enter additional_enter "
                "additional_only entry_branch enter exit")
EVENT = fields("action observed_at reference")
ATTEMPT = fields("attempt observed_at action final completed data_error")
DECISION = dict(SIGNAL, **fields("version symbol timeframe_hours decision_schema final completed "
                                 "action observed_at last_attempt_at reference"),
                attempt_history=[ATTEMPT], execution_history=[EVENT])
POSITION = dict(fields("id symbol direction leverage quantity entry_price entry_reference entry_total "
                      "entry_fee margin_eur opened_at stop_loss take_profit planned_risk last_price "
                      "quote_at version management_version exit_price exit_reference exit_fee closed_at "
                      "close_reason pnl_eur pnl_pct"), signal=SIGNAL, rules=RULES, management_rules=RULES)
COINS = ("BTC", "ETH", "SOL")
PENDING = dict(fields("bar_end started_at expires_at attempts max_attempts last_attempt_at next_retry_at"),
               waiting_symbols=[None], entered_symbols=[None], closed_symbols=[None],
               signals={s: SIGNAL for s in COINS}, completed={s: EVENT for s in COINS},
               decision_records={s: DECISION for s in COINS},
               executions={s: [EVENT] for s in COINS}, last_errors={s: None for s in COINS})
PRIOR = dict(fields("version started_at ended_at equity cash closed_count positions_count rule_hash state_sha256"),
             rules=RULES, pending_bar=PENDING)
RUN = dict(fields("version rule_hash started_at version_started_at version_change_reason initial_capital cash "
                  "equity peak max_drawdown_pct fees_paid last_attempt_at last_success_at last_bar_end "
                  "missed_hours data_errors observation_gaps last_error last_sample_at monitor_success_at "
                  "monitor_error candle_error last_candle_success_at last_candle_bar_end baseline_start_equity "
                  "baseline_start_capital baseline_initial_reserve baseline_strategy baseline_last_strategy "
                  "baseline_equity baseline_peak baseline_max_drawdown_pct baseline_costs baseline_closed_count "
                  "baseline_at baseline_fresh"),
           rules=RULES, prior_versions=[PRIOR], positions=[POSITION], trades=[POSITION],
           pending_bar=PENDING, baseline_strategy_changes=[fields("at strategy")],
           baseline_invalid=[None], baseline_old_trades=[None], baseline_inherited=[None],
           baseline_seen_trades=[None], baseline_seen_open=[None])
REFERENCE_POSITION = fields("id symbol direction leverage margin_eur notional_eur size entry_price stop_loss "
                            "initial_sl_dist take_profit liquidation_price opened_at opened_at_ts reason "
                            "last_price unrealized_pct unrealized_eur trailing_active exit_price closed_at "
                            "pnl_eur pnl_pct close_reason liquidated")
REFERENCE = dict(fields("cash start_capital total_value total_return_pct drawdown_pct trading_halted "
                        "monitor_attempt_at monitor_success_at"),
                 positions=[REFERENCE_POSITION], trades=[REFERENCE_POSITION],
                 value_history=[fields("date value")], monitor_missing_symbols=[None])
EQUITY = fields("equity baseline_equity baseline_fresh data_ok")


def _project(value, schema, depth=0):
    """An allowlist at EVERY nesting level, not a secret-name blacklist."""
    if depth > 16:
        raise ResearchError(413, "data_limit")
    if value is None:
        return None
    if schema is None:
        if isinstance(value, str):
            if len(value) > 16384:
                raise ResearchError(413, "data_limit")
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ResearchError(503, "invalid_paper_data")
        elif not isinstance(value, (int, bool)):
            raise ResearchError(503, "invalid_paper_data")
        return value
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise ResearchError(503, "invalid_paper_data")
        if len(value) > MAX_HISTORY_ROWS:
            raise ResearchError(413, "data_limit")
        return [_project(item, schema[0], depth + 1) for item in value]
    if not isinstance(value, dict):
        raise ResearchError(503, "invalid_paper_data")
    return {key: _project(value[key], nested, depth + 1)
            for key, nested in schema.items() if key in value}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _decode(text):
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        raise ResearchError(503, "invalid_paper_data") from None
    if not isinstance(value, dict):
        raise ResearchError(503, "invalid_paper_data")
    return value


def _number(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise ResearchError(503, "invalid_paper_data")
    return value


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ResearchError(503, "invalid_paper_data")
    return value


def _read_document(connection, table, version=None):
    # table/column names are internal constants, never request input.
    where = "user_id=?" + (" AND version=?" if version is not None else "")
    args = (OWNER, version) if version is not None else (OWNER,)
    row = connection.execute("SELECT length(CAST(data AS BLOB)) FROM " + table + " WHERE " + where, args).fetchone()
    if row is None:
        raise ResearchError(503, "paper_data_unavailable")
    if row[0] is None or row[0] > MAX_DOCUMENT_BYTES:
        raise ResearchError(413, "data_limit")
    return _decode(connection.execute("SELECT data FROM " + table + " WHERE " + where, args).fetchone()[0])


def _capture(path, captured_at, monotonic):
    """One read transaction pins all source rows; no schema/bootstrap calls."""
    deadline = monotonic() + MAX_QUERY_SECONDS
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(monotonic() > deadline), 1000)
        connection.execute("BEGIN")
        versions = [r[0] for r in connection.execute(
            "SELECT version FROM astra_ultra_runs WHERE user_id=? ORDER BY version LIMIT 17", (OWNER,))]
        if not versions:
            raise ResearchError(503, "paper_data_unavailable")
        if len(versions) > 16:
            raise ResearchError(413, "data_limit")
        numbered = []
        for version in versions:
            match = re.fullmatch(r"astra_[a-z0-9_]+_v([1-9][0-9]{0,3})", _text(version))
            if not match:
                raise ResearchError(503, "invalid_paper_data")
            numbered.append((int(match[1]), version))
        if len({n for n, _ in numbered}) != len(numbered):
            raise ResearchError(503, "ambiguous_paper_version")
        latest = max(numbered)[1]
        run = _project(_read_document(connection, "astra_ultra_runs", latest), RUN)
        if run.get("version") != latest:
            raise ResearchError(503, "invalid_paper_data")
        reference = _project(_read_document(connection, "crypto_portfolios"), REFERENCE)
        document = dict(ok=True, schema_version=1, mode="paper_only", captured_at=captured_at,
                        expires_at=captured_at + SNAPSHOT_TTL, available_versions=versions,
                        astra=run, reference=reference)
        document_bytes = _encode(document)
        used = len(document_bytes)
        histories = {}
        total_rows = 0
        for kind, table, identity, order, schema in (
            ("decisions", "astra_ultra_decisions", "bar_end,version,symbol", "bar_end,version,symbol", DECISION),
            ("equity", "astra_ultra_equity", "observed_at,version", "observed_at,version", EQUITY),
        ):
            # COUNT and MAX lengths execute before fetching any potentially huge JSON cell.
            bounds = connection.execute(
                "SELECT COUNT(*),MAX(length(CAST(data AS BLOB))),SUM(length(CAST(data AS BLOB))) FROM "
                + table + " WHERE user_id=?", (OWNER,)).fetchone()
            if (total_rows + bounds[0] > MAX_HISTORY_ROWS or (bounds[1] or 0) > MAX_ROW_BYTES
                    or (bounds[2] or 0) > MAX_SNAPSHOT_BYTES):
                raise ResearchError(413, "data_limit")
            rows, keys = [], []
            for record in connection.execute(
                    "SELECT " + identity + ",data FROM " + table + " WHERE user_id=? ORDER BY " + order, (OWNER,)):
                key = (_number(record[0]), _text(record[1]))
                if key[1] not in versions:
                    raise ResearchError(503, "orphan_paper_history")
                if kind == "decisions":
                    key += (_text(record[2]),)
                row = _project(_decode(record[-1]), schema)
                row.update(version=key[1])
                if kind == "decisions":
                    row.update(bar_end=key[0], symbol=key[2])
                else:
                    row.update(observed_at=key[0])
                encoded = _encode(row)
                used += len(encoded) + 1
                if used > MAX_SNAPSHOT_BYTES:
                    raise ResearchError(413, "data_limit")
                keys.append(key)
                rows.append(encoded)
            histories[kind] = (tuple(keys), tuple(rows))
            total_rows += len(rows)
        # Closing rolls back only this connection's read transaction, with no writes.
    return document_bytes, histories


class SnapshotStore:
    def __init__(self, path, clock, monotonic):
        self.path, self.clock, self.monotonic = path, clock, monotonic
        self.lock = threading.RLock()
        self.snapshots = OrderedDict()
        self.requests = deque()
        self.last_creation = None
        self.generation = None
        self.cursor_key = secrets.token_bytes(32)

    def authorize(self, digest):
        with self.lock:
            if self.generation != digest:
                self.snapshots.clear()
                self.requests.clear()
                self.last_creation = None
                self.generation = digest
                self.cursor_key = secrets.token_bytes(32)
            now = self.monotonic()
            while self.requests and now - self.requests[0] >= 60:
                self.requests.popleft()
            if len(self.requests) >= REQUESTS_PER_MINUTE:
                raise ResearchError(429, "rate_limit")
            self.requests.append(now)
            for key in list(self.snapshots):
                if now >= self.snapshots[key]["deadline"]:
                    del self.snapshots[key]

    def create(self):
        with self.lock:
            now = self.monotonic()
            if self.last_creation is not None and now - self.last_creation < SNAPSHOT_INTERVAL:
                raise ResearchError(429, "snapshot_rate_limit")
            # Failed captures also count, bounding repeated expensive malformed-data scans.
            self.last_creation = now
            # Reserve a slot BEFORE constructing a third snapshot.
            while len(self.snapshots) >= MAX_SNAPSHOTS:
                self.snapshots.popitem(last=False)
            captured_at = self.clock()
            document, histories = _capture(self.path, captured_at, self.monotonic)
            snapshot_id = secrets.token_hex(16)
            counts = {kind: len(values[0]) for kind, values in histories.items()}
            envelope = json.loads(document)
            envelope.update(snapshot_id=snapshot_id, counts=counts)
            encoded = _encode(envelope)
            if len(encoded) + sum(sum(len(row) + 1 for row in values[1]) for values in histories.values()) > MAX_SNAPSHOT_BYTES:
                raise ResearchError(413, "data_limit")
            self.snapshots[snapshot_id] = dict(document=encoded, histories=histories,
                                              captured_at=captured_at, expires_at=captured_at + SNAPSHOT_TTL,
                                              deadline=now + SNAPSHOT_TTL)
            while len(self.snapshots) > MAX_SNAPSHOTS:
                self.snapshots.popitem(last=False)
            return encoded

    def _cursor(self, snapshot_id, kind, key):
        payload = _encode([snapshot_id, kind, list(key)])
        return base64.urlsafe_b64encode(payload + hmac.digest(self.cursor_key, payload, "sha256")).rstrip(b"=").decode("ascii")

    def _read_cursor(self, cursor, snapshot_id, kind):
        try:
            if len(cursor) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
                raise ValueError()
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(hmac.digest(self.cursor_key, payload, "sha256"), signature):
                raise ValueError()
            value = json.loads(payload)
            if not isinstance(value, list) or len(value) != 3 or value[:2] != [snapshot_id, kind]:
                raise ValueError()
            if not isinstance(value[2], list) or len(value[2]) != (3 if kind == "decisions" else 2):
                raise ValueError()
            key = (_number(value[2][0]), _text(value[2][1]))
            return key + (_text(value[2][2]),) if kind == "decisions" else key
        except (ValueError, TypeError, IndexError, ResearchError):
            raise ResearchError(400, "invalid_cursor") from None

    def page(self, snapshot_id, kind, cursor, limit):
        with self.lock:
            snapshot = self.snapshots.get(snapshot_id)
            if snapshot is None or self.monotonic() >= snapshot["deadline"]:
                raise ResearchError(410, "snapshot_expired")
            keys, rows = snapshot["histories"][kind]
            start = bisect.bisect_right(keys, self._read_cursor(cursor, snapshot_id, kind)) if cursor else 0
            end = min(len(rows), start + limit)
            next_cursor = self._cursor(snapshot_id, kind, keys[end - 1]) if end < len(rows) else None
            meta = dict(ok=True, schema_version=1, mode="paper_only", snapshot_id=snapshot_id,
                        captured_at=snapshot["captured_at"], expires_at=snapshot["expires_at"],
                        kind=kind, total=len(rows), next_cursor=next_cursor)
            # Frozen, already encoded rows avoid an unbounded decode/re-encode cycle.
            return _encode(meta)[:-1] + b',"rows":[' + b",".join(rows[start:end]) + b"]}"


def create_blueprint(db_path, *, clock=time.time, monotonic=time.monotonic):
    """Register on Flask; caller must skip general watcher bootstrap for PREFIX."""
    blueprint = Blueprint("astra_research", __name__, url_prefix=PREFIX)
    store = SnapshotStore(db_path, clock, monotonic)

    @blueprint.errorhandler(ResearchError)
    def known_error(error):
        response = Response(_encode(dict(ok=False, error=error.code)), status=error.status, mimetype="application/json")
        if error.status == 429:
            response.headers["Retry-After"] = "60"
        return response

    @blueprint.before_request
    def authenticate():
        configured = os.environ.get("ASTRA_RESEARCH_TOKEN_SHA256", "")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", configured):
            raise ResearchError(404, "research_disabled")
        if request.method != "GET":
            raise ResearchError(405, "get_only")
        authorization = request.headers.get("Authorization", "")
        match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", authorization)
        if not match:
            raise ResearchError(401, "unauthorized")
        token_bytes = base64.urlsafe_b64decode(match[1] + "=")
        if len(token_bytes) != 32 or base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii") != match[1]:
            raise ResearchError(401, "unauthorized")
        digest = hashlib.sha256(match[1].encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, configured.lower()):
            raise ResearchError(401, "unauthorized")
        store.authorize(digest)

    @blueprint.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Vary"] = "Authorization"
        return response

    def queries(allowed, required=()):
        if (set(request.args) - set(allowed) or set(required) - set(request.args)
                or any(len(request.args.getlist(key)) != 1 for key in request.args)):
            raise ResearchError(400, "invalid_query")

    # Binding common methods lets the blueprint reject them without Flask redirects.
    methods = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "TRACE"]

    @blueprint.route("/snapshot", methods=methods)
    def snapshot():
        queries(())
        try:
            return Response(store.create(), mimetype="application/json")
        except (sqlite3.Error, OSError):
            raise ResearchError(503, "paper_data_unavailable") from None

    @blueprint.route("/history", methods=methods)
    def history():
        queries(("snapshot", "kind", "cursor", "limit"), ("snapshot", "kind"))
        snapshot_id, kind = request.args["snapshot"], request.args["kind"]
        limit_text = request.args.get("limit", "250")
        if (not re.fullmatch(r"[a-f0-9]{32}", snapshot_id) or kind not in ("decisions", "equity")
                or not re.fullmatch(r"[0-9]{1,3}", limit_text) or not 1 <= int(limit_text) <= 500):
            raise ResearchError(400, "invalid_query")
        return Response(store.page(snapshot_id, kind, request.args.get("cursor", ""), int(limit_text)), mimetype="application/json")

    return blueprint
