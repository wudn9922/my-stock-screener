# Atlas phone redesign (handoff to Codex), 2026-11-13

Author: Claude (design and review). Audience: Codex, one draft PR per step below.
Read `AGENTS.md` and `atlas/AGENTS.md` first. The owner approved this direction and asked that
Claude review every PR for visual quality before it is merged.

## What is being built

A new look and phone layout for the five main screens. The design is fixed; this is an implementation
job, not a design job.

- Source of truth: `atlas/docs/redesign/reference/{watchlist,markets,screener,stock,me}.html`. Open them
  in a browser at 390px wide. Every size, colour, radius, gap and font in them is deliberate and written
  as inline CSS: read the values, do not estimate them from a screenshot.
- The numbers in the references are a snapshot (report day 2026/11/09). The line and candle shapes are
  drawn by hand as placeholders: real pages keep using real data and the existing chart engine.
- Scope is the phone layout (`max-width: 767px`). At 768px and wider only the tokens and fonts in step 1
  change; no desktop layout is redesigned in this round.

## Rules for every step

1. Do not invent visuals. If a state or element is not in a reference (loading, error, empty, a control the
   reference omits), reuse the nearest pattern that is in a reference and say so in the PR.
2. Nothing functional is removed. A control that is not drawn in a reference still has to be reachable;
   each step says where it goes. If a step does not say, ask in the PR instead of dropping it.
3. Chart drawing, gestures, storage and symbol code are out of scope (`atlas/AGENTS.md` invariants). Restyle
   around the chart; do not change `src/drawing/`, gesture handling or IndexedDB.
4. Both colour conventions must work: `data-colors='tw'` (red up, default) and `'us'` (green up).
5. Touch targets at least 44px; body text contrast at least 4.5:1; no horizontal scroll at 360px and 390px.
6. Tests: a layout assertion may change only where this document changes the expected layout. List every
   changed assertion and why in the PR. Never skip or loosen a test for any other reason.
7. Each PR is a draft and carries one 390px-wide screenshot per changed screen (the cost rules allow this
   for layout changes), plus a short list of every place it differs from the reference and why.

## Tokens (step 1 applies them; later steps only use them)

| Token | Now | New |
| --- | --- | --- |
| `--bg` | `#0b0e13` | `#0E1014` |
| `--surface` | `#11151b` | `#161A20` |
| `--surface-2` | `#161b23` | `#1E232B` |
| `--surface-3` | `#1d232d` | `#2A303A` |
| `--border` | `#232a35` | `#1E232B` (row hairlines) |
| `--border-strong` | `#303846` | `#2A303A` (control and section borders) |
| `--text` / `--text-2` / `--text-3` | `#e3e7ee` / `#a9b1bf` / `#7f8898` | `#EEF1F5` / `#A4ADBA` / `#8A94A3` |
| `--accent`, `--accent-text` | `#3d8bfd`, `#79adff` | `#F5B544` for both; text on an accent fill is `#14110A` |
| `--accent-soft` | blue 14% | `rgba(245,181,68,0.14)` |
| `--up`, `--up-text` (tw) | `#f23645`, `#ff5c68` | `#FF5A5F` for both |
| `--down`, `--down-text` (tw) | `#22ab94`, `#2fc2a8` | `#2BC8A8` for both |
| `--up-soft` / `--down-soft` | 14% | 14% of the new colours |
| tab bar background | `var(--surface)` | `#12151A` |

- `upDownColors()` in `atlas/src/app/sitePreferences.ts` and the hard-coded chart colours
  (`src/chart/ChartEngine.ts`, `src/ui/MiniChart.tsx`, canvases under `src/pages/`) take the same values.
  Chart background `#161A20` inside cards and `#0E1014` on the stock page; grid `#1E232B`; axis text `#8A94A3`.
- Numbers and tickers use IBM Plex Mono (400, 500, 600): set `--font-num` and use it for every price,
  percentage, ticker and date. Self-host the Latin woff2 files (the page must not load fonts from another
  origin, and the pending CSP forbids it) and add the OFL notice to `atlas/THIRD_PARTY_NOTICES.md`.
- Chinese text keeps the existing system stack in `--font-ui`. Do not ship a CJK web font.
- Update `theme-color` and the boot screen colours in `atlas/index.html` to the new `--bg`.

## Steps (one draft PR each, in this order)

1. **Tokens, fonts and shell.** The table above, the font, the top bar and the bottom tab bar
   (64px high, 22px icons, 12px labels, active tab in the accent colour). No page layout changes.
2. **自選** (`watchlist.html`). Underline tabs for the lists; 88px rows: ticker and name, a 104x36 daily
   sparkline, then price, change pill and, for US stocks with one, the after-hours line. No index tiles and no
   summary strip above the list (the owner rejected both). 清單｜持股 moves into the underline tabs as
   a fourth entry 持股. The four list actions (以篩選條件建立清單, 建立手動清單, 匯出清單備份, 匯入備份)
   open from the + button in the header as a sheet.
3. **市場** (`markets.html`). Every index is a full card: name and trend tag, 32px value and change, a
   candlestick chart 150px high (the existing `MiniChart`, restyled; never a line chart), then the MA
   distances as a three-cell grid. Order on the page: index cards, 多頭末期警示 summary, then the rest.
   The existing chart controls (zoom, reset, bar count) stay available from the card.
4. **選股** (`screener.html`). Rows become a four-column grid: code and name, distance to MA20 as a
   bar with its value, P/E, close and change. Actions dock above the tab bar: 連續看圖, 存成清單, CSV.
   Use only data the page already loads: the owner dropped an intraday line for this column because it
   would add requests. Do not add any per-row fetch.
5. **個股** (`stock.html`). The chart is the page: the header shrinks to one row plus the price row, the
   view tabs sit under it, and the plot fills everything down to a 56px toolbar (timeframes, 指標, 繪圖)
   docked above the tab bar. At 390x844 the chart area must be at least 60% of the viewport height.
   `e2e-site/workspace-refinements.spec.ts` measures the header: update it to the new, shorter header.
   Everything in today's header menus (更新 K 線, 建議均線, 波動率, data notes, share) stays reachable.
6. **我的** (`me.html`). Account card, then grouped setting rows. 個股均線參數 and 自訂分組 open as
   sub-views that hold today's forms unchanged; 資料狀態 and 備份與還原 likewise.

## How review works

Claude builds each PR branch, loads the screen at 390px with the published report data and compares it
with the reference side by side. Expect comments on spacing, alignment, type and colour, not only on
behaviour. A step is done when Claude signs off in the PR and CI is green; the owner merges.

## Before starting

- Branch from `main` after `claude/atlas-website-review-oucju7` is merged (it touches `styles.css`, the
  account card and the screener toolbar). If it is still open, branch from it and say so.
- #258 (light theme proposal) was written against the old palette. Leave it; it will be redone on these tokens.
