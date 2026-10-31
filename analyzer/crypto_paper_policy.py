"""Owner-only quality/risk policy for NEW virtual crypto positions.

Public data is acquired before the ledger lock. Planning and virtual booking
then share the re-entrant ledger lock and re-read current state. Existing
positions, gross accounting, the independent exit watcher and other users keep
their existing behavior. No broker or notification calls belong in this module.
"""
import copy
import hashlib
import json
import math
import time
from decimal import Decimal, ROUND_FLOOR

import config
from analyzer import crypto_portfolio, crypto_signals, db_store, okx_client
from analyzer.crypto_runtime import serialized

VERSION = "crypto_offensive_quality_v1"
OWNER_ID = 1
RULES = {
    "owner_user_id": OWNER_ID, "signal_timeframe": "1H", "closed_only": True,
    "max_quote_age_seconds": 60, "max_quote_future_seconds": 5,
    "max_entry_drift_pct": 1.0, "max_entry_risk_pct": 1.5,
    "max_aggregate_risk_pct": 6.0, "max_positions": 4, "max_leverage": 10,
    "max_cash_margin_pct": 30.0, "min_cash_reserve_pct": 15.0,
    "min_margin_eur": 50.0, "fee_pct": .002, "slippage_pct": .0005,
    "net_cash_debit": False, "btc_riskoff_scope": "LONG",
    "existing_positions": "unchanged",
    "signal_timing": "latest_complete_utc_hour", "initial_risk_preserved": True,
    "stop_before_liquidation": True, "positive_model_net_target": True,
}
RULE_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
_COST = RULES["fee_pct"] + RULES["slippage_pct"]
SIGNAL_DATA_VERSION = "fresh_trade_pressure_v1"


def _pressure_evidence(signal, now):
    """Recheck the exact pressure evidence that influenced this paper signal.

    No network under the ledger lock. A delayed/changed valid sample needs a new
    analysis, not a stale score. Unknown pressure keeps the existing no-bonus
    semantics; this is not a new universal volume-entry rule.
    """
    if signal.get("strategy_version") != "volume_confirmed":
        return {}
    sample = signal.get("trade_pressure")
    if not isinstance(sample, dict) or sample.get("version") != SIGNAL_DATA_VERSION:
        raise ValueError("trade_pressure_evidence_missing")
    if type(sample.get("ok")) is not bool:
        raise ValueError("trade_pressure_evidence_invalid")
    current = okx_client.revalidate_trade_pressure(sample, signal["symbol"], now=now)
    metadata = {"signal_data_version": SIGNAL_DATA_VERSION}
    if sample["ok"]:
        same = (current.get("ok") is True and all(current.get(key) == sample.get(key)
                for key in ("buy_ratio", "buy_quote", "sell_quote", "trade_count",
                            "oldest_trade_at", "market_timestamp")))
        if not same:
            raise ValueError("trade_pressure_changed_or_expired")
        metadata.update(pressure_oldest_at=current["oldest_trade_at"],
                        pressure_latest_at=current["market_timestamp"],
                        pressure_trade_count=current["trade_count"],
                        pressure_buy_ratio=current["buy_ratio"])
    elif signal.get("volume_confirmed") or signal.get("buy_pressure_pct") is not None:
        raise ValueError("trade_pressure_unknown_but_used")
    return metadata


def _number(value, name, positive=False):
    if isinstance(value, bool):
        raise ValueError(name)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(name) from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(name)
    return result


def _quote(quote, now, previous=0, signal_end=None):
    if not isinstance(quote, dict):
        raise ValueError("quote_missing")
    price = _number(quote.get("last"), "quote_price", True)
    stamp = _number(quote.get("market_timestamp"), "quote_market_timestamp", True)
    prior = _number(previous, "previous_quote_timestamp")
    if not now - RULES["max_quote_age_seconds"] <= stamp <= now + RULES["max_quote_future_seconds"]:
        raise ValueError("quote_stale_or_future")
    if stamp < prior:
        raise ValueError("quote_time_regression")
    if signal_end is not None and stamp < signal_end:
        raise ValueError("quote_before_signal")
    return price, stamp


