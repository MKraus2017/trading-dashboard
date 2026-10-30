"""Isolated tests: no real DB bootstrap, market requests, Telegram or Git writes."""
import ast
import copy
import importlib.util
import json
import io
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import storage_runtime


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def function(path, name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class CryptoWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = time.time()
        clock_patch = patch("time.time", side_effect=lambda: self.now)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        network_patch = patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden in isolated tests"))
        network_patch.start()
        self.addCleanup(network_patch.stop)
        self.p = dict(cash=1000, start_capital=1000, positions=[], trades=[], value_history=[])
        db = types.ModuleType("analyzer.db_store")
        db.DB_PATH = str(Path(self.tmp.name) / "test.db")
        with closing(sqlite3.connect(db.DB_PATH)) as conn, conn:
            conn.execute("CREATE TABLE ledger(user_id INTEGER PRIMARY KEY, data TEXT)")
        db.load_crypto_portfolio = lambda uid: copy.deepcopy(self.p)
        db._default_crypto_portfolio = lambda: dict(cash=1000, start_capital=1000, positions=[], trades=[], value_history=[])
        def save(uid, value, **kwargs):
            self.p = copy.deepcopy(value)
            with closing(sqlite3.connect(db.DB_PATH)) as conn, conn:
                conn.execute("INSERT OR REPLACE INTO ledger VALUES(?,?)", (uid, json.dumps(value)))
        db.save_crypto_portfolio = Mock(side_effect=save)
        self.db = db
        config = types.ModuleType("config")
        # Read numeric trading constants without executing config or reading secrets.
        for node in ast.parse((ROOT / "config.py").read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("CRYPTO_"):
                        try:
                            setattr(config, target.id, ast.literal_eval(node.value))
                        except ValueError:
                            pass
        self.config = config
        okx = types.ModuleType("analyzer.okx_client")
        okx.fetch_ticker = Mock(return_value=self.quote(100))
        self.okx = okx
        package = types.ModuleType("analyzer")
        package.__path__ = [str(ROOT / "analyzer")]
        package.db_store, package.okx_client = db, okx
        self.patch_modules = patch.dict(sys.modules, {"analyzer": package, "analyzer.db_store": db,
                                                      "analyzer.okx_client": okx, "config": config})
        self.patch_modules.start()
        self.addCleanup(self.patch_modules.stop)
        self.runtime = load("analyzer.crypto_runtime", ROOT / "analyzer/crypto_runtime.py")
        self.watch = load("analyzer.crypto_watch", ROOT / "analyzer/crypto_watch.py")
        self.cp = load("analyzer.crypto_portfolio", ROOT / "analyzer/crypto_portfolio.py")

    def open(self, symbol="BTC", direction="LONG"):
        return self.cp.open_position(1, symbol, direction, 100, 2, 100,
                                     95 if direction == "LONG" else 105,
                                     110 if direction == "LONG" else 90)

    def quote(self, price, stamp=None):
        return {"last": price, "market_timestamp": self.now if stamp is None else stamp}

    def test_parallel_stop_checks_close_once_at_observed_price(self):
        self.open()
        self.okx.fetch_ticker.return_value = self.quote(90)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.cp.evaluate_crypto_portfolio(1, background=True), range(16)))
        self.assertEqual(sum(len(r["events"]) for r in results), 1)
        self.assertEqual(len(self.p["trades"]), 1)
        self.assertEqual(self.p["trades"][0]["exit_price"], 90)  # do not fake an ideal SL fill at 95
        self.assertEqual(self.p["trades"][0]["pnl_eur"], -20)
        self.assertEqual(self.p["cash"], 980)
        self.assertEqual(self.p["positions"], [])
        self.assertFalse(self.db.save_crypto_portfolio.call_args.kwargs["backup"])

    def test_parallel_duplicate_entries_do_not_spend_twice(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.open(), range(8)))
        self.assertEqual(sum(r["ok"] for r in results), 1)
        self.assertEqual(self.p["cash"], 900)

    def test_missing_and_invalid_quotes_do_not_close_or_report_healthy(self):
        self.open()
        self.p["monitor_success_at"] = time.time()
        for quote in [None, {}, {"last": 0}, {"last": -1}, {"last": float("nan")}, {"last": float("inf")}]:
            self.okx.fetch_ticker.return_value = quote
            result = self.cp.evaluate_crypto_portfolio(1, background=True)
            self.assertEqual(result["events"], [])
            self.assertEqual(len(self.p["positions"]), 1)
            self.assertEqual(result["missing_symbols"], ["BTC"])
            self.assertFalse(self.watch.status(self.p)["healthy"])

    def test_page_refresh_cannot_set_background_success(self):
        self.cp.evaluate_crypto_portfolio(1)
        self.assertNotIn("monitor_success_at", self.p)
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertTrue(self.watch.status(self.p)["healthy"])

    def test_history_not_truncated_and_sampled_every_five_minutes(self):
        recent = datetime.now(timezone.utc).isoformat()
        self.p["value_history"] = [{"date": recent, "value": 1000}] * 2500
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(len(self.p["value_history"]), 2500)
        self.p["value_history"][-1] = {"date": "2020-01-01T00:00:00", "value": 1000}
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(len(self.p["value_history"]), 2501)

    def test_short_take_profit_and_liquidation(self):
        self.open(direction="SHORT")
        self.okx.fetch_ticker.return_value = self.quote(89)
        result = self.cp.evaluate_crypto_portfolio(1)
        self.assertIn("Take-Profit", result["events"][0]["msg"])
        self.assertEqual(self.p["cash"], 1022)
        self.open()
        self.okx.fetch_ticker.return_value = self.quote(1)
        result = self.cp.evaluate_crypto_portfolio(1)
        self.assertEqual(result["events"][0]["type"], "liquidation")
        self.assertEqual(self.p["cash"], 922)

    def test_trailing_never_moves_backward(self):
        self.open()
        self.okx.fetch_ticker.return_value = self.quote(104)
        self.cp.evaluate_crypto_portfolio(1)
        stop = self.p["positions"][0]["stop_loss"]
        self.assertTrue(self.p["positions"][0]["trailing_active"])
        self.okx.fetch_ticker.return_value = self.quote(103)
        self.cp.evaluate_crypto_portfolio(1)
        self.assertEqual(self.p["positions"][0]["stop_loss"], stop)

    def test_bad_market_time_cannot_mark_trail_stop_take_profit_or_liquidate(self):
        self.open()
        # A healthy quote would also activate the existing critical drawdown
        # close, so every invalid timestamp must protect that path as well.
        self.p["cash"] = 600
        self.p["positions"][0]["opened_at_ts"] = self.now - 1000
        self.p["positions"][0]["last_quote_at"] = self.now - 30
        original = copy.deepcopy(self.p["positions"][0])
        self.p["monitor_success_at"] = self.now - 1
        cases = [(None, "missing_timestamp"), (self.now-61, "stale_quote"),
                 (self.now+5.001, "future_quote"), (self.now-31, "time_regression"),
                 (float("nan"), "invalid_market_timestamp"), (float("inf"), "invalid_market_timestamp"),
                 (True, "invalid_market_timestamp"), (0, "invalid_market_timestamp")]
        for price in (90, 111, 1, 104):
            for stamp, error in cases:
                with self.subTest(price=price, stamp=stamp):
                    self.okx.fetch_ticker.return_value = {"last": price, "market_timestamp": stamp}
                    result = self.cp.evaluate_crypto_portfolio(1, background=True)
                    self.assertEqual(result["events"], [])
                    self.assertEqual(self.p["positions"], [original])
                    self.assertEqual(self.p["cash"], 600)
                    self.assertEqual(self.p["trades"], [])
                    self.assertEqual(result["missing_symbols"], ["BTC"])
                    self.assertEqual(self.p["monitor_quote_errors"], {"BTC": error})
                    self.assertEqual(self.p["monitor_success_at"], self.now-1)
                    self.assertEqual(self.p["monitor_quote_policy"], "fresh_exit_quotes_v1")
                    self.assertFalse(self.watch.status(self.p)["healthy"])

    def test_quote_before_entry_or_prior_entry_quote_is_rejected(self):
        self.open()
        original = copy.deepcopy(self.p["positions"][0])
        self.okx.fetch_ticker.return_value = self.quote(90, self.now-1)
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(self.p["positions"], [original])
        self.assertEqual(self.p["monitor_quote_errors"], {"BTC": "before_entry"})
        self.p["positions"][0]["opened_at_ts"] -= 100
        self.p["positions"][0]["entry_quote_at"] = self.now
        original = copy.deepcopy(self.p["positions"][0])
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(self.p["positions"], [original])
        self.assertEqual(self.p["monitor_quote_errors"], {"BTC": "before_entry"})

    def test_valid_legacy_iso_entry_and_missing_or_corrupt_entry_times(self):
        self.open()
        self.p["positions"][0].pop("opened_at_ts")
        self.p["positions"][0]["opened_at"] = datetime.fromtimestamp(self.now-3600, timezone.utc).isoformat()
        self.okx.fetch_ticker.return_value = self.quote(100)
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result["missing_symbols"], [])
        self.assertEqual(self.p["positions"][0]["last_quote_at"], self.now)
        good = copy.deepcopy(self.p["positions"][0])
        for key, value, error in [("opened_at", None, "missing_entry_time"),
                                  ("opened_at", "2026-10-01T00:00:00", "invalid_entry_time"),
                                  ("opened_at", "not-a-date", "invalid_entry_time"),
                                  ("opened_at_ts", None, "invalid_entry_time"),
                                  ("last_quote_at", float("nan"), "invalid_previous_quote_time"),
                                  ("entry_quote_at", 0, "invalid_entry_quote_time")]:
            with self.subTest(key=key, value=value):
                bad = {**good, key: value}
                self.p["positions"] = [bad]
                self.okx.fetch_ticker.return_value = self.quote(90)
                self.cp.evaluate_crypto_portfolio(1, background=True)
                self.assertEqual(self.p["positions"], [bad])
                self.assertEqual(self.p["monitor_quote_errors"], {"BTC": error})

    def test_stale_time_exit_blocks_then_fresh_quote_recovers(self):
        self.open()
        self.p["positions"][0]["opened_at_ts"] = self.now - 73*3600
        original = copy.deepcopy(self.p["positions"][0])
        self.okx.fetch_ticker.return_value = self.quote(100, self.now-61)
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result["events"], [])
        self.assertEqual(self.p["positions"], [original])
        self.okx.fetch_ticker.return_value = self.quote(100)
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertIn("Zeit-Exit", result["events"][0]["msg"])
        self.assertEqual(self.p["monitor_quote_errors"], {})
        self.assertEqual(self.p["trades"][0]["last_quote_at"], self.now)
        self.assertTrue(self.watch.status(self.p)["healthy"])

    def test_fresh_mark_and_timestamp_are_saved_together_and_regression_blocked(self):
        self.open()
        self.now += 1
        self.okx.fetch_ticker.return_value = self.quote(104)
        self.cp.evaluate_crypto_portfolio(1, background=True)
        with closing(sqlite3.connect(self.db.DB_PATH)) as conn:
            saved = json.loads(conn.execute("SELECT data FROM ledger WHERE user_id=1").fetchone()[0])
        self.assertEqual(saved, self.p)
        self.assertEqual(saved["positions"][0]["last_price"], 104)
        self.assertEqual(saved["positions"][0]["last_quote_at"], self.now)
        original = copy.deepcopy(self.p["positions"][0])
        self.okx.fetch_ticker.return_value = self.quote(90, self.now-.5)
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(self.p["positions"], [original])
        self.assertEqual(self.p["monitor_quote_errors"], {"BTC": "time_regression"})
        self.now += 1
        self.okx.fetch_ticker.return_value = self.quote(90)
        self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(self.p["positions"], [])
        self.assertEqual(self.p["trades"][0]["exit_price"], 90)
        self.assertEqual(self.p["monitor_quote_errors"], {})

    def test_stale_quote_blocks_emergency_close_then_valid_quote_closes_once(self):
        self.open()
        self.p["cash"] = 600
        self.p["positions"][0]["opened_at_ts"] = self.now - 100
        original = copy.deepcopy(self.p["positions"][0])
        self.okx.fetch_ticker.return_value = self.quote(100, self.now-61)
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result["events"], [])
        self.assertEqual(self.p["positions"], [original])
        self.assertEqual(self.p["cash"], 600)
        self.okx.fetch_ticker.return_value = self.quote(100)
        self.okx.fetch_ticker.reset_mock()
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(self.okx.fetch_ticker.call_count, 1)
        self.assertEqual(len(self.p["trades"]), 1)
        self.assertEqual(result["events"][0]["type"], "emergency_close")
        self.assertEqual(self.p["cash"], 700)

    def test_slow_other_symbol_cannot_turn_old_quote_into_emergency_fill(self):
        self.open("BTC")
        self.open("ETH")
        self.p["cash"] = 500
        for pos in self.p["positions"]:
            pos["opened_at_ts"] = self.now-1000
        first_stamp = self.now
        def delayed(symbol):
            if symbol == "ETH":
                self.now += 61
            return self.quote(100, first_stamp if symbol == "BTC" else self.now)
        self.okx.fetch_ticker.side_effect = delayed
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result["events"], [])
        self.assertEqual(self.p["cash"], 500)
        self.assertEqual(len(self.p["positions"]), 2)
        self.assertEqual(self.p["trades"], [])
        self.assertEqual(self.p["monitor_quote_errors"], {"BTC": "stale_quote"})
        self.assertEqual(self.okx.fetch_ticker.call_count, 2)

    def test_slow_fetch_also_marks_noncritical_cycle_unhealthy_without_undoing_valid_mark(self):
        self.open("BTC")
        self.open("ETH")
        for pos in self.p["positions"]:
            pos["opened_at_ts"] = self.now-1000
        first_stamp = self.now
        self.p["monitor_success_at"] = self.now-1
        def delayed(symbol):
            if symbol == "ETH":
                self.now += 61
            return self.quote(104 if symbol == "BTC" else 100, first_stamp if symbol == "BTC" else self.now)
        self.okx.fetch_ticker.side_effect = delayed
        result = self.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result["events"], [])
        self.assertEqual(self.p["monitor_quote_errors"], {"BTC": "stale_quote"})
        self.assertEqual(self.p["monitor_success_at"], first_stamp-1)
        self.assertFalse(self.watch.status(self.p)["healthy"])
        btc = next(p for p in self.p["positions"] if p["symbol"] == "BTC")
        self.assertEqual(btc["last_quote_at"], first_stamp)
        self.assertEqual(btc["last_price"], 104)
        self.assertTrue(btc["trailing_active"])

    def test_freshness_thresholds_are_inclusive_and_require_entry_floor(self):
        pos = dict(opened_at_ts=self.now-1000)
        for stamp in (self.now-60, self.now+5):
            self.assertEqual(self.cp._owner_quote_time(pos, self.quote(100, stamp), self.now), (stamp, None))
        self.assertEqual(self.cp._owner_quote_time(pos, self.quote(100, self.now-60.001), self.now)[1], "stale_quote")
        self.assertEqual(self.cp._owner_quote_time(pos, self.quote(100, self.now+5.001), self.now)[1], "future_quote")

    def test_other_user_keeps_legacy_quote_behavior(self):
        self.cp.open_position(2, "BTC", "LONG", 100, 2, 100, 95, 110)
        self.okx.fetch_ticker.return_value = {"last": 90}
        result = self.cp.evaluate_crypto_portfolio(2, background=True)
        self.assertEqual(result["events"][0]["trade"]["exit_price"], 90)
        self.assertNotIn("last_quote_at", self.p["trades"][0])
        self.assertNotIn("monitor_quote_errors", self.p)
        self.assertNotIn("monitor_quote_policy", self.p)

    def test_status_stale_future_missing_or_failed(self):
        for stamp in [None, 0, 800, 1001, float("nan")]:
            self.assertFalse(self.watch.status({"monitor_success_at": stamp}, now=1000)["healthy"])
        self.assertTrue(self.watch.status({"monitor_success_at": 990}, now=1000)["healthy"])
        self.assertFalse(self.watch.status({"monitor_success_at": 990, "monitor_missing_symbols": ["BTC"]}, now=1000)["healthy"])

    def test_loop_retries_errors_and_compensates_duration(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        check = Mock(side_effect=[RuntimeError("offline"), {"ok": True}])
        clock = Mock(side_effect=[0, 7, 30, 65])
        self.watch.run_loop(check, stop, clock=clock)
        self.assertEqual(check.call_count, 2)
        self.assertEqual([c.args[0] for c in stop.wait.call_args_list], [23, 1])

    def test_process_lock_excludes_second_leader_and_releases(self):
        path = str(Path(self.tmp.name) / "leader.lock")
        with self.runtime.file_lock(path, blocking=False) as first:
            self.assertTrue(first)
            with self.runtime.file_lock(path, blocking=False) as second:
                self.assertFalse(second)
        with self.runtime.file_lock(path, blocking=False) as next_leader:
            self.assertTrue(next_leader)

    def test_db_save_backup_flag_does_not_change_settings_backup(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript("CREATE TABLE crypto_portfolios(user_id INTEGER PRIMARY KEY,data TEXT,updated_at TEXT); CREATE TABLE settings(user_id INTEGER PRIMARY KEY,crypto_strategy_version TEXT,updated_at TEXT);")
        backup = Mock()
        ns = dict(json=json, get_conn=lambda: conn, _now=lambda: "now", backup_db=backup)
        save = function(ROOT / "analyzer/db_store.py", "save_crypto_portfolio", ns)
        save(1, self.p, backup=False)
        backup.assert_not_called()
        save(1, self.p)
        self.assertEqual(backup.call_count, 1)
        setter = function(ROOT / "analyzer/db_store.py", "set_crypto_strategy_version", ns)
        setter(1, "volume_confirmed")
        self.assertEqual(backup.call_count, 2)


class StorageTests(unittest.TestCase):
    def test_restore_retains_pre_restore_copy_and_only_then_starts_watchers(self):
        from flask import Flask, jsonify
        app = Flask("restore-test")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RENDER": "true", "TRADING_MAINTENANCE": "0"}):
            source = Path(tmp) / "upload.db"
            current = Path(tmp) / "current.db"
            for path, value in [(source, 42), (current, 7)]:
                with closing(sqlite3.connect(path)) as c:
                    c.executescript("CREATE TABLE users(id INTEGER); INSERT INTO users VALUES (1); CREATE TABLE portfolios(data INTEGER);")
                    c.execute("INSERT INTO portfolios VALUES (?)", (value,))
                    c.commit()
            start = Mock()
            ns = dict(os=os, tempfile=tempfile, time=time, closing=closing, jsonify=jsonify,
                      storage_runtime=storage_runtime, start_background_watchers=start,
                      db_store=types.SimpleNamespace(DB_PATH=str(current), DB_DIR=tmp))
            restore = function(ROOT / "app.py", "api_db_restore_upload", ns)
            with app.test_request_context("/restore", method="POST", data={"db_file": (io.BytesIO(source.read_bytes()), "backup.db")}), patch.object(storage_runtime.os.path, "ismount", return_value=True):
                response = restore()
                self.assertTrue(response.json["ok"])
            start.assert_called_once()
            self.assertFalse(storage_runtime.maintenance_required(current))
            with closing(sqlite3.connect(current)) as c:
                self.assertEqual(c.execute("SELECT data FROM portfolios").fetchone()[0], 42)
            previous = list((Path(tmp) / "backups").glob("trading_pre_restore_*.db"))
            self.assertEqual(len(previous), 1)
            with closing(sqlite3.connect(previous[0])) as c:
                self.assertEqual(c.execute("SELECT data FROM portfolios").fetchone()[0], 7)

    def test_existing_render_database_never_overwritten_by_newer_repo_timestamp(self):
        ns = dict(storage_runtime=storage_runtime, _RESTORE_DONE=False, DB_PATH="test.db",
                  _db_has_users=lambda _: True, _backup_db_path=Mock(side_effect=AssertionError("Must not load repository backup")))
        restore = function(ROOT / "analyzer/db_store.py", "restore_db_backup", ns)
        with patch.dict(os.environ, {"RENDER": "true"}):
            restore()
        self.assertTrue(ns["_RESTORE_DONE"])

    def test_render_blocks_until_verified_mount_restore(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RENDER": "true", "TRADING_MAINTENANCE": "0"}):
            db = Path(tmp) / "trading.db"
            self.assertTrue(storage_runtime.maintenance_required(db))
            with patch.object(storage_runtime.os.path, "ismount", return_value=False):
                with self.assertRaises(RuntimeError):
                    storage_runtime.mark_restored(db)
            with patch.object(storage_runtime.os.path, "ismount", return_value=True):
                storage_runtime.mark_restored(db)
            self.assertFalse(storage_runtime.maintenance_required(db))
            with patch.dict(os.environ, {"TRADING_MAINTENANCE": "1"}):
                self.assertTrue(storage_runtime.maintenance_required(db))

    def test_snapshot_includes_committed_wal_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RENDER": "true", "TRADING_MAINTENANCE": "0"}):
            db = Path(tmp) / "trading.db"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE sample(value INTEGER)")
                conn.execute("INSERT INTO sample VALUES (42)")
                conn.commit()
                storage_runtime.marker(db).touch()
                storage_runtime.private_backup(db, force=True)
            copies = list((Path(tmp) / "backups").glob("trading_private_*.db"))
            self.assertEqual(len(copies), 1)
            with closing(sqlite3.connect(copies[0])) as saved:
                self.assertEqual(saved.execute("SELECT value FROM sample").fetchone()[0], 42)
            self.assertFalse((Path(tmp) / "backup").exists())

    def test_migration_guard_blocks_all_trading_and_permits_authenticated_restore_route(self):
        from flask import Flask, jsonify, request
        app = Flask("test")
        db = types.SimpleNamespace(DB_PATH="not-a-real-db")
        ns = dict(storage_runtime=storage_runtime, db_store=db, request=request, jsonify=jsonify)
        guard = function(ROOT / "app.py", "storage_migration_guard", ns)
        with patch.object(storage_runtime, "maintenance_required", return_value=True):
            for endpoint in ["api_scheduler_crypto_analysis", "api_scheduler_okx_spot_monitor", "api_crypto_portfolio", "register_page"]:
                with app.test_request_context("/api/scheduler/test", method="POST"):
                    request.url_rule = types.SimpleNamespace(endpoint=endpoint)
                    self.assertEqual(guard()[1], 503)
            with app.test_request_context("/api/db_restore_upload", method="POST"):
                request.url_rule = types.SimpleNamespace(endpoint="api_db_restore_upload")
                self.assertIsNone(guard())  # existing login_required remains on real endpoint


if __name__ == "__main__":
    unittest.main()
