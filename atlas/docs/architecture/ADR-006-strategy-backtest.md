# ADR-006 — Research strategy and backtest V1

Status: Accepted for the frozen V1 strategy module.

## Boundary

`src/strategy/` is a pure research calculation over normalized `Bar` records. It does not depend on React, Lightweight Charts, provider payloads, storage, or broker APIs. The caller supplies `closedBars`; that name is the explicit contract that every included bar is closed. Market-data code owns regular-session completion rules because a daily candle's timestamp alone does not identify its close. The strategy engine performs no clock-based forming-bar guess.

The module supports a moving-average cross and a close/MA cross, using SMA or EMA over close prices. SMA emits its first value after `period` bars. EMA uses that same initial SMA seed and then the standard recursive smoothing factor `2 / (period + 1)`. MA-cross compares fast against slow and requires `fastPeriod < slowPeriod`; price-cross compares close against the configured slow average. Periods are integers from 1 to 5000. Inputs must be strictly time-sorted, unique, finite, nonnegative OHLCV with internally consistent OHLC ranges. Invalid data is rejected rather than reordered or repaired.

## Signal and execution rules

Signals are evaluated after a closed bar. MA-cross requires finite fast/slow values on both the previous and current bars. Price-cross requires finite slow-average values on both bars. Upward crossings use `previous <= average` then `current > average`; downward crossings use `previous >= average` then `current < average`. The strict current side makes equality a neutral transition. Long mode emits BUY/EXIT; short mode emits SELL/COVER.

Each signal is eligible for execution only at the next supplied bar's open. A signal on the last supplied bar remains a signal with no fill. No order fills at the signal close, and an open position is not liquidated just because the data ends. The final closed-bar close marks an open position for equity and return calculations; closed-trade metrics include closed trades only.

There is at most one position at a time, with 100% of current marked equity allocated to each new position and no leverage. Long quantity reserves enough for its entry commission so cash cannot become negative at entry. Short quantity is based on current equity divided by fill price plus its entry commission. Cash and signed quantity determine marked equity. Commission applies to each executed entry and exit notional. Slippage is adverse on both sides: buys/covers fill above the open and sells/exits below it. Both costs default to zero and are returned as explicit assumptions.

Short positions have no simulated broker margin call. If marked equity reaches zero or below at a closed-bar close, the run stops before processing later bars or orders, retains the actual open position and nonpositive equity, and returns an insolvency status and warning. It does not fabricate a liquidation price or clamp losses. This is a research model, not a broker execution or margin model.

## Results and units

The result includes close-time signals, execution markers, closed trades, a per-simulated-bar equity path, and an optional marked open position. Trade PnL and average trade are in account currency. Win rate, return, drawdown, trade return, exposure, and trade return fields are fractions (for example, `0.10` means 10%). Profit factor is the ratio of positive closed-trade PnL to absolute negative closed-trade PnL and is null when there are no losses. Exposure is the fraction of simulated closed bars whose ending mark has a position. Date range is the first and last simulated closed-bar timestamp, or null for empty input. Total return includes marked unrealized PnL; closed-trade counts and trade statistics do not.

## Verification

`tests/strategy.test.ts` covers SMA/EMA warmup, strict crossings, no lookahead, next-open gap fills, final-bar no-fill behavior, long and short cash/PnL, fees and slippage, marked drawdown, short insolvency without liquidation, and invalid inputs. These tests verify deterministic calculations; they do not model broker routing, financing, borrow availability/cost, taxes, dividends, corporate actions, or exchange-specific session schedules.
