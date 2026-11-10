# Atlas light theme — scope and token proposal (approval gate)

Status: **PROPOSAL ONLY**. No production theme or stylesheet has been changed. Source handoff: `docs/handoff/atlas-site-review-followups-codex.md` on `claude/atlas-website-review-oucju7` (2026-11-10).

## Proposed scope for owner approval

- Entire Atlas site, not only the chart: 大盤、個股、個股財務、價量、研究、世界、主題、選股、清單、我的／設定／登入與彈出面板。
- Under 顯示設定, next to 漲跌顏色, offer `深色` / `淺色` / `跟隨系統`. Store per existing site preference context; default is **深色**, including existing users. Following system responds to `prefers-color-scheme` changes.
- Repaint tokens plus direct canvas/WebGL chart options when resolved theme changes; avoid only changing CSS while charts stay dark.
- Include initial boot screen, `line-login-launch.html`, `theme-color`, focus/disabled/selection states; no chart engine or account/persistence migration.
- Preserve both `data-colors="tw"` (red-up) and `data-colors="us"` (green-up); ensure price/candle/volume/heatmaps and text are readable with either combination.

## Proposed semantic tokens

| Token | Existing dark | Proposed light |
| --- | --- | --- |
| `--bg` | `#0b0e13` | `#f4f7fb` |
| `--surface` | `#11151b` | `#ffffff` |
| `--surface-2` | `#161b23` | `#f7f9fc` |
| `--surface-3` | `#1d232d` | `#edf2f7` |
| `--border` | `#232a35` | `#d5deea` |
| `--border-strong` | `#303846` | `#b5c2d5` |
| `--text` | `#e3e7ee` | `#172338` |
| `--text-2` | `#a9b1bf` | `#485971` |
| `--text-3` | `#7f8898` | `#596a80` |
| `--accent` | `#3d8bfd` | `#2563eb` |
| `--accent-text` | `#79adff` | `#174cb5` |
| `--accent-soft` | blue/0.14 | blue/0.10 |
| `--warn` | `#e0a43a` | `#8f5d08` |
| `--up` (TW) | `#f23645` | `#c7283a` |
| `--down` (TW) | `#22ab94` | `#087b66` |
| `--up-text` (TW) | `#ff5c68` | `#b71e32` |
| `--down-text` (TW) | `#2fc2a8` | `#006b57` |
| `--up-soft` / `--down-soft` | red/green 0.14 | red/green 0.10 |

US convention swaps up and down *after* theme resolution, including text and translucent semantic tokens. Geometry, type scale, radii and layout tokens remain unchanged.

## Static contrast verification (2026-11-10)

Computed the WCAG relative-luminance contrast ratio for proposed text/semantic colors against the proposed light `--surface` (`#ffffff`), `--surface-2` (`#f7f9fc`), and `--surface-3` (`#edf2f7`). The original proposed `--text-3: #627289` scored **4.35:1** on `--surface-3`, below WCAG AA's **4.5:1** normal-text criterion, so the proposal now uses `#596a80` (**4.91:1** against `--surface-3`, **5.53:1** against white). Both proposed TW candle semantic colors exceed **4.6:1** against `--surface-3`. This checks color-pair math only, not actual DOM contrast, translucency/compositing, focus states or screenshots; those must be verified after implementation.

Canvas palette for light: primary chart background `#ffffff`, axis `#485971`, grid `#e8eef5`, crosshair `#7e8ea4`, border `#ccd5e2`; mini-chart grid `#e8eef5`; heat-map canvas label and tile scale need separate visual evaluation. Refactor hard-coded values in `src/chart/ChartEngine.ts`, `src/ui/MiniChart.tsx` and heat-map canvases in `src/pages/` without changing stored drawing anchors.

## Validation gate before rollout

1. **Owner approves scope** (entire site vs only 大盤+個股). Do not start bulk style conversion before explicit approval.
2. Produce **one 390px-wide screenshot of 大盤 and one of 個股** using the actual compiled light-theme UI before conversion of every stylesheet; visually review readability (especially legend, axis, menus, mini charts, green/red conventions).
3. Run affected theme/preference unit tests, relevant browser specs desktop Chromium/mobile Chromium/iPhone WebKit, and the compact 124px chart-header regression. Verify persisted 深色/淺色/系統 and OS mode changes.
4. If any regression fails, fix it rather than weakening test thresholds.

This PR is deliberately documentation-only: no authentic 390px light-theme screenshot can be claimed from GitHub metadata access. Screenshots are a required next gate after initial UI prototype on a runnable device.
