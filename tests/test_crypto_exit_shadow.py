"""Fully isolated: no app bootstrap, production DB, network, Telegram or orders."""
import ast
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from functools import wraps
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("exit_shadow_test", ROOT / "analyzer/crypto_exit_shadow.py")
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.db"
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users VALUES(1); INSERT INTO users VALUES(2); CREATE TABLE crypto_portfolios(user_id INTEGER PRIMARY KEY,data TEXT);")
        shadow.init_schema(self.path)
        self.now = 1791643500.0  # exact 15-minute boundary
        self.now -= self.now % 900
        self.now += 600
        self.started = self.now
        self.p = {"positions": [], "trades": [], "cash": 1000, "monitor_success_at": self.now}
        self.persist()
        self.bars = Mock(side_effect=lambda _: self.candles(self.now))
        self.quote = Mock(side_effect=lambda _: {"price": 99, "market_at": self.now})
        self.cycle()

    def persist(self):
        self.p["monitor_success_at"] = self.now
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("INSERT OR REPLACE INTO crypto_portfolios VALUES(1,?)", (json.dumps(self.p),))

    def cycle(self):
        return shadow.cycle(self.path, bars=self.bars, quote=self.quote, clock=lambda: self.now)

    def result(self, uid=1):
        return shadow.report(self.path, uid, now=self.now)

    def position(self, key="ADA_1", direction="LONG"):
        return {"id": key, "symbol": "ADA", "direction": direction, "leverage": 2,
                "margin_eur": 100, "entry_price": 100, "last_price": 100,
                "opened_at_ts": self.now, "stop_loss": 95, "take_profit": 110,
                "trailing_active": False}

    @staticmethod
    def candles(now, direction="LONG", volume=150, cross=True):
        end = int(now // 900) * 900
        rows = []
        for i in range(60):
            close = (90 if direction == "LONG" else 110) if i == 59 and cross else 100
            rows.append([str(int((end - (60 - i) * 900) * 1000)), "100", "120", "80", str(close), "1", "1", str(volume if i == 59 else 100), "1"])
        return rows[::-1]

    def enter(self, direction="LONG"):
        self.now += 10
        self.p["positions"] = [self.position(direction=direction)]
        self.persist()
        self.cycle()

    def advance_to_bar(self):
        target = (int(self.now // 900) + 1) * 900 + 10
        while self.now < target:
            self.now = min(target, self.now + 30)
            self.persist()
            self.cycle()

    def close_baseline(self, price=98):
        self.now += 30
        pos = self.p["positions"].pop()
        self.p["trades"].append({**pos, "exit_price": price, "closed_at": self.now, "close_reason": "Trailing-Stop"})
        self.persist()
        self.cycle()

    def test_long_short_threshold_and_confirmed_only(self):
        now = int(self.now // 900) * 900 + 10
        for direction in ("LONG", "SHORT"):
            rows = self.candles(now, direction)
            signal = shadow.volume_signal(rows, direction, now)
            self.assertTrue(signal["triggered"])
            self.assertEqual(signal["relative_volume"], 1.5)
            rows[0][7] = "149.9"
            self.assertFalse(shadow.volume_signal(rows, direction, now)["triggered"])
            rows[0][8] = "0"
            with self.assertRaisesRegex(ValueError, "stale"):
                shadow.volume_signal(rows, direction, now)

    def test_volume_alone_or_already_below_ema_does_not_exit(self):
        now = int(self.now // 900) * 900 + 10
        self.assertFalse(shadow.volume_signal(self.candles(now, volume=500, cross=False), "LONG", now)["triggered"])
        rows = self.candles(now, volume=500)
        rows[1][4] = "89"
        self.assertFalse(shadow.volume_signal(rows, "LONG", now)["triggered"])

    def test_no_future_stale_duplicate_missing_or_zero_volume_bars(self):
        now = int(self.now // 900) * 900 + 10
        for kind in ("duplicate", "missing", "nan", "zero", "unconfirmed", "future"):
            rows = self.candles(now)
            if kind == "duplicate": rows.append(rows[3])
            elif kind == "missing": rows.pop(4)
            elif kind == "nan": rows[0][7] = "nan"
            elif kind == "zero":
                for row in rows: row[7] = "0"
            elif kind == "unconfirmed":
                for row in rows: row[8] = "0"
            else:
                for row in rows: row[0] = str(int(row[0]) + 90000000)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                shadow.volume_signal(rows, "LONG", now)

    def test_existing_position_excluded_no_retroactive_exit(self):
        self.p["positions"] = [self.position()]
        self.persist()
        self.cycle()
        r = self.result()["rows"][0]
        self.assertIn("late_adoption", r["exclusions"])
        self.assertIsNone(r["shadow_exit"])
        self.bars.assert_not_called()

    def test_early_exit_paired_costs_persistence_and_no_source_mutation(self):
        self.enter()
        self.advance_to_bar()
        original = copy.deepcopy(self.p)
        result = self.result()
        row = result["rows"][0]
        self.assertEqual(row["exclusions"], [])
        self.assertEqual(row["shadow_exit"]["price"], 99)  # NOT candle close 90
        self.assertEqual(row["shadow_exit"]["gross_eur"], -2)
        self.assertEqual(row["shadow_exit"]["costs_eur"], .6)
        self.assertEqual(row["shadow_exit"]["net_eur"], -2.6)
        self.assertEqual(result["summary"]["completed_pairs"], 0)
        self.assertEqual(shadow._source(self.path, 1), original)
        self.cycle()
        self.quote.assert_called_once()
        self.close_baseline()
        report = self.result()
        self.assertEqual(report["summary"]["completed_pairs"], 1)
        self.assertEqual(report["summary"]["difference_eur"], 1.99)
        self.assertEqual(report["rows"][0]["shadow_exit"]["price"], 99)
        self.assertEqual(report["rows"][0]["baseline_exit"]["price"], 98)
        shadow.init_schema(self.path)  # restart is idempotent
        self.cycle()
        self.assertEqual(self.result(), report)

    def test_no_signal_copies_baseline_exit_and_costs_exactly(self):
        self.enter()
        self.bars.side_effect = lambda _: self.candles(self.now, cross=False)
        self.advance_to_bar()
        self.close_baseline(price=103)
        r = self.result()["rows"][0]
        self.assertEqual(r["baseline_exit"], r["shadow_exit"])
        self.assertEqual(self.result()["summary"]["difference_eur"], 0)

    def test_baseline_closes_during_quote_request_wins_race(self):
        self.enter()
        def quote(_):
            pos = self.p["positions"].pop()
            self.p["trades"].append({**pos, "exit_price": 102, "closed_at": self.now - 1, "close_reason": "TP"})
            self.persist()
            return {"price": 99, "market_at": self.now}
        self.quote.side_effect = quote
        self.advance_to_bar()
        r = self.result()["rows"][0]
        self.assertEqual(r["shadow_exit"]["price"], 102)
        self.assertEqual(r["baseline_exit"], r["shadow_exit"])

    def test_stale_exit_quote_never_fills_and_excludes(self):
        self.enter()
        self.quote.side_effect = lambda _: {"price": 99, "market_at": self.now - 120}
        self.advance_to_bar()
        r = self.result()["rows"][0]
        self.assertIsNone(r["shadow_exit"])
        self.assertIn("data_error", r["exclusions"])
        self.assertFalse(self.result()["healthy"])

    def test_worker_gap_excluded_without_retroactive_fill(self):
        self.enter()
        self.now += 1800
        self.persist()
        self.cycle()
        r = self.result()["rows"][0]
        self.assertIn("observation_gap", r["exclusions"])
        self.assertIn("missed_bar", r["exclusions"])
        self.assertIsNone(r["shadow_exit"])

    def test_reset_keeps_log_but_does_not_invent_a_close(self):
        self.enter()
        self.p["positions"] = []
        self.persist()
        self.cycle()
        r = self.result()["rows"][0]
        self.assertIn("source_position_missing", r["exclusions"])
        self.assertIsNone(r["baseline_exit"])
        self.assertIsNone(r["shadow_exit"])

    def test_missed_entire_trade_does_not_improve_statistics(self):
        self.now += 10
        pos = self.position()
        self.now += 10
        self.p["trades"] = [{**pos, "exit_price": 104, "closed_at": self.now, "close_reason": "TP"}]
        self.persist()
        self.cycle()
        r = self.result()["rows"][0]
        self.assertIn("entry_not_observed", r["exclusions"])
        self.assertEqual(self.result()["summary"]["completed_pairs"], 0)

    def test_report_user_isolation_and_get_has_no_writes(self):
        self.enter()
        before = self.path.read_bytes()
        other = self.result(uid=2)
        self.assertEqual(other["rows"], [])
        self.assertFalse(other["healthy"])
        self.assertEqual(self.path.read_bytes(), before)
        future = shadow.report(self.path, 1, now=self.now + 100)
        self.assertFalse(future["healthy"])

    def test_short_execution_and_liquidation_cost_model(self):
        self.enter(direction="SHORT")
        self.bars.side_effect = lambda _: self.candles(self.now, direction="SHORT")
        self.quote.side_effect = lambda _: {"price": 101, "market_at": self.now}
        self.advance_to_bar()
        record = self.result()["rows"][0]
        self.assertEqual(record["shadow_exit"]["gross_eur"], -2)
        self.assertEqual(shadow.valuation(record, 250)["gross_eur"], -100)

    def test_authenticated_endpoint_does_not_accept_other_user_id(self):
        from flask import Flask, jsonify, redirect, request, session, url_for
        app = Flask("isolated-shadow-api")
        app.secret_key = "isolated-test-only"
        app.add_url_rule('/login', 'login_page', lambda: 'login')
        ns = dict(app=app, wraps=wraps, jsonify=jsonify, redirect=redirect, request=request,
                  url_for=url_for, is_logged_in=lambda: bool(session.get('user_id')),
                  get_current_user_id=lambda: session.get('user_id'),
                  db_store=types.SimpleNamespace(DB_PATH=self.path))
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        for name in ('login_required', 'api_crypto_exit_shadow'):
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated-app', 'exec'), ns)
        fake = types.ModuleType('analyzer')
        fake.crypto_exit_shadow = shadow
        self.enter()
        with patch.dict(sys.modules, {'analyzer': fake}), app.test_client() as client:
            self.assertEqual(client.get('/api/crypto/exit_shadow', headers={'Content-Type':'application/json'}).status_code, 401)
            with client.session_transaction() as sess:
                sess['user_id'] = 2
            response = client.get('/api/crypto/exit_shadow?user_id=1')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['rows'], [])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(client.post('/api/crypto/exit_shadow').status_code, 405)

    def test_route_requires_login_uses_session_and_is_readonly(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "api_crypto_exit_shadow")
        source = ast.unparse(fn)
        self.assertIn("@login_required", source)
        self.assertIn("get_current_user_id()", source)
        self.assertNotIn("request.args", source)
        self.assertNotIn("cycle(", source)
        self.assertNotIn("evaluate_crypto_portfolio", source)


if __name__ == "__main__":
    unittest.main()
