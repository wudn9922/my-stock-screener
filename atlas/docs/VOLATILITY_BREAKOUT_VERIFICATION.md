# Daily volatility, three breakout entries and four success rules — 2026-10-21

## Price volatility

ATR(20)% uses Wilder's 20 true ranges with known previous closes, then (19 × prior ATR + TR)/20; divide by the same completed daily close. Mean intraday amplitude is the latest 20 (high−low)/previous-close percentages. Both require 21 valid daily bars and stay independent of the selected chart timeframe and intraday quote. Provider/date/cache/price-basis provenance remains visible; failures or short histories show unavailable values. Symbol/provider request identity prevents stale results.

## Entries and retries

- `swingHigh`: first establish a 2 × ATR20 close rebound from a trough; its highest wick is confirmed by a later 2 × extreme-date ATR close retreat. Confirmation is usable next bar. This rejects small lateral turns, preserves old levels without an N-day expiry and does not demand an extra full 1-2-3/higher-low/volume pattern. A strict intraday high above the reference records an attempt. A failed signal-day close does not consume the reference: later days can retry until a qualified close. Missing opens do not invent entries. Theoretical entry is max(open, key).
- `box20`: existing prior-20-session box width ≤14% TW /10% US, close above its top. `ema20`: prior close ≤ prior EMA20 and current close > current EMA20, seeded by 20 closes with alpha 2/21. Both retain close entries, the existing ten-session cooldown and first-fresh-event-per-stock/window policy.
- Every signal freezes its own reference line. In particular, the EMA reference used for outcome barriers does not move after entry. A swing's separate failed-close attempts count as separate signals. No actual tick fills, minimum increments, fees, slippage or exit fills are simulated.

## Four success rules

All share the common priority failure: signal-day close < key + 0.1 × Wilder ATR14, computed through that day's close. Equality passes. Immediate failures stay in the denominator. All four compare the same attempts with complete data for the next five MARKET sessions; the signal day is excluded from forward barriers. Recent attempts and missing stock candles are separately reported and never assumed failures.

1. `hold5`: no D+1…D+5 low touches key ×0.95.
2. `nextHigh`: D+1 high strictly exceeds the signal-day high.
3. `firstHit`: earliest of low ≤0.95×key (loss), high ≥1.05×key (win), or a D+1 higher high (win). Five sessions without a stop also win. An opening gap can prove event order. Without known order, positive and negative wicks on the same candle conservatively lose. Earlier results are not overwritten by later target/stop action.
4. `targetAndNext`: some D+1…D+5 high touches 1.05×key AND D+1 makes a higher high. No additional minus-five veto applies to this rule.

Each comparison reports signals = complete + pending + missing, common-close failures, same-candle conservative failures, and four exact wins/failures/evaluated denominators. Win rate is rounded wins/evaluated. Flat as-of-return fields remain only for legacy research compatibility; the UI never uses them for these rates. `successModel=four-rules-v1`, `swingMethod=atr-reversal-v2` and validated cohort arithmetic prevent legacy result relabelling. A recent one-week signal window can have no five-day-complete observations and is visibly pending.

## Scope and publication

Complete current TW listed/OTC ordinary-share and US exchange ordinary-share/ADR lists come from the existing universe-full cache. Lists must satisfy exchange minimums and be ≤14 days old. The listing denominator never shrinks when a history file is missing. At least 90% current valid daily-bar coverage is required before either market is atomically published; otherwise the old report is preserved. No S&P-only fallback. Production builds run after the full-cache refresh; PR CI only restores that cache read-only, computes the real report and uploads evidence. No credentials, production data/cache saves, accounts, Supabase or notifications are changed by review CI.

The interface keeps four compact outcome choices, three cards, sample and missing/pending diagnostics, an expandable comparison matrix and detailed calculation help. Method/rule switching shares the same per-method cohort. Full-scope, model and arithmetic validation hide legacy/partial/malformed rates. Button focus, 44px phone targets and no-page-overflow assertions cover desktop/mobile; WebKit is included in PR CI.

## Verification

- Full Python suite: **1,395 passed**. Focused breakout coverage: **84** (38 shared/legacy, 6 full-market, 22 waves and 18 outcomes). Cases include five frozen real stock histories, MU key retention/retry, pivot source/confirmation dates, scale and prefix invariance, exact threshold touches, ordering and gaps, common failure precedence, complete/pending/missing counts and report preservation.
- Lint, typecheck, production build and Pages build passed.
- Desktop/phone Chromium breakout and volatility regressions: **20 passed**. The initial UI artifact capture exposed internal-scroller clipping; evidence capture now expands the viewport only AFTER real-device interaction/overflow checks.
- Frontend: **927 tests in 78 files passed** on the final full run. One initial SEC fixture exceeded its existing five-second timeout while Python/build/browser jobs ran concurrently; no SEC source or timeout was changed. A sequential full rerun passed all 927 tests in 23 seconds.
- CI uploads successful panel screenshots as review artifacts. Final real-report source run, artifact digest, coverage and rates are recorded with the committed report in the PR description. No local subset result is presented as full market.

## Verified real report

The committed JSON is the exact output of [read-only source run 39568611989](https://github.com/wudn9922/my-stock-screener/actions/runs/39568611989/job/119116912119), at source head `b9003e9b2baf2072d34656fa18cdef050d09d099`. It restored `universe-full-v1-39433983829-1`; artifact `12331201685` ZIP SHA-256 is `b5b19c7a8b99056dd9131ec01d3d91d25e40f82ac9e3227a19f552b0c59d86f8`. Exact artifact bytes, version/model keys, coverage arithmetic, signal = complete + pending + missing, common/tie failure bounds, numerator/denominator/rate arithmetic and cross-rule invariants were verified before installation.

Both markets are dated **2026-10-19**. Taiwan lists 1,979/evaluates 1,948 stocks (31 stale/non-trading); US lists 5,347/evaluates 5,282 (65 stale/non-trading). No history file is missing; per-attempt forward candle gaps are separately excluded. Month-window full-market rates:

|Market / entry|Complete samples|Hold5|Next high|First event|Target AND next high|
|---|---:|---:|---:|---:|---:|
|TW swing|873|341 / 39.1%|295 / 33.8%|383 / 43.9%|244 / 27.9%|
|TW box|398|347 / 87.2%|218 / 54.8%|357 / 89.7%|165 / 41.5%|
|TW EMA20|1,103|860 / 78.0%|537 / 48.7%|880 / 79.8%|313 / 28.4%|
|US swing|1,063|299 / 28.1%|254 / 23.9%|357 / 33.6%|179 / 16.8%|
|US box|238|173 / 72.7%|126 / 52.9%|185 / 77.7%|52 / 21.8%|
|US EMA20|2,118|1,142 / 53.9%|925 / 43.7%|1,367 / 64.5%|561 / 26.5%|

Real-report desktop-TW and phone-US rendering was checked against all three first-event rates, the 12-cell comparison matrix, overflow and runtime errors (zero). These screenshots use the real statistics artifact with unrelated quote/report fixtures; they are not live quote or physical-device evidence. The PR description links the final head's CI result after the data commit.

Current-list survivorship, truncated cached history, missing sessions, unadjusted corporate actions and daily OHLC order limits remain. No independent review or physical Safari verification is claimed. Submit and update PR #147 only; do not merge or enable auto-merge.
