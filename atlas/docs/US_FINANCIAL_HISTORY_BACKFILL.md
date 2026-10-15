# US financial history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The exact tracked US symbol snapshot contains 637 symbols (637 financial files). The ordered snapshot SHA-256 is `2a65e204927804a2601229cf23d4f7649665ea5157de712ba110fd77f5958d9e`.

The refresh used the existing SEC companyfacts seeder with its configured request pacing, User-Agent, retry behavior, merge rules, and split checks. The seeder was run with `--force`; SEC periods, nullable fields, concept lineage, filing metadata, and quality flags remain in the source files.

## SEC refresh run

Command from the repository root: `/tmp/tw-mops-backfill-venv/bin/python /tmp/sec_any_403_guard.py atlas/scripts/seed_financials_sec.py --force`. The temporary guard launcher and response cache lived under `/tmp`, outside this repository. It preserved the seeder’s configured request pace, User-Agent and retry behavior, and stopped immediately on the first SEC HTTP 403; the run completed without a 403. Split checks remained enabled by omitting `--no-splits`.

Results: 71 written, 526 unchanged, 40 skipped. Skip reasons: foreign-filer 32, no-us-gaap 1, non-usd 2, revenue-scope-mismatch 4, short-history 1.
SEC requests: 639; response bytes: 149231725; financial/index JSON bytes written: 1342417. Unique cached SEC bodies: 635 (148701804 bytes). Split checks: Yahoo connected, 122 chart requests; 40 tickers with split events, 107 adjusted, 0 pending. 516 implausible values in 153 tickers were left null.
New financial files in this tracked snapshot: BLDR, CTVA, PSKY, TAP, WBD.

## Offline Yahoo null restoration

The SEC refresh kept its reported values and lineage. An offline pass used the pre-refresh Git snapshot and the standard period matching/null-fill rules to restore only missing cells in retained SEC periods; no period or SEC non-null value was changed. It planned 140 annual and 197 quarterly Yahoo null fills across 12 files. EPS fills additionally require matching fiscal labels, unchanged split metadata, and diluted shares within 1%.
The pass left 44 annual and 54 Yahoo reported-operating-income aliases null where a SEC operating-income value already exists, preserving the UI's SEC-preferred value. It also checks old gross profit and cost values against revenue and their counterpart before filling them.
The exact per-field counts and reviewer-triaged AGM, EEFT, LOPE, PLUS, and BB checks are in [`financials-us-yahoo-null-restoration.json`](../../docs/report/financials-us-yahoo-null-restoration.json). Re-run the offline pass with `python atlas/scripts/restore_yahoo_nulls_from_git.py --baseline-revision e0827e8b106bcc9ab2a5848ec08c2f480b538fe6`.

## Coverage

The calendar window counts only period ends on or after the cutoff. The latest-record window separately counts the latest 15 annual or 60 quarterly rows where available. Neither count implies that every requested field is populated or that the periods form a gap-free series. The target-complete counts require at least 15/60 selected periods and every displayed metric input present throughout that symbol’s selected window; the seven-field count requires all seven metrics to meet that rule. Cell coverage percentages describe the observed period rows and do not imply a complete 15-year history.

| Window | Period type | Rows in window | Rows in source file | Symbols with target rows | Symbols with target contiguous rows | Symbols complete on all 7 fields at target | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 8132 | 9655 | 422 | 413 | 127 | 15 |
| Calendar cutoff | quarterly | 31324 | 31843 | 48 | 48 | 9 | 60 |
| Latest records | annual | 8172 | 9655 | 454 | 440 | 146 | 15 |
| Latest records | quarterly | 31789 | 31843 | 440 | 414 | 48 | 60 |

### Field cells within the 15-calendar-year cutoff

| Period type | Metric | Before available | After displayed | Change (raw for cost) | Raw before | Raw after | Derived after | Missing after | Cells | Symbols complete at target | Coverage after |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 7738 | 7908 | +170 | — | — | — | 224 | 8132 | 399 | 97.25% |
| annual | revenue | 7574 | 7723 | +149 | — | — | — | 409 | 8132 | 355 | 94.97% |
| annual | costOfRevenue | 4496 | 4954 | +128 | 4496 | 4624 | 330 | 3178 | 8132 | 167 | 60.92% |
| annual | nonOperatingIncome | 6128 | 6285 | +157 | — | — | — | 1847 | 8132 | 281 | 77.29% |
| annual | grossMargin | 4788 | 4916 | +128 | — | — | — | 3216 | 8132 | 164 | 60.45% |
| annual | operatingIncome | 6412 | 6569 | +157 | — | — | — | 1563 | 8132 | 306 | 80.78% |
| annual | netMargin | 7464 | 7602 | +138 | — | — | — | 530 | 8132 | 342 | 93.48% |
| quarterly | eps | 29230 | 30003 | +773 | — | — | — | 1321 | 31324 | 34 | 95.78% |
| quarterly | revenue | 28654 | 29348 | +694 | — | — | — | 1976 | 31324 | 39 | 93.69% |
| quarterly | costOfRevenue | 16426 | 18517 | +588 | 16426 | 17014 | 1503 | 12807 | 31324 | 22 | 59.11% |
| quarterly | nonOperatingIncome | 22463 | 23184 | +721 | — | — | — | 8140 | 31324 | 19 | 74.01% |
| quarterly | grossMargin | 17762 | 18358 | +596 | — | — | — | 12966 | 31324 | 22 | 58.61% |
| quarterly | operatingIncome | 24157 | 24879 | +722 | — | — | — | 6445 | 31324 | 25 | 79.42% |
| quarterly | netMargin | 28098 | 28741 | +643 | — | — | — | 2583 | 31324 | 31 | 91.75% |

