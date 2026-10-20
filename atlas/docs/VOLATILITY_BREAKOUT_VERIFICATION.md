# Daily volatility and breakout entries — 2026-10-20

## Calculations and data

- ATR(20)% = Wilder ATR(20) / the same completed daily close × 100. True range is max(high-low, abs(high-previous close), abs(low-previous close)). Seed with the mean of the first 20 true ranges with known prior closes; update with (previous ATR × 19 + TR) / 20 across the available daily history.
- Mean intraday range = mean((high-low) / previous close × 100) across the most recent 20 completed daily bars. This excludes overnight gaps, unlike true range. Require at least 21 valid, increasing daily bars; missing history or a failed request displays an unavailable value.
- Daily data comes through the selected MarketDataProvider, regardless of the displayed chart interval. Session-close filtering uses the symbol's exchange calendar rules. The header never divides ATR by a potentially intraday quote. Data date, source, stale-cache status and price basis appear in the help disclosure. Missing sessions are not fabricated. Unadjusted corporate actions can inflate measured volatility.
- New-high entries retain strict close > the preceding 20/60/120-session highest high. The 20-session box retains the existing width thresholds (TW 14%, US 10%). EMA20 entries require prior close <= prior EMA and current close > current EMA. EMA uses an initial 20-close SMA and then alpha 2/21, matching the chart. EMA crossings use a ten-session cooldown; no future observations enter any signal.
- Each stock/method contributes its first fresh event within the selected window. Only events held at least three trading days enter rates. Returns compare the breakout close with the report-date close, with unchanged data-cleaning and current-membership limitations.
- Strategy win rate = profitable mature signals / mature signals. The benchmark averages same-entry-date universe up-rates across those signals, so signal-heavy dates have greater weight. These denominators differ and the percentages need not match. A percentage-point difference is descriptive, not proof of an effective strategy. US report coverage is S&P 500 constituents with local histories; TW uses the existing ordinary-stock CSV universe.

## Verification

- `npm test`: 923 tests in 78 files passed, including deterministic gap, flat-market, Wilder-history, invalid-input and incomplete-session cases.
- Python full suite: 1,349 tests passed. The 38 breakout tests include an independent slow reference on randomized universes, causal/future-mutation checks, EMA seed/cross/cooldown checks and report/fixture contract validation.
- `npm run lint`, `npm run build` and `npm run build:pages`: passed. Existing dependency annotation notices do not fail the build.
- Playwright: 30 desktop/phone Chromium checks passed across the site, volatility and breakout suites. The PR also runs iPhone WebKit checks. Synthetic fixtures explicitly differ in daily versus weekly prices and in current quote versus daily close. They exercise symbol switching, unavailable daily data, insufficient history, three methods, high-lookback selection and legacy reports.
- Desktop and phone Chromium screenshots were inspected: volatility appears below P/E without horizontal overflow. Screenshots use synthetic prices and are not market evidence. Physical Safari and independent reviewer sign-off remain unverified.

The updated real report uses local CSV histories (TW as of 2026-10-20; US as of 2026-10-19). The one-off S&P 500 constituent source was the public datasets/s-and-p-500-companies CSV (503 symbols, all with local files), because this workspace's network policy blocks Wikipedia. The scheduled production source policy is unchanged. No live account, database or notification writes were performed.
