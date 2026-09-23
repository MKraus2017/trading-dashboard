import unittest
import sqlite3
from unittest.mock import patch

from analyzer import crypto_signals, db_store, okx_client


def candles(relative_volume=1.0):
    count = 60
    volumes = [100.0] * count
    volumes[-1] = 100.0 * relative_volume
    return {
        "closes": [100.0] * count,
        "highs": [102.0] * count,
        "lows": [98.0] * count,
        "opens": [99.0] * count,
        "volumes": volumes,
        "timestamps": list(range(count)),
        "confirmed": [True] * count,
    }


class CryptoVolumeStrategyTests(unittest.TestCase):
    def analyze(self, strategy, relative_volume=1.0, buy_ratio=0.5):
        with (
            patch.object(okx_client, "fetch_candles", return_value=candles(relative_volume)),
            patch.object(okx_client, "fetch_ticker", return_value={"vol24h": 10_000_000}),
            patch.object(okx_client, "fetch_recent_trade_pressure", return_value={
                "buy_ratio": buy_ratio, "trade_count": 300,
            }) as pressure,
            patch.object(crypto_signals.indicators, "ema", side_effect=lambda values, period: [110.0 if period == 20 else 100.0]),
            patch.object(crypto_signals.indicators, "rsi", return_value=[75.0]),
            patch.object(crypto_signals.indicators, "macd", return_value={"macd": [2.0], "signal": [1.0]}),
            patch.object(crypto_signals.indicators, "bollinger", return_value={"upper": [200.0], "lower": [0.0]}),
            patch.object(crypto_signals.indicators, "atr", return_value=[2.0]),
            patch.object(crypto_signals.indicators, "adx", return_value=[70.0]),
        ):
            result = crypto_signals.analyze_crypto_symbol("BTC", strategy_version=strategy)
            return result, pressure

    def test_classic_keeps_existing_signal_and_skips_pressure_request(self):
        result, pressure = self.analyze("classic")
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["score"], 68.0)
        self.assertFalse(result["volume_filter_blocked"])
        pressure.assert_not_called()

    def test_volume_strategy_blocks_overbought_long_without_confirmation(self):
        result, _ = self.analyze("volume_confirmed", relative_volume=1.0, buy_ratio=0.50)
        self.assertEqual(result["direction"], "HALTEN")
        self.assertTrue(result["volume_filter_blocked"])
        self.assertFalse(result["volume_confirmed"])

    def test_volume_strategy_allows_overbought_long_with_both_confirmations(self):
        result, _ = self.analyze("volume_confirmed", relative_volume=1.5, buy_ratio=0.60)
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["score"], 73.0)
        self.assertTrue(result["volume_confirmed"])
        self.assertEqual(result["relative_volume"], 1.5)
        self.assertEqual(result["buy_pressure_pct"], 60.0)

    def test_unknown_version_falls_back_to_classic(self):
        self.assertEqual(crypto_signals.normalize_strategy_version("future"), "classic")


class OkxTradePressureTests(unittest.TestCase):
    def test_trade_pressure_is_quote_weighted(self):
        okx_client._trade_pressure_cache.clear()
        payload = {"data": [
            {"px": "100", "sz": "2", "side": "buy"},
            {"px": "50", "sz": "2", "side": "sell"},
        ]}
        with patch.object(okx_client, "_http_get", return_value=payload):
            result = okx_client.fetch_recent_trade_pressure("BTC", limit=2)
        self.assertAlmostEqual(result["buy_ratio"], 2 / 3)
        self.assertEqual(result["trade_count"], 2)


class SettingsMigrationTests(unittest.TestCase):
    def test_legacy_settings_table_is_upgraded_idempotently(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE settings (
            user_id INTEGER PRIMARY KEY,
            auto_trade_enabled INTEGER DEFAULT 1,
            report_enabled INTEGER DEFAULT 1,
            updated_at TEXT
        )""")
        db_store._ensure_settings_schema(conn)
        db_store._ensure_settings_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
        self.assertIn("telegram_bot_token", columns)
        self.assertIn("telegram_chat_id", columns)
        self.assertIn("crypto_strategy_version", columns)
        self.assertIn("telegram_trade_confirmation_enabled", columns)
        conn.execute(
            "INSERT INTO settings (user_id, crypto_strategy_version) VALUES (?, ?)",
            (1, "volume_confirmed"),
        )
        value = conn.execute(
            "SELECT crypto_strategy_version FROM settings WHERE user_id = 1"
        ).fetchone()[0]
        self.assertEqual(value, "volume_confirmed")
        conn.close()


if __name__ == "__main__":
    unittest.main()
