"""Paper signal data contract; isolated imports, no app, network or real database."""
import copy
import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClosedPaperSignalTests(unittest.TestCase):
    def setUp(self):
        self.config = types.ModuleType('config')
        self.config.CRYPTO_MIN_VOLUME_USDT_24H = 0
        self.config.CRYPTO_MAX_LEVERAGE = 10
        self.config.CRYPTO_BUY_SCORE_THRESHOLD = 66
        self.config.CRYPTO_SL_ATR_MULT = 1.1
        self.config.CRYPTO_MIN_RR_RATIO = 1.4
        self.config.get_crypto_symbol_name = lambda symbol: symbol
        self.config.get_crypto_universe = lambda: [{'symbol': 'BTC'}]
        package = types.ModuleType('analyzer')
        package.indicators = load('paper_test_indicators', ROOT / 'analyzer/indicators.py')
        package.okx_client = types.SimpleNamespace(fetch_candles=Mock(), fetch_ticker=Mock(),
                                                   fetch_recent_trade_pressure=Mock(return_value=None))
        self.patch = patch.dict(sys.modules, {'config': self.config, 'analyzer': package})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.signals = load('paper_test_signals', ROOT / 'analyzer/crypto_signals.py')
        self.okx = package.okx_client
        self.now = 1_793_376_000
        self.now -= self.now % 3600
        starts = [(self.now - (200-i)*3600)*1000 for i in range(201)]
        closes = [100 + i * .1 for i in range(201)]
        self.rows = {'timestamps': starts, 'confirmed': [True]*200 + [False],
                     'opens': closes[:], 'closes': closes[:],
                     'highs': [v+1 for v in closes], 'lows': [v-1 for v in closes],
                     'volumes': [10.]*201, 'quote_volumes': [1000.]*201}
        self.okx.fetch_candles.return_value = self.rows

    def test_copies_all_arrays_and_drops_open_candle(self):
        before = copy.deepcopy(self.rows)
        result = self.signals.confirmed_hourly_candles(self.rows, self.now+20)
        self.assertEqual(self.rows, before)
        for name in self.rows:
            self.assertEqual(len(result[name]), 200)
        self.assertEqual(result['signal_bar_end'], self.now)

    def test_huge_open_bar_does_not_change_paper_signal(self):
        with patch.object(self.signals.time, 'time', return_value=self.now+20):
            initial = self.signals.analyze_crypto_symbol('BTC', closed_only=True)
            for key in ('opens', 'highs', 'lows', 'closes', 'volumes', 'quote_volumes'):
                self.rows[key][-1] = 1e8
            changed = self.signals.analyze_crypto_symbol('BTC', closed_only=True)
        self.assertEqual(initial, changed)
        self.assertIsNotNone(initial)

    def test_missing_confirm_or_quote_volume_blocks(self):
        for key in ('confirmed', 'quote_volumes', 'timestamps'):
            rows = copy.deepcopy(self.rows)
            rows.pop(key)
            self.assertIsNone(self.signals.confirmed_hourly_candles(rows, self.now+20))

    def test_string_zero_is_unconfirmed_and_hole_blocks(self):
        self.rows['confirmed'][100] = '0'
        self.assertIsNone(self.signals.confirmed_hourly_candles(self.rows, self.now+20))

    def test_string_one_is_confirmed(self):
        self.rows['confirmed'] = ['1']*200 + ['0']
        self.assertIsNotNone(self.signals.confirmed_hourly_candles(self.rows, self.now+20))

    def test_future_even_confirmed_bar_is_not_used(self):
        self.rows['confirmed'][-1] = True
        result = self.signals.confirmed_hourly_candles(self.rows, self.now+20)
        self.assertEqual(len(result['closes']), 200)

    def test_stale_duplicate_gap_nonfinite_and_misalignment_rejected(self):
        cases = []
        rows = copy.deepcopy(self.rows); rows['timestamps'] = [v-3_600_000 for v in rows['timestamps']]; cases.append(rows)
        rows = copy.deepcopy(self.rows); rows['timestamps'][55] = rows['timestamps'][54]; cases.append(rows)
        rows = copy.deepcopy(self.rows); rows['closes'][55] = math.nan; cases.append(rows)
        rows = copy.deepcopy(self.rows); rows['quote_volumes'][55] = -1; cases.append(rows)
        rows = copy.deepcopy(self.rows); rows['lows'][55] = rows['highs'][55]+1; cases.append(rows)
        rows = copy.deepcopy(self.rows); rows['opens'].pop(); cases.append(rows)
        rows = copy.deepcopy(self.rows); rows['timestamps'][55] += 1; cases.append(rows)
        for rows in cases:
            self.assertIsNone(self.signals.confirmed_hourly_candles(rows, self.now+20))

    def test_existing_shared_default_does_not_opt_in(self):
        with patch.object(self.signals, 'confirmed_hourly_candles', side_effect=AssertionError('not opted in')):
            result = self.signals.analyze_crypto_symbol('BTC')
        self.assertNotIn('signal_bar_end', result)

    def volume_signal(self, pressure, *, closed_only=True, rsi=75):
        self.rows['quote_volumes'][-2] = 1500.
        self.okx.fetch_recent_trade_pressure.return_value = pressure
        with (patch.object(self.signals.time, 'time', return_value=self.now+20),
              patch.object(self.signals.indicators, 'ema', side_effect=lambda values, period: [110. if period == 20 else 100.]),
              patch.object(self.signals.indicators, 'rsi', return_value=[rsi]),
              patch.object(self.signals.indicators, 'macd', return_value={'macd': [2.], 'signal': [1.]}),
              patch.object(self.signals.indicators, 'bollinger', return_value={'upper': [200.], 'lower': [0.]}),
              patch.object(self.signals.indicators, 'atr', return_value=[2.]),
              patch.object(self.signals.indicators, 'adx', return_value=[70.])):
            return self.signals.analyze_crypto_symbol('BTC', strategy_version='volume_confirmed', closed_only=closed_only)

    def test_paper_volume_requests_strict_pressure_and_preserves_verifiable_sample(self):
        pressure = {'version': 'fresh_trade_pressure_v1', 'ok': True, 'buy_ratio': .6,
                    'trade_count': 2, 'market_timestamp': self.now+19, 'oldest_trade_at': self.now+18,
                    'fetched_at': self.now+20, 'checked_at': self.now+20, 'trades': []}
        result = self.volume_signal(pressure)
        self.okx.fetch_recent_trade_pressure.assert_called_once_with('BTC', limit=300, strict=True)
        self.assertEqual(result['trade_pressure'], pressure)
        self.assertEqual(result['direction'], 'LONG')
        self.assertEqual(result['score'], 73.)
        self.assertTrue(result['volume_confirmed'])
        self.assertTrue(any('2 Trades' in detail for detail in result['details']))

    def test_unknown_pressure_never_confirms_but_does_not_add_a_universal_gate(self):
        pressure = {'version': 'fresh_trade_pressure_v1', 'ok': False, 'reason': 'pressure_stale',
                    'buy_ratio': .99, 'trade_count': 0, 'trades': []}
        result = self.volume_signal(pressure)
        self.assertEqual(result['direction'], 'HALTEN')
        self.assertEqual(result['score'], 68.)
        self.assertFalse(result['volume_confirmed'])
        self.assertIsNone(result['buy_pressure_pct'])
        self.assertTrue(result['volume_filter_blocked'])
        result = self.volume_signal(pressure, rsi=50)
        self.assertEqual(result['direction'], 'LONG')
        self.assertFalse(result['volume_filter_blocked'])

    def test_legacy_volume_keeps_unversioned_pressure_call_and_output(self):
        pressure = {'buy_ratio': .6, 'trade_count': 300}
        result = self.volume_signal(pressure, closed_only=False)
        self.okx.fetch_recent_trade_pressure.assert_called_once_with('BTC', limit=300)
        self.assertNotIn('trade_pressure', result)
        self.assertEqual(result['buy_pressure_pct'], 60.)

    def test_recommendations_forward_only_explicit_opt_in(self):
        with patch.object(self.signals, 'analyze_crypto_symbol', return_value=None) as analyze:
            self.signals.generate_crypto_recommendations()
            analyze.assert_called_once_with('BTC', strategy_version='classic')
            analyze.reset_mock()
            self.signals.generate_crypto_recommendations(closed_only=True)
            analyze.assert_called_once_with('BTC', strategy_version='classic', closed_only=True)


if __name__ == '__main__':
    unittest.main()
