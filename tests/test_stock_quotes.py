"""Offline stock reference quote tests: no app, DB, network or notifications."""
import copy
import importlib.util
import math
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stock_quotes_test", ROOT / "analyzer/stock_quotes.py")
quotes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quotes)


class StockQuoteTests(unittest.TestCase):
    def setUp(self):
        quotes.clear_cache()
        self.now = 1800000000.0
        self.meta = {
            "symbol": "SAP.DE", "instrumentType": "EQUITY", "currency": "EUR",
            "regularMarketPrice": 100.0, "regularMarketTime": self.now - 10,
            "currentTradingPeriod": {"regular": {"start": self.now - 3600,
                                                  "end": self.now + 3600}},
        }
        self.http = patch.object(quotes.requests, "get").start()
        self.addCleanup(patch.stopall)
        self.http.return_value = self.response(self.meta)

    @staticmethod
    def response(meta):
        response = Mock()
        response.json.return_value = {"chart": {"result": [{"meta": copy.deepcopy(meta)}], "error": None}}
        return response

    def fx(self, currency="USD", age=30, rate=1.25):
        return self.response({"symbol": "EUR" + currency + "=X", "currency": currency,
                              "regularMarketPrice": rate, "regularMarketTime": self.now - age})

    def history_response(self, days_back=(3, 2, 1, 0), currency="EUR", symbol="SAP.DE"):
        from datetime import datetime, timezone
        session_date = datetime.fromtimestamp(self.now - 3600, timezone.utc).date()
        midnight = datetime.combine(session_date, datetime.min.time(), timezone.utc).timestamp()
        data = {"meta": {"symbol": symbol, "instrumentType": "EQUITY", "currency": currency},
                "timestamp": [midnight - day * 86400 for day in days_back],
                "indicators": {"quote": [{"open": [100.0] * len(days_back),
                                           "high": [102.0] * len(days_back),
                                           "low": [99.0] * len(days_back),
                                           "close": [101.0] * len(days_back)}]}}
        response = Mock()
        response.json.return_value = {"chart": {"result": [data], "error": None}}
        return response

    def get(self, **kwargs):
        self.http.return_value = self.response(self.meta)
        return quotes.fetch_quote("SAP.DE", now=kwargs.get("now", self.now))

    def test_fresh_eur_quote_and_http_bounds(self):
        result = self.get()
        self.assertEqual(result["price"], 100)
        self.assertTrue(result["tradable"])
        self.assertTrue(result["market_open"])
        self.assertEqual(result["market_at"], self.now - 10)
        self.assertEqual(result["received_at"], self.now)
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.http.call_args.kwargs["timeout"], 6)
        self.assertEqual(self.http.call_args.kwargs["params"], {"interval": "1m", "range": "1d"})

    def test_foreign_currency_divides_by_eurxxx(self):
        self.meta["currency"] = "USD"
        self.http.side_effect = [self.response(self.meta), self.fx()]
        result = quotes.fetch_quote("SAP.DE", now=self.now)
        self.assertEqual(result["price"], 80)
        self.assertEqual(result["source_currency"], "USD")
        self.assertEqual(result["currency"], "EUR")
        self.assertEqual(result["fx_market_at"], self.now - 30)
        self.assertEqual(self.http.call_count, 2)

    def test_chf_and_gbp_conversions(self):
        for currency in ("CHF", "GBP"):
            with self.subTest(currency=currency):
                quotes.clear_cache()
                self.meta["currency"] = currency
                self.http.side_effect = [self.response(self.meta), self.fx(currency, rate=2)]
                self.assertEqual(quotes.fetch_quote("SAP.DE", now=self.now)["price"], 50)

    def test_unsupported_currency_and_pence_fail_closed(self):
        for currency in ("JPY", "GBp", None, {"bad": "currency"}):
            with self.subTest(currency=currency):
                quotes.clear_cache()
                self.meta["currency"] = currency
                with self.assertRaisesRegex(ValueError, "unsupported_currency"):
                    self.get()

    def test_stale_quote_and_future_quote_fail_closed(self):
        for age, message in ((1201, "stale_quote"), (-61, "future_quote")):
            with self.subTest(age=age):
                quotes.clear_cache()
                self.meta["regularMarketTime"] = self.now - age
                with self.assertRaisesRegex(ValueError, message):
                    self.get()

    def test_age_and_future_tolerance_boundaries(self):
        for age in (1200, -60):
            quotes.clear_cache()
            self.meta["regularMarketTime"] = self.now - age
            self.assertTrue(self.get()["tradable"])

    def test_fx_age_currency_and_nonfinite_fail_closed(self):
        self.meta["currency"] = "USD"
        for fx, message in ((self.fx(age=86401), "stale_fx"),
                            (self.fx(age=-61), "future_fx"),
                            (self.fx(rate=math.nan), "invalid_fx_rate"),
                            (self.fx(rate=0), "invalid_fx_rate")):
            with self.subTest(message=message):
                quotes.clear_cache()
                self.http.side_effect = [self.response(self.meta), fx]
                with self.assertRaisesRegex(ValueError, message):
                    quotes.fetch_quote("SAP.DE", now=self.now)
        quotes.clear_cache()
        wrong_currency = self.fx()
        wrong_currency.json.return_value["chart"]["result"][0]["meta"]["currency"] = "CHF"
        self.http.side_effect = [self.response(self.meta), wrong_currency]
        with self.assertRaisesRegex(ValueError, "fx_currency_mismatch"):
            quotes.fetch_quote("SAP.DE", now=self.now)

    def test_invalid_prices_and_times_rejected(self):
        for field, values in (("regularMarketPrice", (math.nan, math.inf, -1, 0, True, None)),
                              ("regularMarketTime", (math.nan, math.inf, 0, True, None))):
            for value in values:
                with self.subTest(field=field, value=value):
                    quotes.clear_cache()
                    meta = copy.deepcopy(self.meta)
                    meta[field] = value
                    self.http.return_value = self.response(meta)
                    with self.assertRaises(ValueError):
                        quotes.fetch_quote("SAP.DE", now=self.now)

    def test_weekend_record_does_not_fetch_stale_fx_or_allow_fill(self):
        self.meta["currency"] = "USD"
        self.meta["regularMarketTime"] = self.now - 2 * 86400
        self.meta["currentTradingPeriod"]["regular"] = {
            "start": self.now - 2 * 86400 - 6 * 3600, "end": self.now - 2 * 86400 + 1}
        result = self.get()
        self.assertFalse(result["market_open"])
        self.assertFalse(result["tradable"])
        self.assertIsNone(result["price"])
        self.assertEqual(result["reason"], "market_closed")
        self.assertEqual(result["data_reason"], "stale_quote")
        self.assertEqual(self.http.call_count, 1)

    def test_market_closed_fresh_price_can_be_displayed_not_filled(self):
        self.meta["currentTradingPeriod"]["regular"]["end"] = self.now - 5
        result = self.get()
        self.assertEqual(result["price"], 100)
        self.assertFalse(result["tradable"])
        self.assertEqual(result["reason"], "market_closed")

    def test_closed_market_stale_fx_never_produces_display_price(self):
        self.meta["currency"] = "USD"
        self.meta["currentTradingPeriod"]["regular"]["end"] = self.now - 5
        self.http.side_effect = [self.response(self.meta), self.fx(age=86401)]
        result = quotes.fetch_quote("SAP.DE", now=self.now)
        self.assertIsNone(result["price"])
        self.assertEqual(result["data_reason"], "stale_fx")
        self.assertEqual(result["reason"], "market_closed")

    def test_quote_from_previous_session_is_not_tradable(self):
        self.meta["currentTradingPeriod"]["regular"]["start"] = self.now - 5
        result = self.get()
        self.assertTrue(result["market_open"])
        self.assertFalse(result["tradable"])
        self.assertEqual(result["reason"], "previous_regular_session")

    def test_cache_revalidates_quote_age_and_market_session(self):
        self.meta["regularMarketTime"] = self.now - 1190
        first = self.get()
        first["price"] = 666
        self.assertEqual(self.get()["price"], 100)
        with self.assertRaisesRegex(ValueError, "stale_quote"):
            self.get(now=self.now + 20)
        self.assertEqual(self.http.call_count, 1)
        quotes.clear_cache()
        self.meta["regularMarketTime"] = self.now - 1
        self.meta["currentTradingPeriod"]["regular"]["end"] = self.now + 10
        self.assertTrue(self.get()["tradable"])
        self.assertFalse(self.get(now=self.now + 11)["tradable"])

    def test_cache_expires_at_sixty_seconds_and_never_falls_back(self):
        self.get()
        self.assertTrue(self.get(now=self.now + 59)["tradable"])
        self.assertEqual(self.http.call_count, 1)
        self.http.side_effect = quotes.requests.Timeout("offline")
        with self.assertRaisesRegex(ValueError, "quote_fetch_failed"):
            self.get(now=self.now + 60)
        self.assertEqual(self.http.call_count, 2)
        self.assertNotIn("SAP.DE", quotes._cache)

    def test_missing_or_invalid_session_symbol_and_instrument(self):
        for change, error in (({"currentTradingPeriod": {}}, "invalid_regular_start"),
                              ({"currentTradingPeriod": []}, "invalid_regular_start"),
                              ({"symbol": "NVDA"}, "quote_symbol_mismatch"),
                              ({"instrumentType": "CRYPTOCURRENCY"}, "unsupported_instrument")):
            quotes.clear_cache()
            meta = dict(self.meta, **change)
            self.http.return_value = self.response(meta)
            with self.assertRaisesRegex(ValueError, error):
                quotes.fetch_quote("SAP.DE", now=self.now)

    def test_malformed_payload_and_no_candle_close_fallback(self):
        self.http.return_value.json.return_value = {"chart": {"result": None, "error": {"code": "Not Found"}}}
        with self.assertRaisesRegex(ValueError, "missing_chart_result"):
            quotes.fetch_quote("SAP.DE", now=self.now)
        self.assertNotIn("SAP.DE", quotes._cache)
        self.meta.pop("regularMarketPrice")
        with self.assertRaisesRegex(ValueError, "invalid_price"):
            self.get()

    def test_bad_symbols_do_not_call_network(self):
        for symbol in (None, "", "../users", "BTC-USD/other", "EURUSD=X"):
            with self.assertRaisesRegex(ValueError, "invalid_symbol"):
                quotes.fetch_quote(symbol, now=self.now)
        self.http.assert_not_called()

    def test_cache_size_is_bounded(self):
        for index in range(quotes.CACHE_LIMIT + 3):
            symbol = f"S{index}"
            self.http.return_value = self.response(dict(self.meta, symbol=symbol))
            quotes.fetch_quote(symbol, now=self.now)
        self.assertEqual(len(quotes._cache), quotes.CACHE_LIMIT)
        self.assertNotIn("S0", quotes._cache)

    def test_concurrent_users_share_one_request(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: quotes.fetch_quote("SAP.DE", now=self.now), range(16)))
        self.assertEqual(self.http.call_count, 1)
        self.assertTrue(all(result["tradable"] for result in results))

    def test_fx_cache_is_revalidated_with_stock_cache(self):
        self.meta["currency"] = "USD"
        self.http.side_effect = [self.response(self.meta), self.fx(age=86390)]
        self.assertTrue(quotes.fetch_quote("SAP.DE", now=self.now)["tradable"])
        with self.assertRaisesRegex(ValueError, "stale_fx"):
            quotes.fetch_quote("SAP.DE", now=self.now + 20)
        self.assertEqual(self.http.call_count, 2)

    def test_pure_revalidation_is_read_only_and_uses_current_clock(self):
        tick = self.get()
        original = copy.deepcopy(tick)
        self.http.reset_mock()
        with patch.object(quotes.time, "time", return_value=self.now):
            self.assertTrue(quotes.is_tradable(tick))
        self.assertEqual(tick, original)
        self.http.assert_not_called()

    def test_pure_revalidation_rejects_stale_and_closed_quotes(self):
        tick = self.get()
        self.assertTrue(quotes.is_tradable(tick, self.now + 1190))
        self.assertFalse(quotes.is_tradable(tick, self.now + 1191))
        tick["regular_end"] = self.now + 5
        self.assertTrue(quotes.is_tradable(tick, self.now + 4))
        self.assertFalse(quotes.is_tradable(tick, self.now + 5))
        tick["regular_start"] = self.now + 1
        self.assertFalse(quotes.is_tradable(tick, self.now))

    def test_pure_revalidation_requires_market_timestamp_inside_session(self):
        tick = self.get()
        tick["regular_start"] = self.now - 5
        self.assertFalse(quotes.is_tradable(tick, self.now))
        tick["regular_start"] = self.now - 3600
        tick["regular_end"] = self.now + 5
        tick["market_at"] = self.now + 5
        self.assertFalse(quotes.is_tradable(tick, self.now))

    def test_pure_revalidation_future_boundaries(self):
        tick = self.get()
        tick["market_at"] = self.now + 60
        self.assertTrue(quotes.is_tradable(tick, self.now))
        tick["market_at"] = self.now + 61
        self.assertFalse(quotes.is_tradable(tick, self.now))

    def test_pure_revalidation_checks_foreign_exchange_again(self):
        tick = dict(self.get(), source_currency="USD", fx_rate=1.25,
                    fx_market_at=self.now - 86400)
        self.assertTrue(quotes.is_tradable(tick, self.now))
        self.assertFalse(quotes.is_tradable(tick, self.now + 1))
        tick["fx_market_at"] = self.now + 60
        self.assertTrue(quotes.is_tradable(tick, self.now))
        tick["fx_market_at"] = self.now + 61
        self.assertFalse(quotes.is_tradable(tick, self.now))
        tick["fx_market_at"] = self.now
        for rate in (None, 0, -1, math.nan, math.inf, True):
            with self.subTest(rate=rate):
                tick["fx_rate"] = rate
                self.assertFalse(quotes.is_tradable(tick, self.now))

    def test_pure_revalidation_bad_data_returns_false_not_exceptions(self):
        tick = self.get()
        for bad in (None, [], "bad", 1):
            self.assertFalse(quotes.is_tradable(bad, self.now))
        for field, values in (
            ("price", (None, math.nan, math.inf, 0, -1, True)),
            ("tradable", (False, None, "true", 1)),
            ("currency", (None, "USD")),
            ("source_currency", (None, "GBp", "JPY", {})),
            ("regular_start", (None, math.nan, math.inf, True, self.now + 1)),
            ("regular_end", (None, math.nan, math.inf, self.now, self.now + 86401)),
            ("market_at", (None, math.nan, math.inf, 0, True)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertFalse(quotes.is_tradable(dict(tick, **{field: value}), self.now))
        for bad_now in (math.nan, math.inf, True, "invalid"):
            self.assertFalse(quotes.is_tradable(tick, bad_now))

    def test_completed_history_excludes_current_day_even_if_ohlc_incomplete(self):
        tick = self.get()
        response = self.history_response()
        for values in response.json.return_value["chart"]["result"][0]["indicators"]["quote"][0].values():
            values[-1] = None
        self.http.return_value = response
        with patch.object(quotes.time, "time", return_value=self.now):
            history = quotes.fetch_completed_history("SAP.DE", tick)
        self.assertEqual(history["closes"], [101, 101, 101])
        self.assertEqual(history["opens"], [100, 100, 100])
        self.assertEqual(history["currency"], "EUR")
        self.assertEqual(self.http.call_args.kwargs["timeout"], 6)
        self.assertEqual(self.http.call_args.kwargs["params"], {"interval": "1d", "range": "2mo"})

    def test_completed_history_uses_exact_validated_tick_fx_and_raw_cache(self):
        tick = dict(self.get(), source_currency="USD", fx_rate=1.25,
                    fx_market_at=self.now - 15)
        self.http.reset_mock()
        self.http.return_value = self.history_response(currency="USD")
        with patch.object(quotes.time, "time", return_value=self.now):
            first = quotes.fetch_completed_history("SAP.DE", tick)
            second = quotes.fetch_completed_history("SAP.DE", dict(tick, fx_rate=2.0))
        self.assertEqual(first["opens"], [80, 80, 80])
        self.assertEqual(second["opens"], [50, 50, 50])
        self.assertEqual(first["fx_rate"], 1.25)
        self.assertEqual(first["fx_market_at"], self.now - 15)
        self.assertEqual(self.http.call_count, 1)

    def test_completed_history_rejects_stale_or_missing_tick_fx_before_network(self):
        tick = self.get()
        self.http.reset_mock()
        with patch.object(quotes.time, "time", return_value=self.now):
            for fields in ({"source_currency": "USD"},
                           {"source_currency": "USD", "fx_rate": 1.25, "fx_market_at": self.now - 86401},
                           {"source_currency": "USD", "fx_rate": 1.25, "fx_market_at": self.now + 61},
                           {"market_at": self.now - 1201}, {"regular_end": self.now}):
                with self.subTest(fields=fields):
                    with self.assertRaisesRegex(ValueError, "history_quote_not_tradable"):
                        quotes.fetch_completed_history("SAP.DE", dict(tick, **fields))
        self.http.assert_not_called()

    def test_completed_history_revalidates_quote_after_network(self):
        tick = self.get()
        response = self.history_response()
        current = [self.now]

        def fetch(*args, **kwargs):
            current[0] += 1201
            return response

        self.http.side_effect = fetch
        with patch.object(quotes.time, "time", side_effect=lambda: current[0]):
            with self.assertRaisesRegex(ValueError, "history_quote_no_longer_tradable"):
                quotes.fetch_completed_history("SAP.DE", tick)

    def test_completed_history_rejects_old_or_missing_completed_bars(self):
        tick = self.get()
        with patch.object(quotes.time, "time", return_value=self.now):
            for days, error in (((0,), "missing_completed_history"),
                                ((9, 8, 0), "stale_completed_history")):
                quotes.clear_cache()
                self.http.return_value = self.history_response(days_back=days)
                with self.assertRaisesRegex(ValueError, error):
                    quotes.fetch_completed_history("SAP.DE", tick)
            quotes.clear_cache()
            self.http.return_value = self.history_response(days_back=(8, 7, 0))
            self.assertEqual(len(quotes.fetch_completed_history("SAP.DE", tick)["closes"]), 2)

    def test_completed_history_rejects_wrong_instrument_currency_and_symbol(self):
        tick = self.get()
        with patch.object(quotes.time, "time", return_value=self.now):
            for fields, error in (({"symbol": "NVDA"}, "history_symbol_mismatch"),
                                  ({"currency": "USD"}, "history_currency_mismatch"),
                                  ({"instrumentType": "CURRENCY"}, "history_unsupported_instrument")):
                quotes.clear_cache()
                response = self.history_response()
                response.json.return_value["chart"]["result"][0]["meta"].update(fields)
                self.http.return_value = response
                with self.assertRaisesRegex(ValueError, error):
                    quotes.fetch_completed_history("SAP.DE", tick)

    def test_completed_history_rejects_unsorted_duplicate_future_timestamps(self):
        tick = self.get()
        with patch.object(quotes.time, "time", return_value=self.now):
            for mode in ("duplicate", "unsorted", "future"):
                quotes.clear_cache()
                response = self.history_response()
                timestamps = response.json.return_value["chart"]["result"][0]["timestamp"]
                if mode == "duplicate":
                    timestamps[1] = timestamps[0]
                elif mode == "unsorted":
                    timestamps[0], timestamps[1] = timestamps[1], timestamps[0]
                else:
                    timestamps[-1] = self.now + 61
                self.http.return_value = response
                with self.assertRaisesRegex(ValueError, "invalid_history_chronology"):
                    quotes.fetch_completed_history("SAP.DE", tick)

    def test_completed_history_rejects_nonfinite_missing_and_invalid_ohlc(self):
        tick = self.get()
        with patch.object(quotes.time, "time", return_value=self.now):
            for value in (None, math.nan, math.inf, 0, -1, True):
                quotes.clear_cache()
                response = self.history_response()
                response.json.return_value["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] = value
                self.http.return_value = response
                with self.assertRaisesRegex(ValueError, "invalid_history_close"):
                    quotes.fetch_completed_history("SAP.DE", tick)
            for field, value in (("high", 99), ("low", 102)):
                quotes.clear_cache()
                response = self.history_response()
                response.json.return_value["chart"]["result"][0]["indicators"]["quote"][0][field][0] = value
                self.http.return_value = response
                with self.assertRaisesRegex(ValueError, "invalid_history_ohlc_bounds"):
                    quotes.fetch_completed_history("SAP.DE", tick)
            quotes.clear_cache()
            response = self.history_response()
            response.json.return_value["chart"]["result"][0]["indicators"]["quote"][0]["close"].pop()
            self.http.return_value = response
            with self.assertRaisesRegex(ValueError, "misaligned_history_ohlc"):
                quotes.fetch_completed_history("SAP.DE", tick)

    def test_completed_history_cache_expires_and_never_falls_back_on_failure(self):
        tick = self.get()
        self.http.reset_mock()
        self.http.return_value = self.history_response()
        with patch.object(quotes.time, "time", return_value=self.now):
            quotes.fetch_completed_history("SAP.DE", tick)
        with patch.object(quotes.time, "time", return_value=self.now + 899):
            quotes.fetch_completed_history("SAP.DE", tick)
        self.assertEqual(self.http.call_count, 1)
        self.http.side_effect = quotes.requests.Timeout("offline")
        with patch.object(quotes.time, "time", return_value=self.now + 900):
            with self.assertRaisesRegex(ValueError, "history_fetch_failed"):
                quotes.fetch_completed_history("SAP.DE", tick)
        self.assertEqual(self.http.call_count, 2)
        self.assertNotIn("SAP.DE", quotes._history_cache)

    def test_completed_history_cache_size_is_bounded(self):
        tick = self.get()
        with patch.object(quotes.time, "time", return_value=self.now):
            for index in range(quotes.CACHE_LIMIT + 2):
                symbol = f"S{index}"
                self.http.return_value = self.history_response(symbol=symbol)
                quotes.fetch_completed_history(symbol, tick)
        self.assertEqual(len(quotes._history_cache), quotes.CACHE_LIMIT)
        self.assertNotIn("S0", quotes._history_cache)


if __name__ == "__main__":
    unittest.main()
