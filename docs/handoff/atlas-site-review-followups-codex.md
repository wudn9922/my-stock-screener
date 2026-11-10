# Atlas site review follow-ups (handoff to Codex), 2026-11-10

Author: Claude (site review on `claude/atlas-website-review-oucju7`). Audience: Codex, one PR per task.
Read `AGENTS.md` and `atlas/AGENTS.md` first; their cost rules and invariants apply. Nothing here is started.
Each task is independent. Do them in this order; stop and report instead of loosening a test.

## 1. Share button on the 個股 page (small)

Why: Atlas is opened from LINE, but a stock view cannot be shared without copying the address bar.

- Build the link with `serializeRoute({ page: 'chart', symbol, tf })` (`atlas/src/app/routes.ts`) on top of
  `location.origin + PUBLIC_BASE` (`atlas/src/app/HostingMode.ts`). Never include account or list ids.
- Use `navigator.share({ title, url })` when present; otherwise `navigator.clipboard.writeText(url)` and a
  short `role="status"` confirmation. A rejected share (the user closed the sheet) is not an error.
- Placement: the phone chart header is at its height limit (`e2e-site/workspace-refinements.spec.ts:53`
  allows 124px). Do not add a row or widen the header; put the action inside an existing menu
  (`atlas/src/ui/StockStatsMenu.tsx` is the first candidate) and keep a 44px touch target.
- Tests: one e2e-site spec (desktop and mobile Chromium, WebKit in CI) that stubs `navigator.share` and the
  clipboard and asserts the exact URL; rerun `workspace-refinements.spec.ts` and `stock-views.spec.ts`.

## 2. Content-Security-Policy (medium; needs a real LINE login)

Why: the 90-day device token (`atlas/src/cloud/deviceSession.ts`) lives in `localStorage`, and the page has
no CSP. No injection sink was found in the review; this is defence in depth against a compromised script.

- GitHub Pages cannot send headers, so this is a `<meta http-equiv>` in `atlas/index.html` and
  `atlas/line-login-launch.html`. `frame-ancestors` and `report-uri` do not work from a meta tag.
- Known needs: `script-src 'self' https://static.line-scdn.net` (the LIFF SDK, `lineIdentity.ts`);
  `style-src 'self' 'unsafe-inline'` (inline boot styles and React `style` props); `connect-src` for the
  Supabase functions host, the LINE API hosts the SDK calls, and same-origin report data; `img-src 'self' data:`;
  `object-src 'none'`; `base-uri 'self'`. The `%BASE_URL%startup.js` boot script is same-origin.
- The LIFF SDK's own network and frame use is not documented in this repo. Before merging, log in on a real
  phone in three places (LINE in-app browser, Safari/Chrome with the QR/handoff flow, installed PWA) with the
  browser console open and no CSP violations. The mocked `line-sdk-contract.spec.ts` cannot prove this.
- If that device check is not possible, do not ship the policy; report what was verified.

## 3. `/run` must fail closed (small; coordinate the deploy)

Why: `breakout_alert/app.py` `is_run_request_authorized()` returns `True` when `RUN_TOKEN` is unset, so a
missing variable silently leaves the Cloud Run trigger open to anyone who has the URL.

- Change: unset token → 503 with a clear message (not 200, not 401), covered by a unit test.
- Deploy order matters: confirm the Cloud Run service has `RUN_TOKEN` and Cloud Scheduler sends
  `X-Run-Token` BEFORE this ships (`.github/workflows/cloud-run.yml` does not set it). State in the PR that
  this was checked, and by whom. Do not print the token.

## 4. Light theme (large; get the owner's go-ahead on scope first)

Why: the site is dark-only (`color-scheme: dark` in `atlas/src/styles.css`).

- Colours are mostly tokens on `:root` in `styles.css`, but the charts hard-code theirs
  (`atlas/src/chart/ChartEngine.ts`, `atlas/src/ui/MiniChart.tsx`, the heat-map canvases under
  `atlas/src/pages/`), and so do `index.html`'s boot screen and `theme-color`.
- Store the choice beside the existing 漲跌顏色 preference (`atlas/src/app/sitePreferences.ts`,
  `atlas/src/ui/DisplaySettings.tsx`): 深色 / 淺色 / 跟隨系統, default 深色 so nothing changes for current users.
- Up/down colours must stay readable in both conventions (`data-colors='tw' | 'us'`) on both themes.
- Propose the token list and one 390px screenshot of 大盤 and 個股 before converting every stylesheet.

## Not for this handoff

- Offline support: the screener deployment retires its service worker on purpose (`atlas/src/auth/retireWorker.ts`).
- `workspace-refinements.spec.ts:6` measured 124.3px vs 124px in a Linux container on unmodified `main`
  (font metrics); check the CI result before treating it as a regression.
