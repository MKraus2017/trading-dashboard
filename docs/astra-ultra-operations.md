# Astra Ultra: isolated paper experiment

This is an experiment, not an optimality/profit claim, financial product or live
trading system. Research/model review was delegated to GPT-6 Astra with Ultra
reasoning. The deployed engine itself is deterministic Python; it never calls an
LLM, a private exchange endpoint, a broker-order function or Telegram.

## Current rules: 2H version, not the earlier 4H backtest

As requested on 2026-10-11, the current named hypothesis is
`astra_breakout_2h_v2`: BTC, ETH and SOL only, LONG/spot-like 1x. A completed
2-hour candle must close strictly above the previous 24 candle highs (48 hours)
AND its 2H EMA200. Both channels exclude the signal candle. Indicators use exactly
500 completed, contiguous 2H candles. EMA starts at the first 200 closes' mean;
ATR14 uses Wilder recursion with first true range equal to high minus low.

The initial stop remains 2 ATR14 below the adverse model entry fill. Exit on an
observed stop breach, a completed 2H close below the previous 12 lows (24 hours),
or 336 hours since entry. There is no fixed take profit. The 30-second quote/stop
monitor is unchanged. Risk remains 0.5% of equity per entry, 1% aggregate initial
planned risk, at most two positions, and at most 40% notional each. Entry requires
fresh data for all three symbols, a fresh quote within 1% of the signal close,
and sufficient budget. Shorter candles do not guarantee entries or better returns.

The 4H results and historical signal-parity evidence in the research document
belong to `astra_breakout_4h_v1`, not this 2H version. No new 2H historical backtest
or profitability validation is presented. The 2H change is a new forward-test
hypothesis, not a historically proven improvement. No other coins were added:
the three-symbol universe is an experimental scope, not a claim that LINK, ADA,
XRP or DOGE cannot be handled technically or have been shown to perform worse.

## Separation and reproducibility

- Dedicated `astra_ultra_runs`, `astra_ultra_decisions`, `astra_ultra_equity` tables.
- Only existing owner user1 is enrolled by the background worker. Other users
  cannot access its reports. GET endpoints never initialize or advance a run.
- Seed once from fresh source account MTM equity (not just available cash). Astra
  starts flat. Source capital, positions, trades and settings are untouched.
- State, all closed trades and timestamped checks remain in persistent SQLite,
  including across deployments. Normal existing DB backups contain the tables.
- Version and a hash of rules are stored. Editing rules under the same version
  fails instead of silently reinterpreting history. The explicit v1-to-v2
  transition retains the old persisted run and carries forward cash, positions,
  trades, fees, initial capital and original experiment start into the new run.
  `version_started_at` records the actual transition; `prior_versions` records
  each earlier version's start/end, ending equity/cash, counts and frozen rules.
  No synthetic close/reopen or capital reset is performed at the switch.
- Carried positions retain their entry version, signal, quantity, entry costs and
  existing stop. `management_version` / `management_rules` explicitly select new
  2H channel management after the transition; a 4H entry is not relabelled as 2H.
  Closed historic trades retain their original details. The report combines old
  and current decision/equity history while labelling each decision's version and
  `timeframe_hours`. Future transitions require their own explicit policy.
- No backward filling of missed candle decisions, fabricated quote, guaranteed stop
  fill, negative-cash borrowing, pyramiding, martingale or same-cycle reentry.
- Public OKX data time and candle-confirm fields checked. Missing/partial-universe
  data blocks new exposure; existing positions remain visible as stale and their
  exits are retried on subsequent fresh quotes.

## Comparison conventions

The observed current account is rebased to the same timestamp/equity. Only costs
of subsequent fills and change in estimated liquidation cost of inherited open
positions affect period return; no historical profit is credited and no old entry
fees are charged again. Missing historical IDs, disappearing positions or reset
start capital invalidate its comparison. Changing selected strategy is recorded.

