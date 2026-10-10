# Volume exit forward comparison (v1)

This feature observes the existing **virtual crypto** portfolio. It never calls
an order, portfolio-save, reset, auto-trading or Telegram operation. The original
entry/exit strategy is unchanged, including open positions at deployment.

## Fixed rule

- OKX spot reference `SYMBOL-USDT`, completed 15-minute bars only.
- LONG: previous close >= previous EMA20; latest close < EMA20 and < its open.
- SHORT: mirrored conditions.
- Quote-volume >= 1.5 × mean of the preceding 20 completed bars (current excluded).
- EMA20 seeded by SMA20 using up to 100 returned bars; minimum 50 contiguous,
  confirmed bars; no synthetic volume or unconfirmed-bar fallback.
- Only decisions observed within 90 seconds of close, after enrollment, are used.
- Exit price is a subsequent public ticker observation, exchange timestamp after
  the signal-bar end and no older than 60 seconds. No fills at historical closes.
- Baseline wins if it closes first, including a concurrent close during data fetch.
- The alternative follows exactly the baseline exit absent an earlier volume exit.

This is a paired exit overlay, not an independently reinvesting portfolio. New
entries, notional and leverage come solely from the existing virtual strategy.

## Fair accounting and limits

Only trades observed from entry and closed in both branches without detected data
errors/gaps enter headline totals. Existing positions are `late_adoption` and
remain visible but excluded. Trades entirely missed, crossing the first bar
before enrollment, reset source positions, and monitoring gaps are also excluded.
No old historical trades are enrolled at startup.

Both branches use identical fixed model assumptions per side: 10 bps fee + 5 bps
slippage, applied to entry and exit notional. Gross P&L follows the existing
virtual ledger (including the isolated-margin gross-loss cap). Funding is not
modeled. Prices are spot references, not actual derivative execution; EUR figures
are the existing virtual account's proportional model, not a currency-converted
broker statement. These model-net figures differ from the original gross display.

The drawdown figure is the largest observed peak-to-trough net mark within an
individual paired trade, not portfolio drawdown. Quotes sampled by the baseline
watcher (roughly 30 seconds) can miss intraperiod movement. No performance or
future-profit claim follows from a small sample.

## Operations

- Separate daemon thread, 30-second cadence, own process leader lock.
- HTTP timeouts cannot block the original stop watcher.
- `CRYPTO_EXIT_SHADOW_ENABLED=0` disables only the observer after restart.
- Reuses the existing private persistent SQLite database with additive tables:
  `crypto_exit_shadow_runs`, `crypto_exit_shadow_positions`, `crypto_exit_shadow_bars`.
- Existing database backups contain the new tables. Restarts do not reset the run.
- `GET /api/crypto/exit_shadow`: authenticated, current session user only,
  read-only, no market requests, `Cache-Control: no-store`.
- Krypto tab: "Volumen-Ausstieg · paralleler Vergleich", refreshes every 30s.
- A rollback to older app code leaves these additive tables recoverable.

## Offline tests (no production data or network)

```
python tests/test_crypto_exit_shadow.py -v
node tests/crypto_exit_shadow.test.cjs
python tests/test_crypto_watch.py
```