def _position_risk(pos):
    if pos.get("direction") not in ("LONG", "SHORT"):
        raise ValueError("existing_direction_invalid")
    entry = _number(pos.get("entry_price"), "existing_entry_invalid", True)
    stop = _number(pos.get("stop_loss"), "existing_stop_missing_or_invalid", True)
    margin = _number(pos.get("margin_eur"), "existing_margin_invalid", True)
    leverage = _number(pos.get("leverage"), "existing_leverage_invalid", True)
    if not 1 <= leverage <= RULES["max_leverage"]:
        raise ValueError("existing_leverage_out_of_bounds")
    sign = 1 if pos["direction"] == "LONG" else -1
    quantity = margin * leverage / entry
    initial_distance = _number(pos.get("initial_sl_dist"), "existing_initial_stop_distance_missing_or_invalid", True)
    initial_stop = entry - sign * initial_distance
    if initial_stop <= 0:
        raise ValueError("existing_initial_stop_invalid")
    initial_risk = quantity * (initial_distance + _COST * (entry + initial_stop))
    current_risk = quantity * (max(0., sign * (entry - stop)) + _COST * (entry + stop))
    risk = max(initial_risk, current_risk)
    if "planned_risk_eur" in pos:
        risk = max(risk, _number(pos["planned_risk_eur"], "existing_planned_risk_invalid", True))
    return risk, entry, margin, leverage, sign


def _marker_status(portfolio):
    if "paper_execution_policy" not in portfolio:
        return "missing"
    marker = portfolio["paper_execution_policy"]
    if not isinstance(marker, dict) or set(marker) != {"version", "started_at", "rule_hash", "rules", "reason"}:
        return "mismatch"
    if marker.get("version") != VERSION or marker.get("rule_hash") != RULE_HASH or marker.get("rules") != RULES:
        return "mismatch"
    if not isinstance(marker.get("reason"), str) or not marker["reason"]:
        return "mismatch"
    try:
        _number(marker.get("started_at"), "policy_started_at_invalid", True)
    except ValueError:
        return "mismatch"
    return "match"


def _state(portfolio, quotes, now):
    if _marker_status(portfolio) == "mismatch":
        raise ValueError("policy_marker_mismatch")
    cash = _number(portfolio.get("cash"), "cash_invalid")
    start = _number(portfolio.get("start_capital"), "start_capital_invalid", True)
    if cash < 0:
        raise ValueError("cash_negative")
    positions = portfolio.get("positions")
    if not isinstance(positions, list):
        raise ValueError("positions_invalid")
    times = portfolio.get("paper_policy_quote_times", {})
    if not isinstance(times, dict):
        raise ValueError("quote_times_invalid")
    equity, risk, seen = cash, 0., set()
    for pos in positions:
        symbol = pos.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in seen:
            raise ValueError("existing_symbol_invalid_or_duplicate")
        seen.add(symbol)
        planned, entry, margin, leverage, sign = _position_risk(pos)
        price, _ = _quote(quotes.get(symbol), now, times.get(symbol, 0))
        # Match the gross isolated-margin ledger, without introducing cost debits.
        pnl = max(-margin, margin * leverage * sign * (price / entry - 1))
        equity += margin + pnl
        risk += planned
    if equity <= 0:
        raise ValueError("equity_nonpositive")
    if portfolio.get("trading_halted"):
        raise ValueError("trading_halted")
    # Do not wait for a stale stored total_value or relax either existing breaker.
    drawdown = 100 * (start - equity) / start
    if drawdown >= min(config.CRYPTO_MAX_DRAWDOWN_PCT, config.CRYPTO_CRITICAL_DRAWDOWN_PCT):
        raise ValueError("drawdown_blocks_new_entries")
    return cash, start, equity, risk, seen, times


