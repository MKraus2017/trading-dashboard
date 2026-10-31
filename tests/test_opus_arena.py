"""Offline Opus arena tests: temp DB, mocked market data, no app bootstrap."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
B = 7200
spec = importlib.util.spec_from_file_location('opus_test', ROOT / 'analyzer/opus_arena.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


def okx_rows(closes, end, opens=None, highs=None, lows=None):
    """Newest-first OKX rows for completed candles ending at `end`."""
    n = len(closes)
    rows = []
    for i, c in enumerate(closes):
        op = opens[i] if opens else c
        hi = highs[i] if highs else max(op, c) + 0.1
        lo = lows[i] if lows else min(op, c) - 0.1
        rows.append([int((end - (n - i) * B) * 1000), str(op), str(hi), str(lo), str(c), '1', '1', '100', '1'])
    return list(reversed(rows))


def pullback_closes():
    # Steady uptrend, sharp 5-candle dip (RSI collapses), then green reversal.
    closes = [100 + 0.25 * i for i in range(394)]
    top = closes[-1]
    closes += [top - 3 * k for k in range(1, 6)]
    return closes  # 399; reversal candle appended by caller


class OpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.db'
        self.now = 1800000000 // B * B + 600
        self.p = dict(cash=1000, positions=[], trades=[], start_capital=1000)
        self.astra = dict(version='astra_breakout_2h_volume_v3', started_at=self.now - 86400 * 10,
                          version_started_at=self.now - 86400, equity=1200)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript("""
              CREATE TABLE crypto_portfolios(user_id INTEGER PRIMARY KEY,data TEXT);
              CREATE TABLE settings(user_id INTEGER PRIMARY KEY,crypto_strategy_version TEXT);
              INSERT INTO settings VALUES(1,'volume_confirmed');
              CREATE TABLE astra_ultra_runs(user_id INTEGER, version TEXT, data TEXT, PRIMARY KEY(user_id,version));""")
        self.persist()
        o.init_schema(self.path)
        self.signal_mode = 'none'
        self.bars = Mock(side_effect=lambda sym: self.candles())
        self.price = 102.0
        self.quote = Mock(side_effect=lambda sym: dict(price=self.price, market_at=self.now))

    def persist(self):
        self.p['monitor_success_at'] = self.now
        self.astra['monitor_success_at'] = self.now
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('INSERT OR REPLACE INTO crypto_portfolios VALUES(1,?)', (json.dumps(self.p),))
            c.execute('INSERT OR REPLACE INTO astra_ultra_runs VALUES(1,?,?)',
                      (self.astra['version'], json.dumps(self.astra)))

    def candles(self):
        end = int(self.now // B) * B
        if self.signal_mode == 'pullback':
            closes = pullback_closes()
            prev = closes[-1]
            closes.append(prev + 4)
            opens = [closes[0]] + closes[:-1]
            opens[-1] = prev
            return okx_rows(closes, end, opens=opens)
        return okx_rows([100.0] * 400, end)

    def cycle(self):
        return o.cycle(self.path, bars=self.bars, quote=self.quote, clock=lambda: self.now)

    def next_bar(self, offset=10):
        self.now = int(self.now // B) * B + B + offset
        self.persist()

    def stored_tables(self):
        with closing(sqlite3.connect(self.path)) as c:
            return (c.execute('SELECT data FROM crypto_portfolios').fetchall(),
                    c.execute('SELECT data FROM astra_ultra_runs').fetchall())

    # ---------------------------------------------------------------- seeding
    def test_seed_is_mean_of_krypto_and_astra(self):
        self.p['positions'] = [dict(id='x', margin_eur=100, leverage=2, entry_price=100, last_price=110, unrealized_eur=20)]
        self.p['cash'] = 900
        self.persist()
        s = self.cycle()
        self.assertAlmostEqual(s['initial_capital'], (1020 + 1200) / 2)
        self.assertEqual(s['cash'], s['initial_capital'])
        self.assertEqual(s['seed']['krypto_equity'], 1020)
        self.assertEqual(s['seed']['astra_equity'], 1200)
        self.bars.assert_not_called()  # seeding never trades the current boundary

    def test_waits_when_astra_missing_or_stale(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('DELETE FROM astra_ultra_runs')
        self.assertIsNone(self.cycle())
        self.persist()
        self.astra['monitor_success_at'] = self.now - 500
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('UPDATE astra_ultra_runs SET data=?', (json.dumps(self.astra),))
        self.assertIsNone(self.cycle())
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM opus_arena_runs').fetchone()[0], 0)

    def test_picks_active_astra_version(self):
        old = dict(version='astra_breakout_2h_v2', started_at=self.now - 86400 * 10,
                   version_started_at=self.now - 86400 * 5, equity=50, monitor_success_at=self.now)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('INSERT INTO astra_ultra_runs VALUES(1,?,?)', (old['version'], json.dumps(old)))
        s = self.cycle()
        self.assertEqual(s['seed']['astra_equity'], 1200)

    def test_never_writes_competitor_tables(self):
        before = self.stored_tables()
        self.cycle()
        self.assertEqual(self.stored_tables(), before)
        self.next_bar()
        self.signal_mode = 'pullback'
        self.price = pullback_closes()[-1] + 4
        before = self.stored_tables()
        s = self.cycle()
        self.assertTrue(s['positions'])
        self.assertEqual(self.stored_tables(), before)

    # ---------------------------------------------------------------- signal
    def test_pullback_signal_definition(self):
        self.signal_mode = 'pullback'
        self.now = int(self.now // B) * B + 10
        sig = o.signal(self.candles(), self.now)
        self.assertTrue(sig['trend_ok'])
        self.assertTrue(sig['pullback_ok'], sig['rsi_pullback_min'])
        self.assertTrue(sig['reversal_ok'])
        self.assertTrue(sig['enter'])
        self.assertFalse(sig['exit'])

    def test_flat_market_no_signal(self):
        self.now = int(self.now // B) * B + 10
        sig = o.signal(self.candles(), self.now)
        self.assertFalse(sig['enter'])

    def test_rejects_stale_or_gappy_bars(self):
        self.now = int(self.now // B) * B + 10
        rows = self.candles()
        with self.assertRaises(ValueError):
            o.signal(rows, self.now + 1000)
        with self.assertRaises(ValueError):
            o.signal(rows[:5] + rows[6:], self.now)

    # ---------------------------------------------------------------- trading
    def enter(self):
        self.cycle()
        self.next_bar()
        self.signal_mode = 'pullback'
        closes = pullback_closes()
        self.price = closes[-1] + 4
        return self.cycle()

    def test_entry_sized_by_risk_and_costs(self):
        s = self.enter()
        self.assertEqual(len(s['positions']), 3)
        for pos in s['positions']:
            self.assertLessEqual(pos['planned_risk'], s['initial_capital'] * 0.005 + 1e-9)
            self.assertLessEqual(pos['margin_eur'], s['initial_capital'] * 0.40 + 1e-9)
            self.assertLess(pos['stop_loss'], pos['entry_price'])
        self.assertLessEqual(sum(p['planned_risk'] for p in s['positions']), s['initial_capital'] * 0.015 + 1e-9)
        self.assertGreater(s['fees_paid'], 0)
        self.assertLess(s['equity'], s['initial_capital'])

    def test_no_entry_if_one_symbol_missing(self):
        self.cycle()
        self.next_bar()
        self.signal_mode = 'pullback'
        self.price = pullback_closes()[-1] + 4
        self.bars.side_effect = lambda sym: (_ for _ in ()).throw(OSError('down')) if sym == 'SOL' else self.candles()
        s = self.cycle()
        self.assertEqual(s['positions'], [])
        self.assertIn('wiederholt', s['candle_error'])
        self.assertIsNotNone(s['pending'])

    def test_stop_and_trailing_stop_exit(self):
        s = self.enter()
        btc = next(p for p in s['positions'] if p['symbol'] == 'BTC')
        # Price rallies, monitor records peak; next candle trails stop upward.
        self.now += 30
        self.price = btc['entry_reference'] * 1.10
        self.persist()
        self.cycle()
        self.next_bar()
        self.signal_mode = 'none'
        # flat 100 candles would trigger EMA200 exit; keep pullback candles but no reversal
        self.signal_mode = 'pullback'
        s = self.cycle()
        btc = next(p for p in s['positions'] if p['symbol'] == 'BTC')
        self.assertGreater(btc['stop_loss'], btc['initial_stop'])
        self.now += 30
        self.price = btc['stop_loss'] * 0.999
        self.persist()
        s = self.cycle()
        self.assertEqual(s['positions'], [])
        self.assertTrue(all(t['close_reason'] == 'Trailing-Stop' for t in s['trades']))

    def test_trend_break_exit(self):
        self.enter()
        self.next_bar()
        self.signal_mode = 'none'  # flat candles at 100: close below EMA200
        self.price = 100.0
        s = self.cycle()
        self.assertEqual(s['positions'], [])
        self.assertTrue(all(t['close_reason'] in ('Stop-Loss', 'Trend gebrochen: Schluss unter EMA200') for t in s['trades']))

    def test_missed_window_counted_never_backfilled(self):
        self.cycle()
        self.next_bar(offset=ENTRY_LATE)
        self.signal_mode = 'pullback'
        s = self.cycle()
        self.assertEqual(s['positions'], [])
        self.assertEqual(s['missed_bars'], 1)
        self.bars.assert_not_called()

    # ---------------------------------------------------------------- scoreboard
    def test_leaderboard_since_opus_start(self):
        self.cycle()
        self.now += 30
        self.p['cash'] = 1100
        self.astra['equity'] = 1080
        self.persist()
        self.cycle()
        r = o.report(self.path, 1, now=self.now)
        board = {row['key']: row for row in r['leaderboard']}
        self.assertAlmostEqual(board['krypto']['return_pct'], 10)
        self.assertAlmostEqual(board['astra']['return_pct'], -10)
        self.assertAlmostEqual(board['opus']['return_pct'], 0)
        self.assertEqual([row['key'] for row in r['leaderboard']], ['krypto', 'opus', 'astra'])

    def test_krypto_reset_invalidates_comparison(self):
        self.cycle()
        self.now += 30
        self.p['start_capital'] = 5000
        self.persist()
        self.cycle()
        r = o.report(self.path, 1, now=self.now)
        self.assertEqual(r['leaderboard'][-1]['key'], 'krypto')
        self.assertFalse(r['leaderboard'][-1]['valid'])

    def test_report_read_only_and_isolated(self):
        r = o.report(self.path, 1, now=self.now)
        self.assertFalse(r['started'])
        self.cycle()
        self.assertTrue(o.report(self.path, 1, now=self.now)['started'])
        self.assertFalse(o.report(self.path, 2, now=self.now)['started'])

    def test_rule_change_requires_new_version(self):
        s = self.cycle()
        s['rules']['risk_pct'] = 0.05
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('UPDATE opus_arena_runs SET data=?', (json.dumps(s),))
        self.now += 30
        self.persist()
        with self.assertRaises(ValueError):
            self.cycle()


ENTRY_LATE = o.ENTRY_WINDOW + 60

if __name__ == '__main__':
    unittest.main()
