# Opus 5.5 Arena: isolated paper competitor

Requested by Martin on 31 October 2026: a third virtual competitor next to the
Krypto reference account and Astra Ultra, designed by Claude Opus 5.5, starting
with the average of both. This is a paper experiment, not a profit claim,
financial product or live trading system. The deployed engine is deterministic
Python. It never calls an LLM, a private exchange endpoint, a broker-order
function or Telegram.

## Start capital

Seeded exactly once, when both competitors are fresh (monitor heartbeat at most
90 seconds old):

    initial_capital = (Krypto mark-to-market equity + active Astra equity) / 2

Krypto equity uses the same formula Astra uses for its own seed. The active
Astra run is the stored run with the latest `version_started_at`. If either side
is missing or stale, the worker waits and creates nothing. Seeding skips the
current 2H boundary; the first decision is the next completed candle.

## Rules: `opus_pullback_2h_v1`

Deliberately different from Astra's breakout: buy a confirmed bounce after a
pullback inside an uptrend. BTC, ETH, SOL (same universe as Astra for a fair
fight), LONG/spot-like 1×, completed OKX 2H candles, 400 bars of history.

Entry (all must hold on the completed signal candle):

1. Trend: close > EMA200 and EMA50 > EMA200.
2. Pullback: the lowest Wilder RSI14 of the previous four completed candles
   (signal candle excluded) is ≤ 40.
3. Reversal: the signal candle is green and closes above the previous candle's high.

Exits:

- Initial stop 1.5 × ATR14 below the adverse model fill.
- Trailing stop: at each completed candle, stop = max(stop, highest observed
  price since entry − 3 × ATR14). The 30-second monitor fires at the stored stop.
- Trend exit: completed close below EMA200.
- Time exit after 120 hours. No fixed take profit.

Risk: 0.5% of equity planned stop risk per entry (costs included), at most 1.5%
aggregate planned risk, at most three positions, at most 40% notional each.
Costs per side: 0.20% fee + 0.05% slippage (same as Astra). Entry only in the
first 180 seconds after the candle close, with fresh complete data for all three
symbols and a quote within 1% of the signal close. No backfilling, no borrowing,
no pyramiding, no re-entry in the same candle after an exit.

**No backtest was run.** Neither the cloud session nor the local machine could
reach OKX/Binance market history when this was built. The rules are a reasoned
hypothesis, not a selected historical winner.

## Scoreboard

Each cycle records the current equity of Krypto and Astra (only when fresh).
All three are compared by percentage return and max observed drawdown since the
Opus start, each against its own equity at that moment. Krypto is shown at its
raw mark-to-market value (no Astra-style cost overlay). A Krypto depot reset
(`start_capital` change) or an Astra restart (`started_at` change) marks that
competitor's comparison invalid; it is ranked last and labelled.

## Separation

- Dedicated `opus_arena_runs`, `opus_arena_decisions`, `opus_arena_equity` tables.
- Only owner user 1 is enrolled. GET `/api/crypto/opus_arena` requires login,
  is no-store and cannot mutate anything. There is no trade/reset API.
- Reads `crypto_portfolios`, `settings` and `astra_ultra_runs` read-only; never
  writes them (covered by tests).
- Rules and their hash are stored; editing rules under the same version fails.
  A rule change needs a new version and an explicit carry-forward policy.
- `OPUS_ARENA_ENABLED=0` disables the whole worker (including monitoring of its
  paper positions) after restart.
- Own leader lock `<db>.opus-arena.lock`, separate 30-second daemon, started from
  `start_background_watchers()` like Astra.

## Verification

`python tests/test_opus_arena.py -v` and `node --test tests/opus_arena.test.cjs`.