def plan_entry(portfolio, signal, quotes, now=None):
    """Pure plan. Returns an explicit block, never creates/mutates an account."""
    now = time.time() if now is None else now
    try:
        now = _number(now, "planning_time_invalid", True)
        cash, start, equity, aggregate, held, times = _state(portfolio, quotes, now)
        symbol = signal.get("symbol")
        direction = signal.get("direction")
        if not isinstance(symbol, str) or not symbol or direction not in ("LONG", "SHORT"):
            raise ValueError("signal_symbol_or_direction_invalid")
        pressure_metadata = _pressure_evidence(signal, now)
        if symbol in held:
            raise ValueError("already_open")
        if len(held) >= min(RULES["max_positions"], config.CRYPTO_MAX_POSITIONS):
            raise ValueError("position_limit")
        signal_price = _number(signal.get("price"), "signal_price_invalid", True)
        signal_end = _number(signal.get("signal_bar_end"), "signal_bar_end_missing", True)
        if signal_end != int(now // 3600) * 3600:
            raise ValueError("signal_bar_not_latest_closed_hour")
        price, quote_at = _quote(quotes.get(symbol), now, times.get(symbol, 0), signal_end)
        if abs(price / signal_price - 1) > RULES["max_entry_drift_pct"] / 100 + 1e-12:
            raise ValueError("entry_drift")
        if config.CRYPTO_BTC_RISKOFF_FILTER_ENABLED and direction == "LONG":
            _quote(quotes.get("BTC"), now, times.get("BTC", 0))
            change = _number(quotes["BTC"].get("change_pct"), "btc_change_missing")
            if change < config.CRYPTO_BTC_RISKOFF_THRESHOLD_PCT:
                raise ValueError("btc_long_riskoff")
        sign = 1 if direction == "LONG" else -1
        sig_stop = _number(signal.get("stop_loss"), "signal_stop_invalid", True)
        sig_target = _number(signal.get("take_profit"), "signal_target_invalid", True)
        distance = sign * (signal_price - sig_stop)
        target_distance = sign * (sig_target - signal_price)
        if distance <= 0 or target_distance <= 0:
            raise ValueError("signal_barriers_wrong_side")
        stop, target = price - sign * distance, price + sign * target_distance
        if stop <= 0 or target <= 0:
            raise ValueError("shifted_barriers_invalid")
        requested = _number(signal.get("leverage"), "signal_leverage_invalid", True)
        if requested != int(requested) or requested < 1:
            raise ValueError("signal_leverage_invalid")
        leverage = min(int(requested), RULES["max_leverage"], config.CRYPTO_MAX_LEVERAGE)
        liquidation = crypto_portfolio._liquidation_price(price, leverage, direction, config.CRYPTO_LIQUIDATION_BUFFER_PCT)
        if sign * (stop - liquidation) <= 0:
            raise ValueError("stop_not_before_liquidation")
        if target_distance - _COST * (price + target) <= 0:
            raise ValueError("target_not_positive_after_model_costs")
        # Fee+slippage is the SAME additive per-notional cost model as the audit.
        risk_per_margin = leverage / price * (distance + _COST * (price + stop))
        risk_budget = min(equity * .015, max(0., equity * .06 - aggregate))
        reserve = start * max(.15, config.CRYPTO_CASH_RESERVE_PCT)
        cap = min(cash * min(.30, config.CRYPTO_MAX_POSITION_PCT), cash - reserve, risk_budget / risk_per_margin)
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("risk_or_cash_budget")
        margin = float(Decimal(str(cap)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))
        if margin < max(RULES["min_margin_eur"], config.CRYPTO_MIN_POSITION_EUR):
            raise ValueError("minimum_margin_or_risk_budget")
        planned = margin * risk_per_margin
        if planned > risk_budget + 1e-9 or aggregate + planned > equity * .06 + 1e-9 or cash - margin < reserve - 1e-9:
            raise ValueError("risk_recheck_failed")
        notional = margin * leverage
        metadata = {
            "paper_policy_version": VERSION, "paper_policy_rule_hash": RULE_HASH,
            "planned_risk_eur": planned,
            "estimated_entry_cost_eur": notional * _COST,
            "estimated_exit_cost_eur": notional * stop / price * _COST,
            "signal_bar_end": signal_end, "entry_quote_at": quote_at,
            "signal_price": signal_price,
            "signal_strategy_version": str(signal.get("strategy_version") or "classic"),
            "risk_equity_eur": equity,
            **pressure_metadata,
        }
        return {"ok": True, "symbol": symbol, "direction": direction,
                "entry_price": price, "stop_loss": stop, "take_profit": target,
                "margin_eur": margin, "leverage": leverage, "paper_metadata": metadata,
                "aggregate_risk_after_eur": aggregate + planned}
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "reason": str(exc)}


def _marker(now):
    return {"version": VERSION, "started_at": now, "rule_hash": RULE_HASH,
            "rules": copy.deepcopy(RULES),
            "reason": "Neue virtuelle Entries: abgeschlossene Kerzen, frische Kurse und feste Stoprisikogrenzen; Bruttohistorie bleibt erhalten."}


@serialized
def initialize(user_id):
    if user_id != OWNER_ID:
        return {"ok": False, "reason": "outside_owner_scope"}
    portfolio = db_store.load_crypto_portfolio(user_id)
    if not portfolio:
        return {"ok": False, "reason": "portfolio_missing"}
    status = _marker_status(portfolio)
    if status == "mismatch":
        return {"ok": False, "reason": "policy_marker_mismatch"}
    if status == "match":
        return {"ok": True, "changed": False}
    portfolio["paper_execution_policy"] = _marker(time.time())
    db_store.save_crypto_portfolio(user_id, portfolio, backup=False)
    return {"ok": True, "changed": True}


