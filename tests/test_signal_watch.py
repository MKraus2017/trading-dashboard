import importlib.util
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

# Avoid importing analyzer.__init__, which starts the legacy DB backup on import.
spec = importlib.util.spec_from_file_location('watch', Path(__file__).parents[1] / 'analyzer/signal_watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class SignalWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.ticket = dict(token='test', inst_id='DOGE-USDC', entry_price=100,
                           stop_loss=98, take_profit=103, max_price_deviation_pct=.75,
                           expires_at=(self.now + timedelta(minutes=5)).isoformat(),
                           status='pending', chat_id='123')

    def test_valid_and_invalid_market_conditions(self):
        reason = lambda price, analysis={'direction':'LONG'}: watch.invalid_reason(self.ticket, {'last':price}, analysis, self.now)
        self.assertIsNone(reason(100))
        self.assertIn('Kursabweichung', reason(101))
        self.assertIn('Stop-Loss', reason(97))
        self.assertIn('Take-Profit', reason(104))
        self.assertIn('kein LONG', reason(100, {'direction':'HALTEN'}))
        self.assertIn('verifizierbar', reason(float('nan')))
        self.assertIn('bestätigen', reason(100, None))

    def test_expiry_includes_already_approved_tickets(self):
        self.ticket['status'] = 'approved'
        result = watch.invalid_reason(self.ticket, now=self.now + timedelta(minutes=6))
        self.assertIn('abgelaufen', result)

    def test_invalidation_delivery_is_persisted_and_retryable(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.executescript('''CREATE TABLE signal_watches(token TEXT PRIMARY KEY, strategy TEXT, reason TEXT, sent_at REAL, lease_until REAL DEFAULT 0);
            CREATE TABLE okx_order_tickets(token TEXT PRIMARY KEY,status TEXT);
            INSERT INTO okx_order_tickets VALUES ('test','approved');''')
        @contextmanager
        def connect():
            with conn:
                yield conn
        with patch.object(watch, 'connection', connect):
            watch.enroll('test', 'classic')
            watch.invalidate('test', 'Testablauf')
            self.assertEqual(conn.execute('SELECT status FROM okx_order_tickets').fetchone()[0], 'expired')
            send = Mock(side_effect=[{'ok':False}, {'ok':True}])
            with patch.object(watch.time, 'time', return_value=1000):
                self.assertFalse(watch.deliver(self.ticket, send, 'fake'))
                self.assertFalse(watch.deliver(self.ticket, send, 'fake'))
            with patch.object(watch.time, 'time', return_value=1121):
                self.assertTrue(watch.deliver(self.ticket, send, 'fake'))
                self.assertFalse(watch.deliver(self.ticket, send, 'fake'))
            self.assertEqual(send.call_count, 2)
            self.assertIn('keine Verkaufsanweisung', send.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
