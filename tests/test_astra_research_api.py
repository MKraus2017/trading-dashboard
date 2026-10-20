"""Offline security/consistency tests; no production DB, app import or network."""
import ast
import base64
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify, session

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("research_api_test", ROOT / "analyzer/astra_research_api.py")
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)
# Public fixture values, NOT production credentials; no secrets are generated.
TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()
VERSION = "astra_breakout_2h_volume_v3"
OLD = "astra_breakout_2h_v2"
PREFIX = api.PREFIX


class ResearchApiTests(unittest.TestCase):
    def setUp(self):
        # Keep generated/cleaned fixtures inside this verified repository, not
        # a Windows sandbox virtual TEMP directory with incompatible ACLs.
        self.temp = tempfile.TemporaryDirectory(prefix=".astra-research-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "paper.db"
        self.now = 1800000000.0
        self.mono = 1000.0
        self.env = patch.dict(os.environ, {"ASTRA_RESEARCH_TOKEN_SHA256": DIGEST})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run = dict(version=VERSION, started_at=1000, initial_capital=1000, cash=990,
                        equity=1001, rules={"fee_pct": .002, "symbols": ["BTC"], "token": "NESTED_SECRET"},
                        trades=[dict(id="t1", symbol="BTC", pnl_eur=1, entry_fee=2,
                                     private_key="TRADE_SECRET", signal={"enter": True, "secret": "SIGNAL_SECRET"})],
                        positions=[], prior_versions=[dict(version=OLD, equity=990, state_sha256="oldhash")],
                        pending_bar={"signals": {"BTC": {"enter": False, "secret": "PENDING_SECRET"},
                                                 "other_account": {"enter": True}}, "unknown": "PENDING_SECRET"},
                        telegram_bot_token="TOP_SECRET")
        self.reference = dict(cash=800, start_capital=1000, positions=[dict(id="r1", symbol="ADA", margin_eur=200)],
                              trades=[dict(id="rclosed", symbol="LINK", pnl_eur=-20)],
                              value_history=[dict(date="2026-10-13T00:00:00+02:00", value=1000)],
                              real_trades=[{"account_number": "REAL_SECRET"}], api_key="REF_SECRET")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE astra_ultra_runs(user_id INTEGER,version TEXT,data TEXT,PRIMARY KEY(user_id,version));
                CREATE TABLE astra_ultra_decisions(user_id INTEGER,version TEXT,bar_end REAL,symbol TEXT,data TEXT,
                                                 PRIMARY KEY(user_id,version,bar_end,symbol));
                CREATE TABLE astra_ultra_equity(user_id INTEGER,version TEXT,observed_at REAL,data TEXT,
                                              PRIMARY KEY(user_id,version,observed_at));
                CREATE TABLE crypto_portfolios(user_id INTEGER PRIMARY KEY,data TEXT);
                CREATE TABLE settings(user_id INTEGER,password TEXT);
                CREATE TABLE real_portfolios(user_id INTEGER,data TEXT);
                INSERT INTO settings VALUES(1,'SETTINGS_SECRET');
                INSERT INTO real_portfolios VALUES(1,'REAL_DATABASE_SECRET');
            """)
            connection.execute("INSERT INTO astra_ultra_runs VALUES(1,?,?)", (VERSION, json.dumps(self.run)))
            connection.execute("INSERT INTO astra_ultra_runs VALUES(1,?,?)", (OLD, json.dumps({"version": OLD})))
            connection.execute("INSERT INTO astra_ultra_runs VALUES(2,?,?)", (VERSION, '{"secret":"USER2_SECRET"}'))
            connection.execute("INSERT INTO crypto_portfolios VALUES(1,?)", (json.dumps(self.reference),))
            connection.execute("INSERT INTO crypto_portfolios VALUES(2,?)", ('{"secret":"USER2_SECRET"}',))
            for index in range(6):
                version = OLD if index < 2 else VERSION
                connection.execute("INSERT INTO astra_ultra_decisions VALUES(1,?,?,?,?)",
                                   (version, 2000 + index, "BTC", json.dumps({"action": "wait", "final": False,
                                     "secret": "DECISION_SECRET", "attempt_history": [{"attempt": 1, "secret": "ATTEMPT_SECRET"}]})))
                connection.execute("INSERT INTO astra_ultra_equity VALUES(1,?,?,?)",
                                   (version, 2000 + index, json.dumps({"equity": 1000 + index, "secret": "EQUITY_SECRET"})))
            connection.execute("INSERT INTO astra_ultra_decisions VALUES(2,?,1,'BTC',?)",
                               (VERSION, '{"action":"USER2_SECRET"}'))
            connection.execute("INSERT INTO astra_ultra_equity VALUES(2,?,1,?)", (VERSION, '{"equity": 999999}'))
        self.app = self.make_app()
        self.client = self.app.test_client()

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "offline-test-only"
        app.testing = True
        app.register_blueprint(api.create_blueprint(self.path, clock=lambda: self.now, monotonic=lambda: self.mono))

        @app.get("/normal")
        def normal():
            return (jsonify(ok=True), 200) if session.get("user_id") else (jsonify(ok=False), 401)
        return app

    def get(self, suffix, token=TOKEN, client=None):
        return (client or self.client).get(PREFIX + suffix, headers={"Authorization": "Bearer " + token})

    def snapshot(self):
        response = self.get("/snapshot")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def page(self, snapshot, kind="decisions", limit=2, cursor="", client=None):
        return self.get("/history?snapshot=" + snapshot["snapshot_id"] + "&kind=" + kind +
                        "&limit=" + str(limit) + ("&cursor=" + cursor if cursor else ""), client=client)

    def test_auth_missing_wrong_and_session_not_substitute(self):
        self.assertEqual(self.client.get(PREFIX + "/snapshot").status_code, 401)
        self.assertEqual(self.get("/snapshot", token="A" * 43).status_code, 401)
        with self.client.session_transaction() as state:
            state["user_id"] = 1
        self.assertEqual(self.client.get(PREFIX + "/snapshot").status_code, 401)
        unauthenticated = self.app.test_client()
        self.assertEqual(unauthenticated.get("/normal", headers={"Authorization": "Bearer " + TOKEN}).status_code, 401)
        self.assertEqual(self.get("/snapshot", token=TOKEN + "x").status_code, 401)
        self.assertEqual(self.client.get(PREFIX + "/snapshot?token=" + TOKEN).status_code, 401)

    def test_disabled_and_malformed_config_fail_closed(self):
        for value in ("", "short", "g" * 64, " " + DIGEST):
            with patch.dict(os.environ, {"ASTRA_RESEARCH_TOKEN_SHA256": value}):
                self.assertEqual(self.get("/snapshot").status_code, 404)

    def test_noncanonical_token_rejected_even_if_its_digest_configured(self):
        token = "B" * 43
        with patch.dict(os.environ, {"ASTRA_RESEARCH_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest()}):
            self.assertEqual(self.get("/snapshot", token=token).status_code, 401)

    def test_only_get_no_redirect_and_security_headers(self):
        for method in ("post", "put", "patch", "delete", "head", "options", "trace"):
            response = self.client.open(PREFIX + "/snapshot", method=method.upper(),
                                        headers={"Authorization": "Bearer " + TOKEN})
            self.assertEqual(response.status_code, 405, method)
            self.assertNotIn("Location", response.headers)
        response = self.get("/snapshot")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_owner_scope_nested_allowlists_and_complete_metadata(self):
        snapshot = self.snapshot()
        body = json.dumps(snapshot)
        self.assertNotIn("SECRET", body)
        self.assertNotIn("user_id", body)
        self.assertEqual(snapshot["counts"], {"decisions": 6, "equity": 6})
        self.assertEqual(snapshot["astra"]["trades"][0]["entry_fee"], 2)
        self.assertEqual(snapshot["reference"]["trades"][0]["pnl_eur"], -20)
        self.assertEqual(snapshot["mode"], "paper_only")
        self.assertEqual(snapshot["expires_at"] - snapshot["captured_at"], 600)
        for kind in ("decisions", "equity"):
            response = self.page(snapshot, kind, limit=500)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("SECRET", response.get_data(as_text=True))
            self.assertNotIn("user_id", response.get_data(as_text=True))

    def test_database_bytes_and_counts_unchanged(self):
        before = self.path.read_bytes()
        snapshot = self.snapshot()
        self.page(snapshot, "equity")
        self.page(snapshot)
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(Path(str(self.path) + "-wal").exists())

    def test_invalid_queries_never_select_other_users_tables_or_paths(self):
        for query in ("?uid=2", "?user_id=2", "?path=other.db", "?table=settings", "?limit=500"):
            self.assertEqual(self.get("/snapshot" + query).status_code, 400)
        snapshot = self.snapshot()
        prefix = "/history?snapshot=" + snapshot["snapshot_id"]
        for tail in ("", "&kind=settings", "&kind=equity&limit=501", "&kind=equity&limit=0",
                     "&kind=equity&limit=-1", "&kind=equity&uid=2", "&kind=equity&kind=decisions",
                     "&kind=equity&limit=1&limit=2", "&kind=equity&cursor=bad"):
            self.assertEqual(self.get(prefix + tail).status_code, 400, tail)

    def test_paging_exactly_once_and_freezes_retry_and_new_rows(self):
        snapshot = self.snapshot()
        first = self.page(snapshot).get_json()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE astra_ultra_decisions SET data=? WHERE user_id=1", ('{"action":"changed"}',))
            connection.execute("INSERT INTO astra_ultra_decisions VALUES(1,?,9999,'BTC',?)", (VERSION, '{"action":"new"}'))
            connection.execute("UPDATE crypto_portfolios SET data=? WHERE user_id=1", ('{"cash":0}',))
        rows = first["rows"]
        page = first
        while page["next_cursor"]:
            page = self.page(snapshot, cursor=page["next_cursor"]).get_json()
            self.assertEqual(page["captured_at"], snapshot["captured_at"])
            self.assertEqual(page["total"], 6)
            rows.extend(page["rows"])
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({(row["bar_end"], row["version"], row["symbol"]) for row in rows}), 6)
        self.assertTrue(all(row["action"] == "wait" for row in rows))
        self.assertEqual(snapshot["reference"]["cash"], 800)

    def test_cursor_tamper_kind_and_snapshot_binding(self):
        snapshot = self.snapshot()
        cursor = self.page(snapshot).get_json()["next_cursor"]
        bad = cursor[:5] + ("A" if cursor[5] != "A" else "B") + cursor[6:]
        self.assertEqual(self.page(snapshot, cursor=bad).status_code, 400)
        self.assertEqual(self.page(snapshot, kind="equity", cursor=cursor).status_code, 400)
        self.mono += 31
        another = self.snapshot()
        self.assertEqual(self.page(another, cursor=cursor).status_code, 400)

    def test_expiry_restart_and_maximum_two_snapshots(self):
        first = self.snapshot()
        self.assertEqual(self.page(first, client=self.make_app().test_client()).status_code, 410)
        self.mono += 31
        second = self.snapshot()
        self.mono += 31
        third = self.snapshot()
        self.assertEqual(self.page(first).status_code, 410)
        self.assertEqual(self.page(second).status_code, 200)
        self.assertEqual(self.page(third).status_code, 200)
        self.mono += 600
        self.assertEqual(self.page(third).status_code, 410)

    def test_rates_bound_expensive_captures_and_requests(self):
        snapshot = self.snapshot()
        self.assertEqual(self.get("/snapshot").status_code, 429)
        with patch.object(api, "REQUESTS_PER_MINUTE", 3):
            self.assertEqual(self.page(snapshot).status_code, 200)
            self.assertEqual(self.page(snapshot).status_code, 429)
        self.mono += 61
        self.assertEqual(self.page(snapshot).status_code, 200)

    def test_token_rotation_revokes_old_token_and_snapshots(self):
        snapshot = self.snapshot()
        replacement = base64.urlsafe_b64encode(bytes(reversed(range(32)))).rstrip(b"=").decode()
        with patch.dict(os.environ, {"ASTRA_RESEARCH_TOKEN_SHA256": hashlib.sha256(replacement.encode()).hexdigest()}):
            self.assertEqual(self.page(snapshot).status_code, 401)
            response = self.get("/history?snapshot=" + snapshot["snapshot_id"] + "&kind=equity", token=replacement)
            self.assertEqual(response.status_code, 410)

    def test_size_rows_and_invalid_data_fail_explicitly_not_truncated(self):
        for constant, value in (("MAX_DOCUMENT_BYTES", 10), ("MAX_ROW_BYTES", 10),
                                ("MAX_HISTORY_ROWS", 5), ("MAX_SNAPSHOT_BYTES", 1000)):
            with patch.object(api, constant, value):
                self.assertEqual(self.get("/snapshot").status_code, 413, constant)
            self.mono += 31
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE crypto_portfolios SET data='not-json' WHERE user_id=1")
        self.assertEqual(self.get("/snapshot").status_code, 503)

    def test_missing_database_does_not_create_it_or_disclose_path(self):
        missing = Path(self.temp.name) / "missing-secret.db"
        app = Flask("missing")
        app.register_blueprint(api.create_blueprint(missing))
        response = app.test_client().get(PREFIX + "/snapshot", headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertFalse(missing.exists())

    def test_single_sqlite_transaction_consistency_during_concurrent_write(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        original = api._read_document
        changed = []

        def interleaved(connection, table, version=None):
            result = original(connection, table, version)
            if table == "astra_ultra_runs" and not changed:
                with closing(sqlite3.connect(self.path)) as writer, writer:
                    writer.execute("UPDATE crypto_portfolios SET data=? WHERE user_id=1", ('{"cash":77}',))
                    writer.execute("UPDATE astra_ultra_decisions SET data=? WHERE user_id=1", ('{"action":"new"}',))
                changed.append(True)
            return result

        with patch.object(api, "_read_document", side_effect=interleaved):
            snapshot = self.snapshot()
        self.assertEqual(snapshot["reference"]["cash"], 800)
        self.assertTrue(all(row["action"] == "wait" for row in self.page(snapshot, limit=500).get_json()["rows"]))

    def test_no_runtime_imports_or_external_network(self):
        source = (ROOT / "analyzer/astra_research_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse(any(name.startswith(("analyzer", "config", "requests", "urllib")) for name in imports))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            snapshot = self.snapshot()
            self.assertEqual(self.page(snapshot).status_code, 200)

    def test_default_page_limit_and_no_hidden_100_row_truncation(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for index in range(6, 507):
                connection.execute("INSERT INTO astra_ultra_equity VALUES(1,?,?,?)",
                                   (VERSION, 2000 + index, '{"equity":1000}'))
        snapshot = self.snapshot()
        response = self.get("/history?snapshot=" + snapshot["snapshot_id"] + "&kind=equity")
        self.assertEqual(len(response.get_json()["rows"]), 250)
        page = response.get_json()
        total = len(page["rows"])
        while page["next_cursor"]:
            page = self.page(snapshot, kind="equity", limit=250, cursor=page["next_cursor"]).get_json()
            total += len(page["rows"])
        self.assertEqual(total, 507)
        self.assertEqual(snapshot["counts"]["equity"], 507)

    def test_full_trade_arrays_are_not_sliced_at_100(self):
        self.run["trades"] = [dict(id="t" + str(i), symbol="BTC", pnl_eur=i) for i in range(155)]
        self.reference["trades"] = [dict(id="r" + str(i), symbol="ADA", pnl_eur=i) for i in range(377)]
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE astra_ultra_runs SET data=? WHERE user_id=1 AND version=?",
                               (json.dumps(self.run), VERSION))
            connection.execute("UPDATE crypto_portfolios SET data=? WHERE user_id=1", (json.dumps(self.reference),))
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot["astra"]["trades"]), 155)
        self.assertEqual(len(snapshot["reference"]["trades"]), 377)

    def test_readonly_uri_and_sql_authorizer_deny_unrelated_reads_and_writes(self):
        original = sqlite3.connect
        tables = set()
        allowed = {"astra_ultra_runs", "astra_ultra_decisions", "astra_ultra_equity", "crypto_portfolios"}

        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_READ:
                tables.add(arg1)
                return sqlite3.SQLITE_OK if arg1 in allowed else sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                          sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ATTACH):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def connect(path, *args, **kwargs):
            self.assertIn("?mode=ro", str(path))
            self.assertTrue(kwargs["uri"])
            connection = original(path, *args, **kwargs)
            connection.set_authorizer(authorize)
            return connection

        with patch.object(api.sqlite3, "connect", side_effect=connect):
            self.snapshot()
        self.assertEqual(tables, allowed)

    def test_large_documents_are_rejected_before_decode(self):
        with patch.object(api, "MAX_DOCUMENT_BYTES", 1), patch.object(api, "_decode") as decode:
            self.assertEqual(self.get("/snapshot").status_code, 413)
            decode.assert_not_called()

    def test_nonfinite_and_nested_scalar_objects_are_rejected_safely(self):
        for document in ('{"cash": NaN}', '{"cash":{"token":"SECRET"}}'):
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("UPDATE crypto_portfolios SET data=? WHERE user_id=1", (document,))
            response = self.get("/snapshot")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("SECRET", response.get_data(as_text=True))
            self.mono += 31

    def test_keyset_ties_across_versions_and_symbols(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO astra_ultra_decisions VALUES(1,?,2000,'BTC',?)", (VERSION, '{"action":"tie"}'))
            connection.execute("INSERT INTO astra_ultra_decisions VALUES(1,?,2000,'ETH',?)", (OLD, '{"action":"tie"}'))
        snapshot = self.snapshot()
        page = self.page(snapshot, limit=1).get_json()
        rows = page["rows"]
        while page["next_cursor"]:
            page = self.page(snapshot, limit=1, cursor=page["next_cursor"]).get_json()
            rows.extend(page["rows"])
        identities = [(row["bar_end"], row["version"], row["symbol"]) for row in rows]
        self.assertEqual(len(identities), 8)
        self.assertEqual(identities, sorted(set(identities)))


if __name__ == "__main__":
    unittest.main()
