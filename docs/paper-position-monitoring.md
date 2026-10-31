# Open-position supervision (30 October 2026)

Martin requested ongoing market checks after a purchase and appropriate virtual
intervention. Scope is the existing owner-1 Krypto and Astra paper accounts.
There is no real order authority, new exchange credential or writable research
API. Fast protection remains on the continuously running Render service.

## Server and assistant have distinct roles

The crypto watcher aims to run every 30 seconds. It marks open positions and
checks liquidation, stop, take-profit, the existing trailing mechanism and time
exit. The existing portfolio loss circuit remains unchanged. Astra separately
checks stops/time exits every 30 seconds and its channel exit on completed 2H
candles. The schedule is a target, not guaranteed tick-by-tick execution.

Since Martin's 31 October instruction, the additional assistant check runs every
15 minutes and reviews every open position: accepted
quote age, price movement, unrealized PnL and estimated costs, original/current
stop risk, TP and liquidation distance, holding time, concentration, completed
candle trend, ATR/volatility, RSI/MACD, relative volume and available fresh buying
pressure. Coin-specific news is checked against dated primary sources when
material. Missing measures are explicitly unknown; total volume is not a
substitute for buying pressure. It uses a complete scoped snapshot, then free
public market data, keeping observation time and source separate.

This review is periodic; it is not an event-triggered, always-running LLM.
Local scheduled work requires the app/computer to remain running. The Render
protective worker is independent. No new generic RSI, trend or volume-selling
rule is enabled by this release. The existing 15-minute volume-exit observer is
still hypothetical. Extra discretionary exits are not inferred from one adverse
candle; rule changes require offline evidence, versioning, independent review,
temporary-database tests and preserved capital/positions/history.

On a new completed Astra 2H candle, the review also checks the trade thesis and
existing research. Material new evidence can start a small preregistered offline
test between the broader five-day strategy reviews. Empty accounts do not cause
forced entries or a new parameter search every 15 minutes. Krypto remains the
aggressive rule-based paper sleeve; Astra remains conservative. Neither schedule
changes the existing entry/aggregate risk caps. Both saved automations remain
attached to the existing chat and retain notifications only for relevant changes.

## Technical repair: accepted quote time for Krypto exits

Before this release, owner-1 crypto entries checked exchange time, but the exit
path checked only a positive finite last price. Consequently a recent worker
heartbeat could be mistaken for recent market data. `fresh_exit_quotes_v1`
records the repaired technical monitor; the entry policy and strategy hashes
are unchanged.

For owner 1, accepted exchange timestamps must be finite and positive, no more
than 60 seconds old and no more than 5 seconds ahead. They cannot precede the
position's entry or a previously accepted entry/monitor quote. Legacy positions
without a monitor quote time may begin with an unambiguous stored entry time.
Missing/invalid clocks fail closed; local fetch time is never fabricated as
exchange time. Validation occurs before applying marks, trailing changes or
exits, and again before execution after potentially slow market requests.
The loss-circuit close path must also use still-valid quotes.

Rejected quotes keep affected positions and their last valid protections
unchanged, mark the monitor incomplete and are retried. Neither stale data nor
a fabricated price can justify a synthetic exit. The dashboard names affected
symbols and data-quality reasons; the scoped export exposes the accepted quote
time, technical monitor identifier and bounded error codes. These diagnostics
also make the hourly review capable of distinguishing a healthy loop from a
healthy price feed. Other owners and real-OKX paths retain their previous logic.

No stop, TP, risk budget, leverage choice, trade/history record or cash balance is
reset or retroactively repriced. Market gaps or missing data can still lead to
losses beyond planned stop risk. No profit or guaranteed fill is implied.
