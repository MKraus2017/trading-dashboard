"""Isolated policy tests: temporary SQLite ledger, blocked network, no app boot."""
import ast
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "isolated.db"
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TABLE ledger(uid INTEGER PRIMARY KEY, data TEXT)")
        self.now = 1793354407.
        self.original = dict(cash=1000., start_capital=1000., positions=[],
                             trades=[{"id": "historic", "pnl_eur": 0.}],
                             value_history=[{"date": "2026-10-01", "value": 1000.}])
        fake = types.ModuleType("analyzer")
        fake.__path__ = []
        config = types.ModuleType("config")
        # Read only literal CRYPTO_* constants, never import app/environment config.
        tree = ast.parse((ROOT / "config.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith("CRYPTO_"):
                setattr(config, node.targets[0].id, ast.literal_eval(node.value))
        self.db = types.ModuleType("analyzer.db_store")
        self.db.DB_PATH = self.path
        self.db.get_settings = lambda uid: {"crypto_strategy_version": "volume_confirmed"}
        self.db._default_crypto_portfolio = lambda: copy.deepcopy(self.original)
        def read(uid):
            with closing(sqlite3.connect(self.path)) as conn:
                row = conn.execute("SELECT data FROM ledger WHERE uid=?", (uid,)).fetchone()
                return json.loads(row[0]) if row else None
        def save(uid, p, **kwargs):
            with closing(sqlite3.connect(self.path)) as conn, conn:
                conn.execute("INSERT OR REPLACE INTO ledger VALUES(?,?)", (uid, json.dumps(p, allow_nan=False)))
        self.db.load_crypto_portfolio = read
        self.db.save_crypto_portfolio = Mock(side_effect=save)
        self.db.save_crypto_portfolio(1, self.original)
        self.db.save_crypto_portfolio.reset_mock()
        self.market = types.ModuleType("analyzer.okx_client")
        self.pressure_client = load("policy_pressure_client", "analyzer/okx_client.py")
        self.market.revalidate_trade_pressure = self.pressure_client.revalidate_trade_pressure
        self.signals = types.ModuleType("analyzer.crypto_signals")
        self.signal = dict(symbol="ETH", direction="LONG", score=75, leverage=5,
                           price=100., stop_loss=98., take_profit=102.8,
                           signal_bar_end=self.now - 7, strategy_version="volume_confirmed")
        self.signal["trade_pressure"] = dict(version="fresh_trade_pressure_v1", ok=False,
                                             reason="missing", trades=[])
        self.quotes = {s: {"last": 100., "market_timestamp": self.now, "change_pct": 0.} for s in ("BTC", "ETH", "SOL", "ADA", "XRP")}
        self.signals.generate_crypto_recommendations = Mock(side_effect=lambda **kwargs: {"count": 1, "suggestions": [copy.deepcopy(self.signal)]})
        fake.db_store, fake.okx_client, fake.crypto_signals = self.db, self.market, self.signals
        modules = {"analyzer": fake, "config": config, "analyzer.db_store": self.db,
                   "analyzer.okx_client": self.market, "analyzer.crypto_signals": self.signals}
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.runtime = load("analyzer.crypto_runtime", "analyzer/crypto_runtime.py")
        fake.crypto_runtime = self.runtime
        self.portfolio = load("analyzer.crypto_portfolio", "analyzer/crypto_portfolio.py")
        fake.crypto_portfolio = self.portfolio
        self.policy = load("analyzer.crypto_paper_policy", "analyzer/crypto_paper_policy.py")
        fake.crypto_paper_policy = self.policy
        self.network = patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.clock = patch.object(self.policy.time, "time", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        def quote(symbol):
            self.assertFalse(getattr(self.runtime._held, "paths", set()), "market request while ledger lock held")
            return copy.deepcopy(self.quotes.get(symbol))
        self.market.fetch_ticker = Mock(side_effect=quote)

    def read(self):
        return self.db.load_crypto_portfolio(1)

    def plan(self, p=None, signal=None, quotes=None):
        return self.policy.plan_entry(p or self.read(), signal or self.signal,
                                      self.quotes if quotes is None else quotes, now=self.now)

    def pressure_signal(self):
        rows = [dict(instId="ETH-USDT", tradeId="1", side="buy", px="100", sz="2", ts=str(int((self.now-5)*1000))),
                dict(instId="ETH-USDT", tradeId="2", side="sell", px="100", sz="1", ts=str(int((self.now-10)*1000)))]
        with patch.object(self.pressure_client, "_http_get", return_value={"code": "0", "data": rows}):
            pressure = self.pressure_client.fetch_recent_trade_pressure("ETH", strict=True)
        self.assertTrue(pressure["ok"])
        return dict(self.signal, trade_pressure=pressure, buy_pressure_pct=66.7, volume_confirmed=True)

    def test_pressure_rechecked_at_entry_without_network_and_compact_evidence_saved(self):
        self.signal = self.pressure_signal()
        result = self.policy._execute(1, {"suggestions": [self.signal]}, self.quotes)
        self.assertEqual(len(result["actions"]), 1)
        p = self.read()
        pos = p["positions"][0]
        self.assertEqual(pos["signal_data_version"], "fresh_trade_pressure_v1")
        self.assertEqual(pos["pressure_trade_count"], 2)
        self.assertAlmostEqual(pos["pressure_buy_ratio"], 2/3)
        self.assertNotIn("trade_pressure", pos)
        self.assertEqual(p["trades"], self.original["trades"])
        self.assertEqual(p["value_history"], self.original["value_history"])

    def test_expired_changed_missing_and_conflicting_pressure_never_book(self):
        fresh = self.pressure_signal()
        expired = copy.deepcopy(fresh)
        for trade in expired["trade_pressure"]["trades"]:
            trade["timestamp"] -= 100
        changed = copy.deepcopy(fresh)
        changed["trade_pressure"]["buy_ratio"] = .99
        missing = copy.deepcopy(fresh); missing.pop("trade_pressure")
        unknown_used = dict(self.signal, volume_confirmed=True)
        before = self.read()
        for sig in (expired, changed, missing, unknown_used):
            result = self.policy._execute(1, {"suggestions": [sig]}, self.quotes, dry_run=True)
            self.assertEqual(result["actions"], [])
            self.assertTrue(result["policy_blocks"])
            self.assertEqual(self.read(), before)

    def test_unknown_volume_keeps_no_bonus_semantics_and_classic_needs_no_pressure(self):
        self.assertTrue(self.plan()["ok"])
        classic = dict(self.signal, strategy_version="classic")
        classic.pop("trade_pressure")
        planned = self.plan(signal=classic)
        self.assertTrue(planned["ok"])
        self.assertNotIn("signal_data_version", planned["paper_metadata"])

    def test_initialization_preserves_ledger_and_missing_or_other_account(self):
        self.assertFalse(self.policy.initialize(2)["ok"])
        result = self.policy.initialize(1)
        self.assertTrue(result["changed"])
        p = self.read()
        marker = p.pop("paper_execution_policy")
        self.assertEqual(p, self.original)
        self.assertEqual(marker["rule_hash"], self.policy.RULE_HASH)
        self.assertFalse(self.policy.initialize(1)["changed"])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DELETE FROM ledger WHERE uid=1")
        self.assertEqual(self.policy.initialize(1)["reason"], "portfolio_missing")
        self.assertIsNone(self.read())

    def test_risk_floor_costs_and_shifted_fill_barriers(self):
        self.quotes["ETH"]["last"] = 100.5
        plan = self.plan()
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["entry_price"], 100.5)
        self.assertEqual(plan["stop_loss"], 98.5)
        self.assertAlmostEqual(plan["take_profit"], 103.3)
        meta = plan["paper_metadata"]
        self.assertLessEqual(meta["planned_risk_eur"], 15.)
        self.assertGreater(meta["planned_risk_eur"], 14.99)
        self.assertAlmostEqual(plan["margin_eur"] * 100, round(plan["margin_eur"] * 100))
        self.assertAlmostEqual(meta["estimated_entry_cost_eur"], plan["margin_eur"] * 5 * .0025)
        self.assertAlmostEqual(meta["estimated_exit_cost_eur"], plan["margin_eur"] * 5 * 98.5 / 100.5 * .0025)

    def test_short_costs_direction_and_riskoff_only_long(self):
        self.quotes["BTC"]["change_pct"] = -10
        self.assertEqual(self.plan()["reason"], "btc_long_riskoff")
        self.signal.update(direction="SHORT", stop_loss=102., take_profit=97.2)
        plan = self.plan()
        self.assertTrue(plan["ok"], plan)
        meta = plan["paper_metadata"]
        self.assertGreater(meta["estimated_exit_cost_eur"], meta["estimated_entry_cost_eur"])
        self.assertLessEqual(meta["planned_risk_eur"], 15.)

    def test_invalid_stale_future_regressed_before_signal_and_drift_block(self):
        for field, value, reason in [("market_timestamp", self.now-61, "quote_stale_or_future"),
                                      ("market_timestamp", self.now+6, "quote_stale_or_future"),
                                      ("market_timestamp", self.now-8, "quote_before_signal"),
                                      ("market_timestamp", None, "quote_market_timestamp"),
                                      ("last", float("nan"), "quote_price"),
                                      ("last", 101.1, "entry_drift")]:
            with self.subTest(field=field, value=value):
                q = copy.deepcopy(self.quotes)
                q["ETH"][field] = value
                self.assertEqual(self.plan(quotes=q)["reason"], reason)
        p = self.read()
        p["paper_policy_quote_times"] = {"ETH": self.now+1}
        self.assertEqual(self.plan(p=p)["reason"], "quote_time_regression")

    def test_stale_or_invalid_existing_position_blocks_entry(self):
        p = self.read()
        p["cash"] = 900
        p["positions"] = [dict(symbol="SOL", direction="LONG", entry_price=100, stop_loss=98,
                                margin_eur=100, leverage=1, initial_sl_dist=2)]
        self.assertTrue(self.plan(p=p)["ok"])
        p["positions"][0]["stop_loss"] = None
        self.assertEqual(self.plan(p=p)["reason"], "existing_stop_missing_or_invalid")
        p["positions"][0]["stop_loss"] = 98
        q = copy.deepcopy(self.quotes)
        q["SOL"]["market_timestamp"] -= 61
        self.assertEqual(self.plan(p=p, quotes=q)["reason"], "quote_stale_or_future")

    def test_existing_initial_risk_cannot_be_freed_by_trailing(self):
        p = self.read()
        p["cash"] = 900
        p["positions"] = [dict(symbol="SOL", direction="LONG", entry_price=100, stop_loss=101,
                                margin_eur=100, leverage=1, initial_sl_dist=2, planned_risk_eur=59)]
        plan = self.plan(p=p)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["reason"], "minimum_margin_or_risk_budget")

    def test_dry_run_does_not_evaluate_or_write(self):
        before = self.path.read_bytes()
        with patch.object(self.portfolio, "evaluate_crypto_portfolio", side_effect=AssertionError("evaluation forbidden")):
            result = self.policy.run(1, dry_run=True)
        self.assertEqual(result["actions"][0]["action"], "WOULD-OPEN-LONG")
        self.assertEqual(self.path.read_bytes(), before)
        self.db.save_crypto_portfolio.assert_not_called()
        self.signals.generate_crypto_recommendations.assert_called_once_with(strategy_version="volume_confirmed", closed_only=True)

    def test_booking_metadata_gross_cash_and_history_preserved(self):
        result = self.policy.run(1)
        self.assertEqual(len(result["actions"]), 1, result)
        p = self.read()
        pos = p["positions"][0]
        self.assertEqual(p["cash"], round(1000-pos["margin_eur"], 2))
        self.assertEqual(p["trades"], self.original["trades"])
        self.assertEqual(p["value_history"], self.original["value_history"])
        self.assertEqual(p["paper_policy_quote_times"]["ETH"], self.now)
        self.assertEqual(pos["paper_policy_version"], self.policy.VERSION)
        self.assertLessEqual(pos["planned_risk_eur"], 15)

    def test_revalidate_current_state_when_position_changes_during_fetch(self):
        def changes_during_fetch(symbol):
            if symbol == "ETH":
                p = self.read()
                p["positions"] = [dict(symbol="ADA", direction="LONG", entry_price=100,
                                       stop_loss=98, margin_eur=100, leverage=1, initial_sl_dist=2)]
                p["cash"] = 900
                self.db.save_crypto_portfolio(1, p)
            return copy.deepcopy(self.quotes[symbol])
        self.market.fetch_ticker.side_effect = changes_during_fetch
        result = self.policy.run(1)
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["policy_blocks"][0]["reason"], "quote_missing")
        self.assertEqual([p["symbol"] for p in self.read()["positions"]], ["ADA"])

    def test_parallel_cycles_do_not_duplicate_or_overallocate(self):
        recs = {"count": 1, "suggestions": [self.signal]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.policy._execute(1, recs, copy.deepcopy(self.quotes)), range(2)))
        self.assertEqual(sum(len(r["actions"]) for r in results), 1)
        self.assertEqual(len(self.read()["positions"]), 1)

    def test_halt_drawdown_and_position_limit_are_retained(self):
        p = self.read()
        p["trading_halted"] = True
        self.assertEqual(self.plan(p=p)["reason"], "trading_halted")
        p.pop("trading_halted")
        p["cash"] = 740
        self.assertEqual(self.plan(p=p)["reason"], "drawdown_blocks_new_entries")
        p["cash"] = 600
        p["positions"] = [dict(symbol=s, direction="LONG", entry_price=100, stop_loss=98,
                                margin_eur=100, leverage=1, initial_sl_dist=2) for s in ("BTC","SOL","ADA","XRP")]
        self.assertEqual(self.plan(p=p)["reason"], "position_limit")

    def test_failed_booking_does_not_advance_quote_clock(self):
        self.policy.initialize(1)
        with patch.object(self.portfolio, "open_position", return_value={"ok": False, "error": "injected_failure"}):
            result = self.policy.run(1)
        self.assertEqual(result["actions"], [])
        self.assertNotIn("paper_policy_quote_times", self.read())

    def test_legacy_trailing_keeps_initial_risk_and_missing_distance_blocks(self):
        p = self.read()
        p["cash"] = 800
        old = dict(symbol="SOL", direction="LONG", entry_price=100, stop_loss=102,
                   margin_eur=200, leverage=5, initial_sl_dist=5.5)
        p["positions"] = [old]
        # Current trailing stop is in profit; original modeled risk is still 59.8625.
        self.assertEqual(self.plan(p=p)["reason"], "minimum_margin_or_risk_budget")
        self.assertGreater(self.policy._position_risk(old)[0], 59)
        for value in (None, 0, -1, float("nan")):
            with self.subTest(initial_sl_dist=value):
                old["initial_sl_dist"] = value
                self.assertEqual(self.plan(p=p)["reason"], "existing_initial_stop_distance_missing_or_invalid")

    def test_gross_target_that_cannot_cover_costs_is_blocked_both_directions(self):
        self.signal["take_profit"] = 100.4
        self.assertEqual(self.plan()["reason"], "target_not_positive_after_model_costs")
        self.signal.update(direction="SHORT", stop_loss=102., take_profit=99.6)
        self.assertEqual(self.plan()["reason"], "target_not_positive_after_model_costs")
        self.signal["take_profit"] = 99.0
        self.assertTrue(self.plan()["ok"])

    def test_stop_at_or_beyond_liquidation_is_blocked_both_directions(self):
        self.signal.update(leverage=10, stop_loss=90.2)
        self.assertEqual(self.plan()["reason"], "stop_not_before_liquidation")
        self.signal["stop_loss"] = 90
        self.assertEqual(self.plan()["reason"], "stop_not_before_liquidation")
        self.signal.update(direction="SHORT", stop_loss=109.8, take_profit=97.2)
        self.assertEqual(self.plan()["reason"], "stop_not_before_liquidation")
        self.signal["stop_loss"] = 110
        self.assertEqual(self.plan()["reason"], "stop_not_before_liquidation")

    def test_signal_hour_and_clock_are_revalidated_inside_planning(self):
        for offset in (-3600, 3600, 1):
            signal = {**self.signal, "signal_bar_end": self.signal["signal_bar_end"] + offset}
            self.assertEqual(self.plan(signal=signal)["reason"], "signal_bar_not_latest_closed_hour")
        for now in (float("nan"), float("inf"), 0, -1):
            self.assertEqual(self.policy.plan_entry(self.read(), self.signal, self.quotes, now=now)["reason"], "planning_time_invalid")
        # Quotes collected immediately after the next boundary are fresh, but an
        # earlier signal must still be rejected under the commit lock.
        next_hour = self.signal["signal_bar_end"] + 3600
        quotes = {s: {**q, "market_timestamp": next_hour} for s, q in self.quotes.items()}
        self.assertEqual(self.policy.plan_entry(self.read(), self.signal, quotes, now=next_hour)["reason"], "signal_bar_not_latest_closed_hour")

    def test_conflicting_or_malformed_marker_is_preserved_and_blocks_all_entry_paths(self):
        valid = self.policy._marker(self.now)
        variants = [None, "bad", {}, {**valid, "version": "future_v2"},
                    {**valid, "rule_hash": "wrong"}, {**valid, "rules": {**self.policy.RULES, "max_positions": 5}},
                    {**valid, "started_at": None}, {**valid, "reason": ""}]
        for marker in variants:
            with self.subTest(marker=marker):
                self.db.save_crypto_portfolio(1, {**self.original, "paper_execution_policy": marker})
                self.db.save_crypto_portfolio.reset_mock()
                before = self.path.read_bytes()
                self.assertEqual(self.policy.initialize(1)["reason"], "policy_marker_mismatch")
                for dry in (False, True):
                    result = self.policy.run(1, dry_run=dry)
                    self.assertEqual(result["actions"], [])
                    self.assertEqual(result["policy_blocks"][0]["reason"], "policy_marker_mismatch")
                self.assertEqual(self.path.read_bytes(), before)
                self.db.save_crypto_portfolio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
