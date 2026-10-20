# Astra Ultra: isolated paper experiment

This is an experiment, not an optimality/profit claim, financial product or live
trading system. Research/model review was delegated to GPT-6 Astra with Ultra
reasoning. The deployed engine itself is deterministic Python; it never calls an
LLM, a private exchange endpoint, a broker-order function or Telegram.

## Current release rules: experimental 2H version 3

Martin explicitly requested selection and online activation of a new **virtual**
strategy on 2026-10-20. The selected C2 hypothesis is
`astra_breakout_2h_volume_v3`: BTC, ETH and SOL only, LONG/spot-like 1x. Check the
authenticated runtime report before asserting this release is actually active.
A completed 2-hour candle must close strictly above its 2H EMA200 AND satisfy
either of these entry branches:

1. The original v2 branch: close above the previous 24 candle highs (48 hours).
2. Additional branch: close above the previous 12 candle highs (24 hours), EMA200
   strictly higher than six candles earlier, and RVOL20 at least 1.25.

The original branch remains valid without volume confirmation; the new branch
does not filter old valid signals. RVOL20 is the completed signal candle's USDT
quote turnover divided by the mean turnover of its previous 20 candles. It is
total turnover, **not buying pressure or historical taker-buy share**. Missing,
non-finite, negative volume, a zero-volume signal candle or a non-positive reference mean disables only the
additional branch; it never fabricates a zero RVOL or blocks an otherwise valid
baseline signal. Price/confirmation/continuity errors still block new exposure.

All price channels and the RVOL denominator exclude the signal candle. Fetch
506 completed, contiguous 2H candles. Current EMA and ATR still use exactly the
last 500; the six-candle-lagged EMA uses its own 500-candle window ending six bars
earlier. EMA starts at its first 200 closes' mean; ATR14 uses Wilder recursion
with first true range equal to high minus low. Older v1/v2 rules remain frozen.

The initial stop remains 2 ATR14 below the adverse model entry fill. Exit on an
observed stop breach, a completed 2H close below the previous 12 lows (24 hours),
or 336 hours since entry. There is no fixed take profit. The 30-second quote/stop
monitor is unchanged. Risk remains 0.5% of equity per entry, 1% aggregate initial
planned risk, at most two positions, and at most 40% notional each. Entry requires
fresh data for all three symbols, a fresh quote within 1% of the signal close,
and sufficient budget. Shorter candles do not guarantee entries or better returns.

The new 2H offline comparison is documented at the top of the research report.
C2 lost 1.6638% over 360 days after main model costs, versus v2's 6.6814% loss;
its hourly sampled drawdown was 16.3089% versus 20.4114%, with 106 versus 104
entries. It still lost 7.6299% under higher costs; only one of four separate
quarter blocks was profitable. **It failed the preregistered robustness gates.**
The user-authorized forward experiment does not turn that failed assessment into
a validated improvement. The six-day diagnostic window had no C2 entry and was
not blind out-of-sample data. More activity or a victory is not promised.

The archival 4H results and historical signal-parity evidence still belong to
`astra_breakout_4h_v1`, not this 2H version. No other coins were added:
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
  fails instead of silently reinterpreting history. The explicit v1-to-v2 and
  v2-to-v3 transitions retain the old persisted run and carry forward cash, positions,
  trades, fees, initial capital and original experiment start into the new run.
  `version_started_at` records the actual transition; `prior_versions` records
  each earlier version's start/end, ending equity/cash, counts and frozen rules.
  No synthetic close/reopen or capital reset is performed at the switch.
- Carried positions retain their entry version, signal, quantity, entry costs and
  existing stop. During the earlier v1-to-v2 change, `management_version` /
  `management_rules` explicitly selected 2H channel management; a 4H entry was not
  relabelled as 2H. The v2-to-v3 change alters entries only, so already-open positions
  also keep their existing management version and rules unchanged.
  Closed historic trades retain their original details. The report combines old
  and current decision/equity history while labelling each decision's version and
  `timeframe_hours`. Future transitions require their own explicit policy.
