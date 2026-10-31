"""Owner-paper trade-pressure contract; public API mocked, no app or database."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("strict_pressure_okx", ROOT / "analyzer/okx_client.py")
okx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(okx)


class StrictTradePressureTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.
        okx._strict_trade_pressure_cache.clear()
        okx._trade_pressure_cache.clear()
        clock = patch.object(okx.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def row(self, trade_id="1", age=1., side="buy", price="100", size="2", **changes):
        return dict(instId="BTC-USDT", tradeId=trade_id, ts=str((self.now-age)*1000),
                    side=side, px=price, sz=size, **changes)

    def fetch(self, rows):
        okx._strict_trade_pressure_cache.clear()
        with patch.object(okx, "_http_get", return_value={"data": rows}):
            return okx.fetch_recent_trade_pressure("BTC", strict=True)

    def test_quote_weighting_and_exchange_times_with_sparse_sample_visible(self):
        result = self.fetch([self.row(), self.row("2", age=2, side="sell", price="50")])
        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(result["buy_ratio"], 2/3)
        self.assertEqual(result["trade_count"], 2)
        self.assertEqual(result["market_timestamp"], self.now-1)
        self.assertEqual(result["oldest_trade_at"], self.now-2)
        self.assertEqual(result["fetched_at"], self.now)
        sparse = self.fetch([self.row()])
        self.assertTrue(sparse["ok"])
        self.assertEqual(sparse["trade_count"], 1)
        self.assertEqual(sparse["buy_ratio"], 1.)

    def test_stale_future_missing_time_and_empty_response_are_unknown(self):
        missing = self.row()
        missing.pop("ts")
        cases = [([self.row(age=61)], "pressure_stale"),
                 ([self.row(age=301)], "pressure_no_recent_trades"),
                 ([self.row(age=-5.001)], "pressure_trade_future"),
                 ([missing], "pressure_timestamp_invalid"),
                 ([], "pressure_no_recent_trades"), (None, "pressure_response_invalid")]
        for rows, reason in cases:
            with self.subTest(reason=reason):
                result = self.fetch(rows)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], reason)
                self.assertIsNone(result["buy_ratio"])
        self.assertTrue(self.fetch([self.row(age=60)])["ok"])
        self.assertTrue(self.fetch([self.row(age=-5)])["ok"])

    def test_malformed_nonfinite_negative_overflow_and_wrong_instrument_fail_closed(self):
        changes = [("px", "nan"), ("px", "inf"), ("px", 0), ("px", -1), ("px", True),
                   ("sz", "nan"), ("sz", "inf"), ("sz", 0), ("sz", -.1), ("sz", False),
                   ("ts", "nan"), ("ts", "inf"), ("ts", -1), ("ts", True),
                   ("side", "unknown"), ("instId", "ETH-USDT"), ("tradeId", ""),
                   ("tradeId", None)]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                row = self.row("2")
                row[field] = value
                result = self.fetch([self.row(), row])
                self.assertFalse(result["ok"])
                self.assertIsNone(result["buy_ratio"])
        overflow = self.row(price="1e308", size="1e308")
        self.assertEqual(self.fetch([overflow])["reason"], "pressure_notional_invalid")
        self.assertFalse(self.fetch([self.row(), None])["ok"])
        # Malformed old rows may not be silently removed to produce confirmation.
        old_bad = self.row("2", age=400, size="-1")
        self.assertFalse(self.fetch([self.row(), old_bad])["ok"])

    def test_duplicate_ids_are_counted_once_and_conflicts_invalidate_sample(self):
        buy, sell = self.row(), self.row("2", side="sell", price="50")
        result = self.fetch([buy, sell, copy.deepcopy(buy)])
        self.assertTrue(result["ok"])
        self.assertEqual(result["trade_count"], 2)
        self.assertAlmostEqual(result["buy_ratio"], 2/3)
        conflict = {**buy, "sz": "3"}
        self.assertEqual(self.fetch([buy, conflict])["reason"], "pressure_conflicting_duplicate")

    def test_cached_rows_age_out_and_ratio_is_recalculated_without_new_fetch(self):
        rows = [self.row(age=299), self.row("2", side="sell", price="50")]
        with patch.object(okx, "_http_get", return_value={"data": rows}) as http:
            first = okx.fetch_recent_trade_pressure("BTC", strict=True)
            self.now += 2
            second = okx.fetch_recent_trade_pressure("BTC", strict=True)
        http.assert_called_once()
        self.assertEqual(first["trade_count"], 2)
        self.assertEqual(second["trade_count"], 1)
        self.assertEqual(second["buy_ratio"], 0.)
        self.assertEqual(second["fetched_at"], first["fetched_at"])
        self.assertEqual(second["checked_at"], first["checked_at"]+2)

    def test_cached_market_time_can_expire_before_cache_ttl(self):
        rows = [self.row(age=59)]
        with patch.object(okx, "_http_get", return_value={"data": rows}) as http:
            self.assertTrue(okx.fetch_recent_trade_pressure("BTC", strict=True)["ok"])
            self.now += 2
            result = okx.fetch_recent_trade_pressure("BTC", strict=True)
        http.assert_called_once()
        self.assertEqual(result["reason"], "pressure_stale")

    def test_cache_expiry_fetches_again_and_returned_data_cannot_mutate_raw_cache(self):
        with patch.object(okx, "_http_get", side_effect=lambda path: {"data": [self.row()]}) as http:
            first = okx.fetch_recent_trade_pressure("BTC", strict=True)
            first["trades"][0]["size"] = -100
            self.assertTrue(okx.fetch_recent_trade_pressure("BTC", strict=True)["ok"])
            self.now += 30
            result = okx.fetch_recent_trade_pressure("BTC", strict=True)
        self.assertEqual(http.call_count, 2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched_at"], self.now)

    def test_pure_commit_revalidation_expires_rows_and_ignores_claimed_aggregates(self):
        sample = self.fetch([self.row(age=299), self.row("2", side="sell", price="50")])
        before = copy.deepcopy(sample)
        sample["buy_ratio"] = -999
        current = okx.revalidate_trade_pressure(sample, "BTC", self.now+2)
        self.assertEqual(current["buy_ratio"], 0.)
        self.assertEqual(current["trade_count"], 1)
        self.assertEqual(sample["trades"], before["trades"])
        self.assertEqual(okx.revalidate_trade_pressure(sample, "ETH", self.now)["reason"],
                         "pressure_instrument_mismatch")
        self.assertFalse(okx.revalidate_trade_pressure(sample, "BTC", self.now+62)["ok"])
        for invalid in (None, {}, {**sample, "version": "unknown"}):
            self.assertFalse(okx.revalidate_trade_pressure(invalid, "BTC", self.now)["ok"])

    def test_legacy_default_retains_old_parser_result_and_its_separate_cache(self):
        # Existing consumers intentionally retain their original unversioned contract.
        rows = [{"px": "100", "sz": "2", "side": "buy"},
                {"px": "50", "sz": "2", "side": "sell"}]
        with patch.object(okx, "_http_get", return_value={"data": rows}) as http:
            legacy = okx.fetch_recent_trade_pressure("BTC")
            strict = okx.fetch_recent_trade_pressure("BTC", strict=True)
            cached = okx.fetch_recent_trade_pressure("BTC")
        self.assertEqual(http.call_count, 2)
        self.assertEqual(legacy, {"buy_ratio": 2/3, "buy_quote": 200., "sell_quote": 100., "trade_count": 2})
        self.assertEqual(cached, legacy)
        self.assertFalse(strict["ok"])


if __name__ == "__main__":
    unittest.main()