The seven metrics are evaluated as the page displays them: EPS uses diluted EPS then basic EPS; operating income prefers the listed line then the general operating-income field; gross margin uses reported gross profit or revenue minus cost of revenue; net margin requires positive revenue; non-operating income is pretax income minus operating income. Cost of revenue shows the reported raw value when present, otherwise it may display the same-period revenue-minus-gross-profit estimate under the conservative rules in the UI. The estimate is never written into the source JSON; the table separates raw and derived counts. For cost of revenue, the change compares raw-reported cell counts so display derivations are not described as newly fetched SEC data.

Non-operating income is a derived net non-operating result, not a separately reported non-operating revenue total. The app marks that basis and shows the formula and inputs. SEC/Yahoo source marks apply to a period; Yahoo may fill a null cell in an SEC-sourced period. The operating-income labels therefore make no source claim, and the report preserves SEC concept lineage where it exists.

## Gaps and missing values

The JSON companion lists each ticker’s date spans, field availability, missing-input reason counts, SEC concept lineage, quality issues, and optional refresh outcome. A period is counted as present when it appears in the source file; a metric cell is counted only when the inputs required for that displayed metric exist. A reported null remains null.

A consecutive-period check allows 330–395 days between annual ends and 75–120 days between quarter ends to accommodate fiscal calendars and 52/53-week years. The check is evidence about date spacing, not proof that a company reported every economic line item.

The scope is the existing tracked US snapshot from `build_financials.collect_symbols()`; its exact symbol count and ordered hash appear above. The broader universe catalog was not substituted. SEC does not provide 15 years for every issuer, newly listed companies naturally have shorter histories, and some financial-sector statements do not expose cost of revenue or operating income in a comparable form. Such values remain unavailable instead of being filled with estimates.
For the latest-record window, 454 of 637 symbols have at least 15 annual rows and 440 have at least 60 quarterly rows. Only 146 annual and 48 quarterly histories have all seven displayed metrics populated throughout those target rows.

Baseline comparison uses commit `e0827e8b106bcc9ab2a5848ec08c2f480b538fe6` (2026-10-15T11:29:10+08:00); raw period files are read from Git at that revision. For cost of revenue, baseline availability is raw-reported only, while the after column also includes safe display-only derivations. These counts do not imply that every cell changed in this refresh.

Machine-readable per-symbol evidence: [`financials-us-coverage.json`](../../docs/report/financials-us-coverage.json). Offline generator: [`report_financials_us_coverage.py`](../scripts/report_financials_us_coverage.py).
Regenerate the report with `python atlas/scripts/report_financials_us_coverage.py --as-of 2026-10-15 --baseline-revision e0827e8b106bcc9ab2a5848ec08c2f480b538fe6 --refresh-log /tmp/us-15y-sec-refresh.log --raw-cache-manifest /tmp/us-15y-sec-raw/manifest.json --restoration-audit docs/report/financials-us-yahoo-null-restoration.json`. `--refresh-log`, `--raw-cache-manifest`, and `--restoration-audit` are optional provenance inputs.

## Validation

`npm ci` passed. The Python financials, SEC-seeder, growth, coverage-report, and Yahoo-restoration suites passed 230 tests; `npm test -- --maxWorkers=2` passed 806 tests across 66 files; `npm run lint` and `npm run build` passed. The financials browser regression passed 14/14 Chromium cases and 7/7 WebKit cases.

WebKit ran from Playwright browser files under `/tmp/us-15y-playwright`. Since host packages could not be installed system-wide, 28 Debian dependency packages were extracted under `/tmp/us-15y-webkit-deps`; five required library names were linked into Playwright’s temporary WPE bundle. `ldd` resolved the WPE browser and process binaries before running the tests. The host validation override was only for this user-space setup; the browser assertions ran normally. The WebKit command was `LD_LIBRARY_PATH=/tmp/us-15y-playwright/webkit-2359/minibrowser-wpe/lib:/tmp/us-15y-playwright/webkit-2359/minibrowser-wpe/sys/lib XDG_CACHE_HOME=/tmp/us-15y-cache XDG_CONFIG_HOME=/tmp/us-15y-config PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 PLAYWRIGHT_BROWSERS_PATH=/tmp/us-15y-playwright npm run test:site -- financials.spec.ts --project=iphone-webkit`.
