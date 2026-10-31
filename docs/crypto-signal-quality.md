# Fresh pressure evidence for owner-1 paper signals (31 October 2026)

Technical version: `fresh_trade_pressure_v1`. The risk/execution policy remains
`crypto_offensive_quality_v1`; Astra remains `astra_breakout_2h_volume_v3`.
This repairs input evidence, not a demonstrated profitable trading strategy.

## Defect and bounded repair

The earlier public-trades parser ignored exchange timestamps and trade IDs and
accepted negative/non-finite amounts. Reproductions accepted day-old trades as
buying pressure, counted duplicate IDs twice, and returned buy_ratio=2 for a
buy notional of 100 and invalid sell notional of -50.

Only the closed-candle paper-signal path now opts into strict pressure parsing.
Price, size, notional, timestamp, instrument and taker side must be valid. The
most recent trade must be at most 60 seconds old; future tolerance is 5 seconds.
Only supplied trades within the preceding 300 seconds contribute. Identical IDs
are deduplicated; conflicting IDs or malformed rows invalidate the sample.
The raw cache is separate from the legacy cache and revalidated against the
current time on every use. Local retrieval time is never exchange time.

This is a sample of up to 300 recent OKX spot trades, not the complete market,
order book or guaranteed full five-minute coverage. Sample count and oldest/
latest exchange timestamps are explicit. There is no added arbitrary minimum
trade count, score, universal volume gate, or directional entry/exit rule.
Unknown pressure yields no positive or negative pressure bonus; the existing
overbought-LONG rule still needs both relative volume and valid buying pressure.
Classic signals and shared real-exchange callers keep their prior defaults.

Before an owner-1 volume-based entry, the serialized plan revalidates exactly
the sample used for scoring, without network calls under the ledger lock. If
it expires or changes as rows age out, the entry is rejected until a later
fresh analysis. The score is never silently used with changed evidence.
New positions store only the technical version and compact pressure summary;
no raw trades are added to the ledger. The scoped read export allowlists those
fields and reports the deployed technical version even when portfolios are flat.

## Continuity and verification

No migration or reset runs. Existing positions, stops, targets, initial risk,
cash, costs, losses, histories, and older rule markers remain untouched. The
pre-release full snapshot is retained and compared with a new complete snapshot
after deployment. Both independent server protection loops remain active.

Regression checks cover stale/future/malformed/non-finite observations,
instrument mismatch, duplicates, cached rows ageing out, delayed entry,
unknown-pressure semantics, legacy callers, compact allowlisted export and
temporary-SQLite ledger preservation. Tests cannot prove future profitability.

## Separately tested economic candidate — not activated

The small preregistered family was B0 (existing classic/hourly research proxy)
versus N1 (require modeled net target reward at least modeled initial stop risk).
Both preserve the same risk/cash/fees/exit model. Across 360 days, B0 returned
-25.4704% (58 trades, loss circuit); N1 -11.4368% (33 trades), and N1 -13.8676%
under cost stress. N1's four separate quarters all lost money. It failed the
profitability, quarter-consistency and concentration gates and is not promoted.

The research proxy debits model costs and uses net equity for sizing; the live
Krypto account sizes on gross equity with a separate cost overlay. Both test
candidates share the same model, but the test is not a production replay.
These are known historical selection data, not fresh forward validation or an
exact replay of the running volume-confirmed strategy: historical taker pressure
and the actual 30-second path are unavailable. Full results, failed candidates,
causality checks, source hashes and protocols are retained outside the repository
under `work/crypto-signal-research-20261031` in the original project.

Sources: [OKX public trades](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-trades)
defines taker side, base-coin size for spot, trade ID and exchange time in ms;
the age limits above are this application's documented data contract.