- No backward filling of missed candle decisions, fabricated quote, guaranteed stop
  fill, negative-cash borrowing, pyramiding, martingale or same-cycle reentry.
- Public OKX data time and candle-confirm fields checked. Missing/partial-universe
  data blocks new exposure; existing positions remain visible as stale and their
  exits are retried on subsequent fresh quotes.

### Version 3 migration and rollback safety

Once a user's v3 run exists, SQLite triggers reject inserts, updates and deletes
on that user's older-version run, decision and equity rows. Rolling back to an
old executable therefore fails closed (`astra_version_retired_no_rollback`)
instead of resuming obsolete capital as a second account fork. Recovery requires
an explicitly reviewed, forward-compatible fix that continues the current ledger;
do not drop these triggers or delete history to force a rollback.

At the explicit v2-to-v3 migration only, a pending v2 retry is preserved in the
old state and the new run's `prior_versions` record, then cleared from the active
retry slot. It is not replayed under new rules or falsely marked successful.
An existing `candle_error` survives the transition and remains visible until a
new complete candle evaluation succeeds. Routine restarts do not clear retries.

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
evidence, with separate v3 and archived 4H sections. Different historic execution
resolution is disclosed there. Current displayed return, drawdown and comparison remain measured
from the ORIGINAL experiment start and include all version periods; they are not
isolated v3 results. The version-start timestamp does not reset these metrics.
A few days of any version do not validate a strategy.

## Runtime

Separate30-second daemon and process leader lock keep HTTP data requests outside
the original stop-monitor loop. Each public request has6-second timeout. New rule
decisions request506closed2H bars (500 per EMA/ATR window) during the first180seconds after close, not on
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

### Technical retry correction (2026-10-12; no strategy change)

The quote-monitor heartbeat and candle-evaluation health are separate. A fresh
`monitor_success_at` (also retained as legacy `last_success_at`) does not clear
an unresolved `candle_error`. `last_candle_success_at` and `last_candle_bar_end`
are recorded only after a complete successful candle evaluation; older runs
without these fields are not retrospectively labelled as evaluated.

Failed/partial candle checks keep a persistent `pending_bar` and retry only
inside the original 180-second decision window (at most six attempts, at least
30 seconds between attempts). The run records the attempt
count, deadline and completed actions across restarts. A retry must not repeat
an entry or re-enter a symbol already closed in that window. New entries still
require fresh complete-universe data. Stop/time-exit monitoring continues
independently, including while the candle check is incomplete.
Position quotes must also be no earlier than the entry time or the last accepted
market timestamp. A stale/backward tick cannot fabricate a protective exit.

An expired/unresolved window is recorded as missed once; it is never backfilled.
Its warning remains until a later complete candle check succeeds. The UI shows
the monitor timestamp separately from the most recent confirmed candle and
shows pending retries. This is execution reliability, not a new trading
hypothesis: `VERSION`, `RULES`, rule hash, account balances, existing positions
and historical trades are not reset or reinterpreted by this correction.

New decision rows retain an append-only `attempt_history` and an explicit
`execution_history`; the UI makes both inspectable. Only unfinished rows from
this retry protocol may be extended. Legacy and finalized decisions are not
overwritten. State, fills and their decision records still commit atomically.

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
entry-versus-management candle labels, v3 rule labels, nullable volume and
decision evidence. The full Flask smoke test
must run from a clean release with temporary DB, maintenance enabled, all workers
disabled and sockets blocked, never against the user's production database.

The former4H24/12-channel hypothesis is preserved in the research record. The
earlier1H candidate was rejected and never deployed. Version3 retains v2's
24-high entry branch, 12-low exit, EMA200, ATR14×2 and336h maximumhold, adding
only the explicit 12-high/trend/volume entry branch. Old4H signal-parity tests do
not validate 2H performance. Historical OHLCstop execution is not identical to
observed live quotes. Before deploying, compare the pure v3 signal definition to
the frozen C2 research definition, test migration twice for idempotence, and
verify the source-account ledger and all carried Astra capital/history survive.
