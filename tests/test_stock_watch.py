"""Offline stock-monitor tests: fake dependencies, no DB/network/app bootstrap."""
import builtins
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


MODULE = Path(__file__).resolve().parents[1] / "analyzer" / "stock_watch.py"
NOW = 1800000000


def load_watch():
    spec = importlib.util.spec_from_file_location("isolated_stock_watch", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StopAfter:
    def __init__(self, iterations=1):
        self.iterations = iterations
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def clear(self):
        self.stopped = False

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        if len(self.waits) >= self.iterations:
            self.stopped = True
        return self.stopped


class FakeThread:
    created = []

    def __init__(self, target, name, daemon):
        self.target, self.name, self.daemon = target, name, daemon
        self.alive = False
        self.created.append(self)

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive


class StockWatchTests(unittest.TestCase):
    def setUp(self):
        self.watch = load_watch()
        self.env = patch.dict(os.environ, {"STOCK_PAPER_MONITOR_ENABLED": "1"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.db = types.SimpleNamespace(DB_PATH="/fake/trading.db", list_users=Mock(return_value=[{"id": 1}, {"id": 2}]))
        self.portfolio = types.SimpleNamespace(evaluate_portfolio=Mock(side_effect=lambda uid: (self.fresh(), [])))
        self.storage = types.ModuleType("storage_runtime")
        self.storage.maintenance_required = Mock(return_value=False)
        self.analyzer = types.ModuleType("analyzer")
        self.analyzer.db_store, self.analyzer.portfolio = self.db, self.portfolio
        self.runtime = types.ModuleType("analyzer.crypto_runtime")
        self.runtime.file_lock = Mock(side_effect=lambda *args, **kwargs: contextlib.nullcontext(True))
        self.modules = patch.dict(sys.modules, {"analyzer": self.analyzer, "analyzer.crypto_runtime": self.runtime,
                                              "storage_runtime": self.storage})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.clock = patch.object(self.watch.time, "time", return_value=NOW)
        self.clock.start(); self.addCleanup(self.clock.stop)
        FakeThread.created = []

    def fresh(self, **fields):
        return {"stock_monitor_attempt_at": NOW, "stock_monitor_success_at": NOW,
                "stock_monitor_missing_symbols": [], "stock_monitor_closed_symbols": [], **fields}

    def test_import_has_no_app_database_storage_or_thread_side_effects(self):
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == "storage_runtime" or name == "analyzer" or name.startswith("analyzer."):
                raise AssertionError("runtime dependency imported during module load")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), patch("threading.Thread.start") as start:
            module = load_watch()
        start.assert_not_called()
        self.assertIsNone(module._thread)

    def test_status_is_pure_and_market_closed_does_not_mean_unhealthy(self):
        p = self.fresh(); p["stock_monitor_closed_symbols"] = ["AAPL"]
        before = copy.deepcopy(p)
        result = self.watch.status(p, NOW)
        self.assertTrue(result["healthy"])
        self.assertTrue(result["data_complete"])
        self.assertEqual(result["closed_symbols"], ["AAPL"])
        self.assertEqual(result["interval_seconds"], 60)
        self.assertEqual(result["stale_after_seconds"], 180)
        self.assertEqual(result["scope"], "virtual_stocks_only")
        result["closed_symbols"].append("OTHER")
        self.assertEqual(p, before)

    def test_missing_data_is_separate_from_heartbeat(self):
        p = self.fresh(); p["stock_monitor_missing_symbols"] = ["SAP.DE"]
        result = self.watch.status(p, NOW)
        self.assertTrue(result["healthy"])
        self.assertFalse(result["data_complete"])
        self.assertEqual(result["missing_symbols"], ["SAP.DE"])

    def test_status_requires_valid_recent_timestamp_and_enabled_worker(self):
        for stamp in (None, 0, True, "1800000000", float("nan"), float("inf"), NOW + 1, NOW - 181):
            p = self.fresh(); p["stock_monitor_success_at"] = stamp
            self.assertFalse(self.watch.status(p, NOW)["healthy"], repr(stamp))
        p = self.fresh(); p["stock_monitor_success_at"] = NOW - 180
        self.assertTrue(self.watch.status(p, NOW)["healthy"])
        self.assertEqual(self.watch.status(p, NOW)["age_seconds"], 180)
        with patch.dict(os.environ, {"STOCK_PAPER_MONITOR_ENABLED": "0"}):
            self.assertFalse(self.watch.status(p, NOW)["healthy"])
            self.assertFalse(self.watch.status(p, NOW)["enabled"])

    def test_cycle_evaluates_each_user_once_without_notification_or_trade_calls(self):
        # The fake portfolio exposes ONLY evaluation; buys/sells/messages cannot be called.
        result = self.watch.cycle()
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual([call.args for call in self.portfolio.evaluate_portfolio.call_args_list], [(1,), (2,)])
        self.assertEqual(self.db.list_users.call_count, 1)
        self.assertEqual(result["failed_users"], [])

    def test_one_user_failure_does_not_block_others_or_leak_exception_content(self):
        self.portfolio.evaluate_portfolio.side_effect = [RuntimeError("private payload must not leak"), (self.fresh(), [])]
        result = self.watch.cycle()
        self.assertFalse(result["ok"])
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["results"][0]["user_id"], 2)
        self.assertEqual(result["failed_users"], [{"user_id": 1, "error_type": "RuntimeError"}])
        self.assertNotIn("private payload", repr(result))

    def test_maintenance_skips_listing_and_evaluation(self):
        self.storage.maintenance_required.return_value = True
        result = self.watch.cycle()
        self.assertEqual(result["skipped"], "maintenance")
        self.db.list_users.assert_not_called()
        self.portfolio.evaluate_portfolio.assert_not_called()

    def test_maintenance_activated_midcycle_blocks_remaining_user(self):
        self.storage.maintenance_required.side_effect = [False, False, True]
        result = self.watch.cycle()
        self.assertEqual(result["skipped"], "maintenance")
        self.portfolio.evaluate_portfolio.assert_called_once_with(1)

    def test_disabled_cycle_and_start_do_nothing(self):
        with patch.dict(os.environ, {"STOCK_PAPER_MONITOR_ENABLED": "0"}), patch.object(self.watch.threading, "Thread", FakeThread):
            self.assertEqual(self.watch.cycle()["skipped"], "disabled")
            self.watch.start()
        self.assertFalse(FakeThread.created)
        self.db.list_users.assert_not_called()

    def test_run_loop_paces_cycles_and_recovers_from_failure(self):
        stop = StopAfter(2)
        check = Mock(side_effect=[RuntimeError("secret error"), {"ok": True, "checked": 1}])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.watch.run_loop(check, stop, clock=Mock(side_effect=[0, 10, 60, 135]))
        self.assertEqual(stop.waits, [50, 1])
        self.assertEqual(check.call_count, 2)
        self.assertNotIn("secret error", output.getvalue())

    def test_start_is_idempotent_and_uses_expected_nonblocking_leader_lock(self):
        self.watch._stop = StopAfter()
        with patch.object(self.watch.threading, "Thread", FakeThread), patch.object(self.watch, "run_loop") as loop:
            loop.side_effect = lambda check, stop: stop.set()
            self.watch.start(); self.watch.start()
            self.assertEqual(len(FakeThread.created), 1)
            thread = FakeThread.created[0]
            self.assertTrue(thread.daemon)
            thread.target()
        self.runtime.file_lock.assert_called_once_with("/fake/trading.db.stock-monitor.lock", blocking=False)
        loop.assert_called_once_with(self.watch.cycle, self.watch._stop)

    def test_nonleader_never_evaluates_positions(self):
        self.watch._stop = StopAfter()
        self.runtime.file_lock.side_effect = lambda *args, **kwargs: contextlib.nullcontext(False)
        with patch.object(self.watch.threading, "Thread", FakeThread), patch.object(self.watch, "run_loop") as loop:
            self.watch.start(); FakeThread.created[0].target()
        loop.assert_not_called()
        self.portfolio.evaluate_portfolio.assert_not_called()

    def test_worker_maintenance_does_not_create_leader_file(self):
        self.watch._stop = StopAfter()
        self.storage.maintenance_required.return_value = True
        with patch.object(self.watch.threading, "Thread", FakeThread):
            self.watch.start(); FakeThread.created[0].target()
        self.runtime.file_lock.assert_not_called()
        self.portfolio.evaluate_portfolio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
