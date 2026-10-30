# Krypto Offensiv: paper execution policy v1

Authorized on 30 October 2026 for Martin's virtual crypto account (owner 1).
The user explicitly selected crypto as the more aggressive sleeve; Astra stays
the conservative comparison. This is an execution and sizing rule change,
not a demonstrated profitability improvement. No real-exchange order path is
added or changed. Paid services and Telegram tests are outside this release.

## Economic scope

`crypto_offensive_quality_v1` retains the chosen classic/volume-confirmed score,
dynamic leverage capped at 10, at most four positions, the 30% available-cash
margin limit and existing cash reserve/minimum margin. New entries additionally
have at most 1.5% of marked virtual equity in planned stop risk including model
costs and at most 6% original planned risk across all open positions. Astra's
0.5% per entry / 1% aggregate and v3 rules remain unchanged.

For entry E, original stop S, margin M and leverage L, modeled planned loss is
`M*L*(abs(E-S)/E + (0.002+0.0005)*(1+S/E))`. The same additive costs apply to the
target reward; a target that is nonpositive after costs cannot support a new
entry. Costs are scenario assumptions, not this user's verified exchange tier.
Gross ledger accounting remains unchanged. Fees/slippage are not debited again.
The sizing equity is gross and does not subtract historical overlay costs.
The existing Astra reference-cost overlay remains the single comparison cost
adjustment. These are spot-price-based leveraged paper positions, with no actual
currency conversion or historically reconstructed futures funding.

The initial risk of existing positions counts against the aggregate cap, even
after a trailing stop moves. Unknown/invalid old-position data blocks additional
exposure. Risk sizing rounds margin down to cents. Existing positions above the
new cap are preserved and monitored; they are not forcibly closed. Stop losses
can exceed planned risk after gaps/delays. No promise of more trades or profit.

## Data and execution

Only this paper path opts into completed 1H candles. All OHLC/volume/timestamp
arrays are filtered together and checked for alignment, confirmation, freshness,
continuity and finite values. A running candle cannot drive the indicators.
Other callers of the shared signal module retain their existing default.

Entry uses an observed public ticker's exchange timestamp, not local cache time.
Quotes older than 60 seconds, more than 5 seconds in the future, preceding the
signal close or going backwards against the last accepted per-coin entry time
are rejected. Entry may deviate at most 1% from the signal price. Absolute stop
and target distances are transferred to the observed entry; unsafe direction,
price and liquidation relationships are rejected. Quotes are fetched outside
the ledger lock, then revalidated alongside cash, positions and risk under the
same lock as the entry commit. Missing current holdings' marks block new entries.

BTC risk-off continues to block LONG entries and no longer blocks eligible SHORT
paper entries solely because BTC is falling. No additional trend filter or
volume-reversal exit is enabled. Dry-run analysis for owner 1 does not evaluate,
close, initialize or otherwise write the virtual portfolio.

## Continuity and visibility

The existing crypto monitor enrolls only an already existing owner-1 portfolio.
The idempotent marker records version, rule hash, actual enrollment time, rules
and reason. Failure to enroll never stops protection of existing positions.
Other owners retain the old execution path. The per-position metadata and last
accepted quote timestamp are committed with the actual entry, not a failed plan.

No reset, new cash, historical repricing or close/reopen operation occurs. Old
trades, balances, position protections and histories remain intact. Astra's
reference identifier includes the paper policy version so the comparison records
a new reference rule period. Its original start, fees, losses and complete curve
are retained; its own strategy version and rule hash do not change.

The scoped GET-only research export explicitly allowlists the marker and new
per-position risk/cost/quote fields. No users, credentials or writable controls
are added. The dashboard describes the aggressive sleeve and explicitly labels
the primary crypto ledger as gross. The unchanged reference overlay is not a
claim about actual broker charges.

## Evidence and rejected alternatives

The 30 October audit captured complete consistent histories at 16:51:27 UTC.
Since 25 October, crypto had 23 closes: gross -18.36 virtual EUR and estimated
additional costs 227.36; Astra had no new trades and no new data/check failures.
The five trailing exits were collectively negative after the specified costs.

The separate fixed crypto family compared the classic closed-candle proxy,
a strict EMA direction filter, a volume-reversal exit and their combination under
identical risk/cost/cash constraints. The historical simulations all hit the
existing loss circuit; none justified enabling the tested signal/exit changes.
Historical taker pressure and the actual 30-second path cannot be reconstructed
from the hourly archive, so this is not exact replay of `volume_confirmed`.

For Astra, the fixed 360-day comparison was v3 -1.6638%, volume-exit -2.8118%,
trend-filter -1.5974%. All remain negative under higher costs, have only one
positive quarter and lose without the best trade. All had zero entries in the
new 126-hour 25–30 October window. The alternatives failed the preregistered
criteria; Astra v3 is retained, without a claim that it is optimal.

Dated audit, raw market responses, protocols, all successful/failed runs and
reproduction scripts are outside the repository in `trading-review-20261030`.
Known historical data remain selection evidence, not unseen validation.

## Release verification

Use temporary databases and mocked public data; never run tests on the live DB.
Verify confirmed-array handling, unchanged shared defaults, owner isolation,
LONG/SHORT risk, nonpositive net targets, stale/future/backward timestamps,
concurrent entries, old/new positions, cent rounding and capital continuity.
Verify the new marker is exported and creates a reference-version boundary
without changing Astra rules or double-charging costs. Independently review the
diff. Before release capture a full ledger snapshot; afterwards compare all old
IDs/rows/capital and current monitoring, exact commit and persistent disk.
Do not invoke the GitHub scheduler, which includes unrelated real OKX actions.
