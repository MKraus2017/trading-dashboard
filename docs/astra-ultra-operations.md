# Astra Ultra: isolated paper experiment

This is an experiment, not an optimality/profit claim, financial product or live
trading system. Research/model review was delegated to GPT-6 Astra with Ultra
reasoning. The deployed engine itself is deterministic Python; it never calls an
LLM, a private exchange endpoint, a broker-order function or Telegram.

## Separation and reproducibility

- Dedicated `astra_ultra_runs`, `astra_ultra_decisions`, `astra_ultra_equity` tables.
- Only existing owner user1 is enrolled by the background worker. Other users
  cannot access its reports. GET endpoints never initialize or advance a run.
- Seed once from fresh source account MTM equity (not just available cash). Astra
  starts flat. Source capital, positions, trades and settings are untouched.
- State, all closed trades and timestamped checks remain in persistent SQLite,
  including across deployments. Normal existing DB backups contain the tables.
- Version and a hash of rules are stored. Editing rules under the same version
  fails instead of silently reinterpreting history. Future promotion must be a new
  named version and preserve the old run, with explicit carry/close/start policy.
- No backward filling of missed hour decisions, fabricated quote, guaranteed stop
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
evidence. Different historic execution resolution is disclosed there. The future
paper run is the new observation period; a few days do not validate a strategy.

## Runtime

Separate30-second daemon and process leader lock keep HTTP data requests outside
the original stop-monitor loop. Each public request has6-second timeout. Rule
decisions use exactly500closed4H bars during the first180seconds after close, not on
page refresh. Epoch/missed-hour/data-error counters expose interruption.

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
checks read-only UI polling, escaping and navigation. The full Flask smoke test
must run from a clean release with temporary DB, maintenance enabled, all workers
disabled and sockets blocked, never against the user's production database.

The chosen research hypothesis is the4H24/12-channel withEMA200, ATR14×2 and336h
maximumhold. The earlier1H candidate is rejected and was never deployed. Each
decision uses a finite500-bar indicator window; EMA seeds atSMA200, first ATR true
range=high-low. Historical/live pure-indicator parity must be tested. Historical
OHLCstop execution is still not identical to observed live quotes.
