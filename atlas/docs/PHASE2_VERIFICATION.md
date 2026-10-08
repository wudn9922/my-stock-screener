# Phase 2 verification log

Phase 2 was authorized in the existing workspace on 2026-10-06. Phase 1 architecture, Lightweight Charts 5.2.1, IndexedDB stores and canonical anchors were retained. No project initialization or ZIP restoration was performed.

## Sequential drawing gates

- **Horizontal Ray**: 32 unit tests passed; lint and production build passed. Browser gate: 31 passed, one expected desktop-touch skip across desktop/mobile Chromium and iPad/iPhone WebKit. Rectangle implementation began only after this gate.
- **Rectangle**: 36 unit tests passed; lint/build passed. Main gate initially had 33 browser passes, one expected skip and two iPad artifact-close failures. A second concurrent Playwright process had removed the shared default trace directory; both errors were `browserContext.close: ENOENT`, without failed interaction assertions. Runs were then serialized. Full iPad subset passed 10/10. Dedicated four-corner editing test passed 4/4 projects, including Chromium CDP touch and WebKit touch modality. Fibonacci implementation began only after these gates.
- **Fibonacci Retracement**: 39 unit tests passed; lint/build passed. Full drawing browser gate passed below.

The four projects and physical-device/RAF limitations remain those documented in VERIFICATION.md. All future padding contains time-only whitespace, never OHLC. No independent reviewer sign-off has been obtained.

## Full drawing gate

39 unit tests, lint and production build passed. Full browser run: 48 passed, 4 expected skips, zero failed (52 project cases, 3.6 minutes). Includes Phase 1, new tools, four-corner touch, production offline PWA and original stress coverage. No Phase 1 interaction regression found.

## Fundamentals and final Phase 2 gate

Clean install and required commands executed on 2026-10-06, Node 24.19.0:

| Command              | Result                                                                        |
| -------------------- | ----------------------------------------------------------------------------- |
| npm ci               | PASS, 212 packages installed from lockfile                                    |
| npm test             | PASS, 63 unit/regression tests in 8 files                                     |
| npm run lint         | PASS, zero errors/warnings                                                    |
| npm run build        | PASS, browser assets + standalone SEC API bundle                              |
| npm run test:browser | PASS, 57 passed / 7 expected skips / 0 failed (64 project cases, 4.8 minutes) |

npm reports the existing ESLint 9 release's deprecation notice. Production build has non-fatal upstream Zod annotation warnings; no failing check or >500kB chunk warning. Lightweight Charts stays pinned to 5.2.1. No paid key/card/license or new project initialization.

Financial browser subset separately passed 8/8 cases: desktop/mobile Chromium, iPad/iPhone WebKit; four official fixture company patterns, statements, annual/quarterly, source audit, missing/error states, delayed response isolation and unchanged chart dimensions.

Unit additions cover generic CIK aliases, TTL cache/in-flight dedupe/serialized request timing/429 cooldown, wrong-CIK rejection/retry, normalized browser provider isolation, backend input validation, concept priority and fallback, instant/duration/units, annual/comparative fiscal periods, aligned quarter derivation, unsafe baselines, duplicate/amended/conflicting facts, missing EPS, signed/falling CapEx, FCF/gross-profit lineage, margins, fiscal growth with gaps and provenance validation. Four actual SEC fixtures verify AAPL/MSFT/NVDA/TSLA; fixture hashes/source/filter provenance are in tests/fixtures/sec/provenance.json. Synthetic facts are test-only, never app financial data.

## Live SEC evidence

[Standalone API smoke](sec-api-smoke.json) uses the final built Node backend and official SEC network, not fixture substitution. It resolves CIK and returns both annual/quarterly data for AAPL, MSFT, NVDA and TSLA. Representative annual filings remain 10-K sources; per-metric audit preserves later comparative instant filings independently.

[Live production browser smoke](live-financial-browser-smoke.json) opens AAPL Financials through production preview's actual SEC backend, validates the entire 73-period response in the browser, renders revenue and expands the actual accession audit. Zero runtime errors. Screenshot: scratch/live-financial-desktop.png.

These are availability/correctness smoke observations on this date, not guarantees about future SEC access or every issuer/tag. No financial fixture is bundled into UI.

## Performance, preview and limitations

The original 2,500 bars / 8 SMAs / 100 Trend Lines regression remains unchanged. A separate 100-object mixed Trend/Horizontal/Ray/Rectangle/Fib scenario verifies native pan/zoom, lock immutability and no runtime errors, with scratch/stress-phase2-mixed.png. Neither is a physical FPS measurement. The original scheduling JSON is refreshed by the complete run; it measures headless RAF gaps only.

Physical iPhone/iPad Safari verification and measured device latency/FPS remain outstanding. Chromium mobile uses actual CDP touch; WebKit emulates touch modality on native captured pointers. No original mobile tests were removed or skipped to make new tools pass.

Standard mapped USD US-GAAP filings and ordinary calendar/52/53-week fiscal years are supported. IFRS/custom extension concepts, unusual fiscal transitions and absent standalone Q4 EPS can remain N/A. Quarter baseline alignment preserves explicit source vintage; unusual restatement comparisons require audit/review. Future chart dates retain Phase 1's non-holiday-aware schedule limitation.

Local preview: http://localhost:4173; development: http://localhost:5173. Bindings are 0.0.0.0; use Codex Cloud port forwarding if available. No tool-provided public URL or GitHub Pages deployment. Standalone SEC API is on 127.0.0.1:8788. Source workspace remains authoritative.

Independent Gemini/Claude reviewer unavailable; [REVIEW_PACKAGE.md](../REVIEW_PACKAGE.md) includes eight review priorities, source/fixture map, reproduction commands, primary self-check triage and independent findings template. No independent sign-off claimed. Stop at Phase 2 for user testing; no Phase 3 work.

The seven expected skips are one desktop-only touch case plus two controlled desktop load/measurement cases on each of the three mobile/WebKit projects. All mobile drawing and financial interaction cases ran. No Phase 1 regression was found.
