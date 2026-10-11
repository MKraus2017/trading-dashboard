"""Offline stock ledger invariants, no application bootstrap or public requests."""
import ast
import copy
import importlib.util
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class StockPortfolioSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stock-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.config = types.ModuleType('config')
        for k, v in dict(START_CAPITAL=1000, USE_CHANDELIER_EXIT=True, CHANDELIER_PERIOD=22,
                         CHANDELIER_MULT=2, TRAILING_STOP_PCT=.08, BREAKEVEN_AT_PCT=4,
                         TIME_EXIT_DAYS=10, MAX_POSITION_PCT=.16, CASH_RESERVE_PCT=.2,
                         MIN_POSITION_EUR=10, MAX_POSITIONS=5, DEFAULT_STOP_PCT=.03,
                         MIN_RR_RATIO=2).items():
            setattr(self.config, k, v)
        self.pkg = types.ModuleType('analyzer')
        self.pkg.__path__ = [str(ROOT / 'analyzer')]
        self.states = {1: dict(cash=900, positions=[self.position()], trades=[dict(action='BUY', symbol='TEST', invested=100)],
                               real_positions=[dict(symbol='PRIVATE', entry_price=30, shares=1)],
                               real_trades=[dict(action='BUY', symbol='PRIVATE', invested=30)],
                               value_history=[dict(date='2026-01-01T00:00:00', value=i) for i in range(370)])}
        self.db = types.SimpleNamespace(DB_PATH=str(Path(self.tmp.name) / 'unused.db'),
            load_portfolio=lambda uid: copy.deepcopy(self.states.get(uid)), save_portfolio=self.save,
            trigger_backup=Mock())
        self.yahoo = types.SimpleNamespace(get_fetch_stats=lambda: {}, fetch_yahoo=Mock(), fetch_latest_price=Mock())
        self.telegram = types.SimpleNamespace(notify_virtual_trade=Mock(), _send_message=Mock(), fmt_eur=str)
        self.quotes = types.SimpleNamespace(fetch_quote=Mock(side_effect=lambda sym: self.tick(102)))
        self.signals = types.SimpleNamespace(generate_recommendations=Mock(return_value={'suggestions': []}))
        self.pkg.db_store = self.db
        self.pkg.yahoo_client = self.yahoo
        self.pkg.telegram = self.telegram
        self.pkg.stock_quotes = self.quotes
        self.pkg.signals = self.signals
        self.pkg.stock_watch = types.SimpleNamespace(status=lambda p: {'scope': 'virtual_stocks_only'})
        self.modules = patch.dict(sys.modules, {'analyzer': self.pkg, 'config': self.config})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        load('analyzer.crypto_runtime', 'analyzer/crypto_runtime.py')
        load('analyzer.stock_runtime', 'analyzer/stock_runtime.py')
        quote_module = load('isolated_stock_quotes', 'analyzer/stock_quotes.py')
        self.quotes.is_tradable = quote_module.is_tradable
        self.pkg.indicators = load('analyzer.indicators', 'analyzer/indicators.py')
        self.p = load('isolated_stock_portfolio', 'analyzer/portfolio.py')
        self.actual_chandelier = self.p._chandelier_stop
        self.pkg.portfolio = self.p
        self.p.PORTFOLIO_FILE = str(Path(self.tmp.name) / 'test-only.json')
        self.p._chandelier_stop = Mock(return_value=98)
        self.auto = load('isolated_stock_auto', 'analyzer/auto_trader.py')

    def save(self, uid, p, **kwargs):
        self.states[uid] = copy.deepcopy(p)

    @staticmethod
    def position(**values):
        result = dict(symbol='TEST', shares=1, entry_price=100, invested=100, last_price=100,
                      stop_loss=97, take_profit=200, opened_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        result.update(values)
        return result

    @staticmethod
    def tick(price, tradable=True):
        now = time.time()
        return dict(price=price, tradable=tradable, market_open=tradable, currency='EUR', source_currency='EUR',
                    market_at=now, received_at=now, regular_start=now-3600, regular_end=now+3600)

    def evaluate(self):
        return self.p.evaluate_portfolio(1)

    def test_effective_stop_uses_highest_valid_stop(self):
        self.assertEqual(self.p.effective_stop(dict(stop_loss=100, trailing_stop=99, breakeven_set=True)), (100, 'breakeven'))
        self.assertEqual(self.p.effective_stop(dict(stop_loss=100, trailing_stop=101)), (101, 'trailing'))
        self.assertEqual(self.p.effective_stop(dict(stop_loss=100, trailing_stop=100)), (100, 'trailing'))
        self.assertEqual(self.p.effective_stop(dict(stop_loss=100, trailing_stop=float('nan'))), (100, 'stop_loss'))

    def test_chandelier_uses_validated_completed_history_or_keeps_existing_stop(self):
        history = dict(highs=[102]*30, lows=[98]*30, closes=[100]*30)
        self.quotes.fetch_completed_history = Mock(return_value=history)
        tick = self.tick(110)
        self.assertEqual(self.actual_chandelier('TEST', 120, tick), 112)
        self.quotes.fetch_completed_history.assert_called_once_with('TEST', tick)
        self.quotes.fetch_completed_history.side_effect = ValueError('unverifiable_history')
        self.assertIsNone(self.actual_chandelier('TEST', 120, tick))
        self.yahoo.fetch_yahoo.assert_not_called()

    def test_monitor_save_skips_publishing_and_shared_json(self):
        self.db.save_portfolio = Mock(side_effect=self.save)
        self.evaluate()
        self.assertEqual(self.db.save_portfolio.call_args.kwargs, {'automatic_backup': False})
        self.assertFalse(Path(self.p.PORTFOLIO_FILE).exists())

    def test_trailing_ratchets_and_never_lowers(self):
        self.p._chandelier_stop.side_effect = [98, 103, 90]
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(110)
        first, _ = self.evaluate()
        self.assertEqual(first['positions'][0]['trailing_stop'], 98)
        self.assertEqual(first['positions'][0]['effective_stop_loss'], 100)
        second, _ = self.evaluate()
        self.assertEqual(second['positions'][0]['trailing_stop'], 103)
        third, _ = self.evaluate()
        self.assertEqual(third['positions'][0]['trailing_stop'], 103)
        self.assertEqual(third['positions'][0]['stop_loss'], 100)
        self.assertIn('stop_last_raised_at', third['positions'][0])

    def test_fixed_percentage_trailing_follows_new_high(self):
        self.config.USE_CHANDELIER_EXIT = False
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(130)
        first, _ = self.evaluate()
        self.assertEqual(first['positions'][0]['trailing_stop'], 119.6)
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(140)
        second, _ = self.evaluate()
        self.assertEqual(second['positions'][0]['trailing_stop'], 128.8)
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(135)
        third, _ = self.evaluate()
        self.assertEqual(third['positions'][0]['trailing_stop'], 128.8)

    def test_gap_exit_uses_observed_price_and_strongest_reason(self):
        self.states[1]['positions'][0]['trailing_stop'] = 99
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(90)
        after, _ = self.evaluate()
        self.assertFalse(after['positions'])
        trade = after['trades'][-1]
        self.assertEqual(trade['price'], 90)
        self.assertIn('Trailing-Stop 99', trade['reason'])
        self.assertEqual(trade['effective_stop_loss'], 99)
        self.p._chandelier_stop.assert_not_called()

    def test_existing_tp_stays_active_with_trailing(self):
        pos = self.states[1]['positions'][0]
        pos.update(take_profit=106, trailing_stop=99)
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(107)
        after, _ = self.evaluate()
        self.assertIn('Take-Profit', after['trades'][-1]['reason'])
        self.assertEqual(after['trades'][-1]['take_profit'], 106)

    def test_market_closed_never_executes_or_changes_protection(self):
        before = copy.deepcopy(self.states[1])
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(90, tradable=False)
        after, alerts = self.evaluate()
        self.assertEqual(after['trades'], before['trades'])
        self.assertEqual(after['cash'], before['cash'])
        for k in ('entry_price', 'shares', 'stop_loss', 'take_profit', 'opened_at'):
            self.assertEqual(after['positions'][0][k], before['positions'][0][k])
        self.assertEqual(after['stock_monitor_closed_symbols'], ['TEST'])
        self.assertFalse(alerts)
        self.p._chandelier_stop.assert_not_called()

    def test_missing_quotes_keep_last_price_position_and_history(self):
        self.quotes.fetch_quote.side_effect = ValueError('stale_quote')
        before = copy.deepcopy(self.states[1])
        after, _ = self.evaluate()
        self.assertEqual(after['positions'][0]['last_price'], before['positions'][0]['last_price'])
        self.assertEqual(after['stock_monitor_missing_symbols'], ['TEST'])
        self.assertEqual(after['trades'], before['trades'])
        self.assertEqual(after['value_history'][:370], before['value_history'])

    def test_quote_ages_while_other_symbols_load_no_execution(self):
        self.states[1]['positions'].append(self.position(symbol='OTHER'))
        first = self.tick(90)
        first['regular_end'] = time.time() + 1
        def fetch(symbol):
            if symbol == 'TEST': return first
            first['regular_end'] = time.time() - 1
            return self.tick(102)
        self.quotes.fetch_quote.side_effect = fetch
        after, _ = self.evaluate()
        self.assertEqual(len(after['positions']), 2)
        self.assertEqual(len(after['trades']), 1)
        self.assertIn('TEST', after['stock_monitor_closed_symbols'])

    def test_quote_ages_during_atr_fetch_no_protection_change(self):
        tick = self.tick(110)
        self.quotes.fetch_quote.side_effect = lambda _: tick
        def atr(*args):
            tick['market_at'] = time.time() - 1201
            return 109
        self.p._chandelier_stop.side_effect = atr
        before = copy.deepcopy(self.states[1]['positions'][0])
        after, _ = self.evaluate()
        self.assertEqual(after['positions'][0]['stop_loss'], before['stop_loss'])
        self.assertNotIn('trailing_stop', after['positions'][0])
        self.assertNotIn('highest_price', after['positions'][0])
        self.assertIn('TEST', after['stock_monitor_missing_symbols'])

    def test_preserves_real_records_all_history_and_limits_new_sampling(self):
        before = copy.deepcopy(self.states[1])
        first, _ = self.evaluate()
        second, _ = self.evaluate()
        self.assertEqual(second['real_positions'], before['real_positions'])
        self.assertEqual(second['real_trades'], before['real_trades'])
        self.assertEqual(second['trades'], before['trades'])
        self.assertEqual(second['value_history'][:370], before['value_history'])
        self.assertEqual(len(first['value_history']), 371)
        self.assertEqual(len(second['value_history']), 371)
        self.telegram._send_message.assert_not_called()
        self.telegram.notify_virtual_trade.assert_not_called()

    def test_snapshot_does_not_evaluate_save_or_fetch(self):
        before = copy.deepcopy(self.states)
        snap = self.p.portfolio_snapshot(1)
        self.assertEqual(snap['positions'][0]['effective_stop_loss'], 97)
        self.assertEqual(before, self.states)
        self.quotes.fetch_quote.assert_not_called()

    def test_dry_run_never_evaluates_or_sends(self):
        before = copy.deepcopy(self.states)
        self.signals.generate_recommendations.return_value = {'suggestions': [{'symbol': 'TEST', 'direction': 'VERKAUF', 'score': 5}]}
        with patch.object(self.p, 'evaluate_portfolio', side_effect=AssertionError('dry run mutated')):
            result = self.auto.run_auto_trading(1, dry_run=True)
        self.assertEqual(result['actions'][0]['action'], 'WOULD-SELL')
        self.assertEqual(self.states, before)
        self.telegram._send_message.assert_not_called()
        self.quotes.fetch_quote.assert_not_called()

    def test_no_immediate_rebuy_after_stop_exit(self):
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(90)
        self.signals.generate_recommendations.return_value = {'suggestions': [{'symbol': 'TEST', 'direction': 'KAUF', 'score': 90}]}
        result = self.auto.run_auto_trading(1)
        self.assertEqual(result['portfolio']['positions'], [])
        self.assertEqual(len(result['portfolio']['trades']), 2)
        self.assertTrue(any(a['action'] == 'SKIP' for a in result['actions']))

    def test_addon_never_lowers_stop_or_changes_existing_tp(self):
        self.states[1]['positions'][0].update(stop_loss=100, trailing_stop=110, breakeven_set=True)
        result = self.p.buy(1, 'TEST', price=120, amount_eur=100, notify=False)
        self.assertTrue(result['ok'])
        self.assertGreaterEqual(self.p.effective_stop(result['position'])[0], 110)
        self.assertEqual(result['position']['take_profit'], 200)

    def test_new_buy_stores_initial_stop_and_fresh_quote_is_required(self):
        result = self.p.buy(1, 'NEW', price=100, amount_eur=100, notify=False)
        self.assertEqual(result['position']['initial_stop_loss'], 97)
        self.quotes.fetch_quote.side_effect = lambda sym: self.tick(100, tradable=False)
        before = copy.deepcopy(self.states)
        self.assertFalse(self.p.buy(1, 'OTHER', amount_eur=100, notify=False)['ok'])
        self.assertFalse(self.p.sell(1, 'TEST', notify=False)['ok'])
        self.assertEqual(before, self.states)

    def test_concurrent_evaluation_and_buy_keep_both_changes(self):
        entered, release, bought = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def quote(sym):
            entered.set()
            if not release.wait(3): raise AssertionError('test timeout')
            return self.tick(90)
        self.quotes.fetch_quote.side_effect = quote
        def evaluate():
            try: self.evaluate()
            except Exception as exc: errors.append(exc)
        def buy():
            try:
                self.p.buy(1, 'NEW', price=100, amount_eur=100, notify=False)
                bought.set()
            except Exception as exc: errors.append(exc)
        first = threading.Thread(target=evaluate); first.start()
        self.assertTrue(entered.wait(2))
        second = threading.Thread(target=buy); second.start()
        self.assertFalse(bought.wait(.05))
        release.set(); first.join(3); second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([t['action'] for t in self.states[1]['trades']], ['BUY', 'SELL', 'BUY'])
        self.assertEqual([p['symbol'] for p in self.states[1]['positions']], ['NEW'])
        self.assertEqual(self.states[1]['cash'], 890)

    def test_readonly_route_uses_session_not_supplied_user(self):
        from flask import Flask, jsonify
        app = Flask('stock-readonly-test')
        ns = dict(app=app, login_required=lambda f: f, get_current_user_id=lambda: 1,
                  portfolio=self.p, jsonify=jsonify, _calc_real_guv=lambda p:p,
                  _calc_virtual_guv=lambda p:p, _enrich_position_advice=lambda p:p)
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_portfolio')
        with patch.object(self.p, 'enrich_real_positions', side_effect=lambda p:p), \
             patch.object(self.p, 'calculate_comparison', return_value={}), \
             patch.object(self.p, 'run_backtest', return_value={}):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated-stock-route', 'exec'), ns)
            before = copy.deepcopy(self.states)
            with app.test_client() as client:
                result = client.get('/api/portfolio?user_id=99')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['portfolio']['positions'][0]['symbol'], 'TEST')
            self.assertEqual(before, self.states)
            self.quotes.fetch_quote.assert_not_called()


if __name__ == '__main__': unittest.main()
