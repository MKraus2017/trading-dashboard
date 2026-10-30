"""Independent owner-1 exit-quote review; isolated fixtures, no external IO."""
import copy
import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IndependentQuoteGuardTests(unittest.TestCase):
    def setUp(self):
        self.guards = []
        for target in ('urllib.request.urlopen', 'socket.create_connection', 'socket.socket.connect', 'subprocess.Popen'):
            guard = patch(target, side_effect=AssertionError('External IO forbidden in quote-guard tests'))
            guard.start()
            self.addCleanup(guard.stop)
        harness = load('independent_quote_fixture', ROOT / 'tests/test_crypto_watch.py')
        self.f = harness.CryptoWatchTests('test_parallel_stop_checks_close_once_at_observed_price')
        self.addCleanup(self.f.doCleanups)
        temp_factory = tempfile.TemporaryDirectory
        with patch.object(harness.tempfile, 'TemporaryDirectory',
                          side_effect=lambda: temp_factory(prefix='.quote-guard-', dir=ROOT)):
            self.f.setUp()

    def test_later_slow_symbol_cannot_make_expired_first_mark_look_healthy(self):
        f = self.f
        f.open('BTC')
        f.open('ETH')
        initial_now = f.now
        initial_success = initial_now - 10
        f.p['monitor_success_at'] = initial_success
        initial_cash = f.p['cash']
        def ticker(symbol):
            if symbol == 'BTC':
                return {'last': 100., 'market_timestamp': initial_now}
            f.now += 61
            return {'last': 100., 'market_timestamp': f.now}
        f.okx.fetch_ticker.side_effect = ticker
        result = f.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result['events'], [])
        self.assertEqual(f.p['cash'], initial_cash)
        self.assertEqual(len(f.p['positions']), 2)
        self.assertIn('BTC', result['missing_symbols'])
        self.assertEqual(result['quote_errors']['BTC'], 'stale_quote')
        self.assertEqual(f.p['monitor_success_at'], initial_success)
        self.assertFalse(f.watch.status(f.p, now=f.now)['healthy'])

    def test_quote_expiring_during_position_calculation_does_not_leave_trailing_update(self):
        f = self.f
        f.open()
        original = copy.deepcopy(f.p['positions'][0])
        original_cash = f.p['cash']
        f.okx.fetch_ticker.return_value = f.quote(104.)
        calculate = f.cp._pnl_pct
        def delayed_calculation(*args):
            result = calculate(*args)
            f.now += 61
            return result
        with patch.object(f.cp, '_pnl_pct', side_effect=delayed_calculation):
            result = f.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(f.p['positions'], [original])
        self.assertEqual(f.p['cash'], original_cash)
        self.assertEqual(f.p['trades'], [])
        self.assertEqual(result['quote_errors'], {'BTC': 'stale_quote'})

    def test_invalid_stop_quote_cannot_close_but_other_valid_position_still_can(self):
        f = self.f
        f.open('BTC')
        f.open('ETH')
        btc_original = copy.deepcopy(f.p['positions'][0])
        before_cash = f.p['cash']
        f.okx.fetch_ticker.side_effect = lambda symbol: {'last': 90., 'market_timestamp': f.now-61 if symbol == 'BTC' else f.now}
        result = f.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual([event['symbol'] for event in result['events']], ['ETH'])
        self.assertEqual(f.p['positions'], [btc_original])
        self.assertEqual([trade['symbol'] for trade in f.p['trades']], ['ETH'])
        self.assertEqual(f.p['trades'][0]['pnl_eur'], -20.)
        self.assertEqual(f.p['cash'], before_cash+80.)
        self.assertEqual(result['quote_errors'], {'BTC': 'stale_quote'})

    def test_owner2_exit_behavior_is_unchanged_for_timestamp_absent_ticker(self):
        f = self.f
        f.cp.open_position(2, 'BTC', 'LONG', 100., 2, 100., 95., 110.)
        f.okx.fetch_ticker.return_value = {'last': 90.}
        result = f.cp.evaluate_crypto_portfolio(2, background=True)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(f.p['cash'], 980.)
        self.assertNotIn('quote_errors', result)
        self.assertNotIn('monitor_quote_policy', f.p)
        self.assertNotIn('last_quote_at', f.p['trades'][0])

    def test_legacy_timezone_aware_entry_is_supported_without_rewriting_it(self):
        f = self.f
        f.open()
        original_open = f.p['positions'][0]['opened_at']
        del f.p['positions'][0]['opened_at_ts']
        # Fixture wall-clock ISO and mocked time may differ slightly; the quote
        # must be demonstrably after the stored legacy timestamp.
        from datetime import datetime
        f.now = max(f.now, datetime.fromisoformat(original_open).timestamp()) + 1
        f.okx.fetch_ticker.return_value = f.quote(100.)
        result = f.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(result['quote_errors'], {})
        self.assertEqual(f.p['positions'][0]['opened_at'], original_open)
        self.assertNotIn('opened_at_ts', f.p['positions'][0])
        self.assertEqual(f.p['positions'][0]['last_quote_at'], f.now)

    def test_closed_trade_carries_exact_quote_evidence_without_repricing_history(self):
        f = self.f
        f.p['trades'] = [{'id': 'legacy', 'pnl_eur': 12.34, 'close_reason': 'historic'}]
        historic = copy.deepcopy(f.p['trades'])
        f.open()
        f.now += 1
        f.okx.fetch_ticker.return_value = f.quote(90.)
        result = f.cp.evaluate_crypto_portfolio(1, background=True)
        self.assertEqual(f.p['trades'][:len(historic)], historic)
        self.assertEqual(f.p['trades'][-1]['entry_price'], 100.)
        self.assertEqual(f.p['trades'][-1]['exit_price'], 90.)
        self.assertEqual(f.p['trades'][-1]['last_quote_at'], f.now)
        self.assertEqual(f.p['trades'][-1]['pnl_eur'], -20.)
        self.assertEqual(result['quote_errors'], {})

    def test_research_quote_evidence_is_allowlisted_without_unknown_keys(self):
        api = load('independent_quote_research', ROOT / 'analyzer/astra_research_api.py')
        value = {'monitor_quote_policy': 'fresh_exit_quotes_v1',
                 'monitor_quote_errors': {'BTC': 'stale_quote', 'secret_key': 'SECRET'},
                 'positions': [{'id': 'p', 'last_quote_at': 1800000000., 'private_key': 'SECRET'}]}
        result = api._project(value, api.REFERENCE)
        self.assertEqual(result['monitor_quote_errors'], {'BTC': 'stale_quote'})
        self.assertEqual(result['positions'], [{'id': 'p', 'last_quote_at': 1800000000.}])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_status_expires_between_ticks_without_mutating_ledger_or_stored_errors(self):
        f = self.f
        current = f.now + 100
        p = {'cash': 1234., 'trades': [{'id': 'historic'}], 'value_history': [{'value': 1234.}],
             'paper_execution_policy': {'version': 'original', 'rule_hash': 'unchanged'},
             'monitor_success_at': current-1, 'monitor_quote_policy': 'fresh_exit_quotes_v1',
             'monitor_quote_errors': {}, 'monitor_missing_symbols': [],
             'positions': [{'symbol': 'BTC', 'last_quote_at': current-60},
                           {'symbol': 'ETH', 'last_quote_at': current-10}]}
        before = copy.deepcopy(p)
        self.assertTrue(f.watch.status(p, now=current)['healthy'])
        result = f.watch.status(p, now=current+.01)
        self.assertFalse(result['healthy'])
        self.assertEqual(result['quote_errors'], {'BTC': 'stale_quote'})
        self.assertEqual(result['missing_symbols'], ['BTC'])
        self.assertGreaterEqual(result['oldest_quote_age_seconds'], 60)
        self.assertEqual(p, before)
        # Result dictionaries/lists must not alias the persistent ledger.
        result['quote_errors']['ETH'] = 'injected'
        result['missing_symbols'].append('ETH')
        self.assertEqual(p, before)

    def test_status_new_policy_requires_quote_evidence_but_legacy_scope_does_not(self):
        f = self.f
        legacy = {'monitor_success_at': f.now-1, 'positions': [{'symbol': 'BTC'}]}
        self.assertTrue(f.watch.status(legacy, now=f.now)['healthy'])
        guarded = dict(legacy, monitor_quote_policy='fresh_exit_quotes_v1')
        result = f.watch.status(guarded, now=f.now)
        self.assertFalse(result['healthy'])
        self.assertEqual(result['quote_errors'], {'BTC': 'missing_timestamp'})
        guarded['positions'] = [{'symbol': 'BTC', 'last_quote_at': f.now+5}]
        self.assertTrue(f.watch.status(guarded, now=f.now)['healthy'])
        guarded['positions'][0]['last_quote_at'] += .01
        result = f.watch.status(guarded, now=f.now)
        self.assertFalse(result['healthy'])
        self.assertEqual(result['quote_errors'], {'BTC': 'future_quote'})


if __name__ == '__main__':
    unittest.main()
