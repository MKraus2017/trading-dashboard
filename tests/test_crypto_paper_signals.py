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

    def test_recommendations_forward_only_explicit_opt_in(self):
        with patch.object(self.signals, 'analyze_crypto_symbol', return_value=None) as analyze:
            self.signals.generate_crypto_recommendations()
            analyze.assert_called_once_with('BTC', strategy_version='classic')
            analyze.reset_mock()
            self.signals.generate_crypto_recommendations(closed_only=True)
            analyze.assert_called_once_with('BTC', strategy_version='classic', closed_only=True)


if __name__ == '__main__':
    unittest.main()