@serialized
def _execute(user_id, recs, quotes, dry_run=False):
    if user_id != OWNER_ID:
        raise ValueError("outside_owner_scope")
    p = db_store.load_crypto_portfolio(user_id)
    actions, blocked = [], []
    if not p:
        return {"actions": [], "recommendations": recs, "portfolio": {},
                "policy_version": VERSION, "policy_blocks": ["portfolio_missing"]}
    p = copy.deepcopy(p)
    status = _marker_status(p)
    if status == "mismatch":
        return {"actions": [], "recommendations": recs, "portfolio": p,
                "policy_version": VERSION, "policy_blocks": [{"symbol": "MARKET", "reason": "policy_marker_mismatch"}]}
    if not dry_run and status == "missing":
        p["paper_execution_policy"] = _marker(time.time())
        db_store.save_crypto_portfolio(user_id, p, backup=False)
    for sig in recs.get("suggestions", []):
        # Prior fills and any concurrent watcher updates must be reflected here.
        if not dry_run:
            p = db_store.load_crypto_portfolio(user_id)
        plan = plan_entry(p, sig, quotes, now=time.time())
        if not plan["ok"]:
            blocked.append({"symbol": sig.get("symbol"), "reason": plan["reason"]})
            continue
        if dry_run:
            p["cash"] -= plan["margin_eur"]
            p.setdefault("positions", []).append({**{key: plan[key] for key in
                ("symbol", "direction", "entry_price", "stop_loss", "take_profit", "margin_eur", "leverage")},
                "initial_sl_dist": abs(plan["entry_price"] - plan["stop_loss"]), **plan["paper_metadata"]})
            p.setdefault("paper_policy_quote_times", {})[plan["symbol"]] = plan["paper_metadata"]["entry_quote_at"]
        else:
            fields = {key: plan[key] for key in ("symbol", "direction", "margin_eur", "leverage", "entry_price", "stop_loss", "take_profit", "paper_metadata")}
            result = crypto_portfolio.open_position(user_id, **fields,
                reason=f"Score {sig.get('score')}/100; {VERSION}; initiales Modellrisiko {plan['paper_metadata']['planned_risk_eur']:.2f} EUR")
            if not result.get("ok"):
                blocked.append({"symbol": plan["symbol"], "reason": result.get("error", "booking_failed")})
                continue
        actions.append({"symbol": plan["symbol"],
            "action": ("WOULD-OPEN-" if dry_run else "AUTO-OPEN-") + plan["direction"],
            "leverage": plan["leverage"], "score": sig.get("score"), "margin_eur": plan["margin_eur"],
            "planned_risk_eur": plan["paper_metadata"]["planned_risk_eur"], "policy_version": VERSION})
    # Dry runs return the real, untouched portfolio, not simulated booked state.
    current = db_store.load_crypto_portfolio(user_id)
    return {"actions": actions, "recommendations": recs, "portfolio": current,
            "halted": bool(current and current.get("trading_halted")),
            "policy_version": VERSION, "policy_blocks": blocked}


def run(user_id, dry_run=False):
    if user_id != OWNER_ID:
        raise ValueError("outside_owner_scope")
    snapshot = db_store.load_crypto_portfolio(user_id)
    if not snapshot:
        return {"actions": [], "recommendations": {"count": 0, "suggestions": []},
                "portfolio": {}, "policy_version": VERSION, "policy_blocks": ["portfolio_missing"]}
    strategy = db_store.get_settings(user_id).get("crypto_strategy_version", "classic")
    recs = crypto_signals.generate_crypto_recommendations(strategy_version=strategy, closed_only=True)
    symbols = {p.get("symbol") for p in snapshot.get("positions", [])}
    symbols.update(s.get("symbol") for s in recs.get("suggestions", []))
    symbols.add("BTC")
    quotes = {}
    for symbol in sorted(s for s in symbols if isinstance(s, str) and s):
        try:
            quotes[symbol] = okx_client.fetch_ticker(symbol)
        except Exception:
            quotes[symbol] = None
    # No public data acquisition occurs in the serialized commit path.
    return _execute(user_id, recs, quotes, dry_run=dry_run)
