import unittest
import sqlite3
import ast
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

# Import isolated sources, never db_store's production bootstrap/backup side effects.
ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


okx_client = load('volume_test_okx', ROOT / 'analyzer/okx_client.py')
package = types.ModuleType('analyzer')
package.okx_client = okx_client
package.indicators = load('volume_test_indicators', ROOT / 'analyzer/indicators.py')
config = types.ModuleType('config')
for node in ast.parse((ROOT / 'config.py').read_text(encoding='utf-8')).body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith('CRYPTO_'):
        setattr(config, node.targets[0].id, ast.literal_eval(node.value))
config.get_crypto_symbol_name = lambda symbol: symbol
config.get_crypto_universe = lambda: [{'symbol': 'BTC'}]
with patch.dict(sys.modules, {'analyzer': package, 'config': config}):
    crypto_signals = load('volume_test_signals', ROOT / 'analyzer/crypto_signals.py')
schema = next(node for node in ast.parse((ROOT / 'analyzer/db_store.py').read_text(encoding='utf-8')).body
              if isinstance(node, ast.FunctionDef) and node.name == '_ensure_settings_schema')
namespace = {}
exec(compile(ast.Module(body=[schema], type_ignores=[]), 'db_store_schema_only', 'exec'), namespace)
db_store = types.SimpleNamespace(_ensure_settings_schema=namespace['_ensure_settings_schema'])


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
    def test_exchange_timestamp_is_not_local_cache_time(self):
        row = dict(last='100', high24h='101', low24h='99', open24h='100', ts='1793379087123')
        for stamp, expected in [('1793379087123', 1793379087.123), ('broken', None), (None, None)]:
            row['ts'] = stamp
            okx_client._price_cache.clear()
            with patch.object(okx_client, '_http_get', return_value={'data': [row]}), patch.object(okx_client.time, 'time', return_value=1800000000.):
                result = okx_client.fetch_ticker('BTC')
                self.assertEqual(result['market_timestamp'], expected)
                self.assertEqual(result['ts'], 1800000000.)
                self.assertEqual(result['last'], 100.)

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