This is NOT a matched-risk isolation of signal quality: the old account retains
its leverage, symbols, entry schedule, manual interventions and risk rules. It
uses synthetic derivatives on spot prices with no funding. Astra1x is spot-like.
Both use .20% fee and .05% slippage per side for a comparable cost scenario, not a
claim about this user's fee tier. This fee is motivated by the2026-09-11 OKX EEA
spot-only regular-tier notice effective2026-09-25; other account tiers differ.

Astra entry fills are adverse by .05%, fees debited; marked equity includes adverse
exit slippage and fee. The old ledger gets an explicit estimated cost overlay.
USDT price ratios are applied to virtual EUR notionals, with NO real currency
conversion. Quantities are model units. Drawdown is sample-based portfolio MTM,
not guaranteed intrabar maximum. Gap losses may exceed planned .5% stop risk.

Research backtests in `docs/astra-ultra-research.md` are retrospective selection
evidence for the former 4H hypothesis. Different historic execution resolution is
disclosed there. Current displayed return, drawdown and comparison remain measured
from the ORIGINAL experiment start and include both version periods; they are not
isolated 2H results. The version-start timestamp does not reset these metrics.
A few days of either version do not validate a strategy.

## Runtime

Separate30-second daemon and process leader lock keep HTTP data requests outside
the original stop-monitor loop. Each public request has6-second timeout. New rule
decisions use exactly500closed2H bars during the first180seconds after close, not on
page refresh. The candle length is7200seconds. Boundaries use UTC epoch time;
timestamps in the UI are shown in Europe/Berlin. Entry checks begin no earlier
than5seconds after close. Seeding or changing version skips the current completed
boundary instead of backfilling. The first subsequent completed 2H candle is the
first eligible new-version decision.

`missed_hours` is a legacy field name: it counts missed candle-decision windows,
not elapsed hours, and may aggregate 4H and 2H periods. The UI therefore labels it
as missed candle checks across versions. Data-error and observation-gap counters
also retain their history. Inspect per-candle decisions as well as the heartbeat:
with no open positions, a routine monitor cycle alone does not establish that a
new completed candle has been successfully evaluated.

Workers start through Gunicorn's post_worker_init hook, never at app-module import.
This remains safe if environment arguments override preload_app=False. A first
HTTP request is an idempotent fallback for other WSGI hosts; standalone app.py
starts explicitly. A deployment check must verify monitor leader locks belong to
the worker, not the master, and that authenticated report reads succeed.

`ASTRA_ULTRA_ENABLED=0` disables the entire experiment worker (INCLUDING monitoring
of its paper positions) after restart; it does not affect the original account.
Do not use this switch to disable entries alone when paper positions exist.
Maintenance gate is shared with existing storage maintenance. Report
`GET /api/crypto/astra_ultra` requires login, reads session userid, is no-store and
cannot mutate any ledger. There is no trade/reset API for the experiment.

## Review every five days

Check run heartbeat, data coverage, original monitor, costs, concentration,
max drawdown, per-symbol net results and sample size. Do not retune to a handful of
losses or market this as an independently validated winner. Pre-register small
candidate sets and preserve unsuccessful results. Ask before paid subscriptions,
new access, real-money activity or publishing performance claims.

## Verification

`python tests/test_astra_ultra.py -v` exercises temporary databases and mocked
market data, authentication/isolation, source immutability, accounting, gap/stale
data, rule freezing and duplicate prevention. `node tests/astra_ultra.test.cjs`
checks read-only UI polling, escaping, navigation, mixed-version history and
entry-versus-management candle labels. The full Flask smoke test
must run from a clean release with temporary DB, maintenance enabled, all workers
disabled and sockets blocked, never against the user's production database.

The former4H24/12-channel hypothesis is preserved in the research record. The
earlier1H candidate was rejected and never deployed. The current2H hypothesis
retains channel lengths24/12, EMA200, ATR14×2 and336h maximumhold, but these are
different elapsed-time lookbacks. Old4H signal-parity tests do not validate new2H
performance. Historical OHLCstop execution is not identical to observed live quotes.
