# PR #152 operational follow-up

Same branch `codex/seven-research-modules`; **do not merge**. No production data job or deployment was dispatched for this follow-up.

| Request | Result |
|---|---|
| Timestamp churn | Recursive semantic comparison; clocks only in five module indexes. Timestamp-free market files are only replaced when their bytes change. Unit test runs the actual volume builder twice and checks bytes, mtimes and Git diff. A second full-data replay checked all 10,384 records: **12 unchanged files, zero changed bytes**. |
| Commit frequency | Taiwan 11:23 UTC Mon–Fri; US 05:23 UTC Tue–Sat. Only the relevant market/modules run. One successful publish commit per run, none for unchanged output. Maximum **2/day, 10/week**, roughly 90% fewer than 20/day. Manual runs also consolidate publication. |
| Size | Five indexes + seven compact market files, replacing 10,389 files. All original semantic records and dates preserved; frontend retrieves an individual record by byte range. |
| SEC identity | `secrets.SEC_USER_AGENT` only, matching the existing backfill convention. No fallback email or repository variable. Unconfigured SEC skips clearly without network/data changes; other modules continue. |
| Permissions | Global/collector `contents: read`; collector credential persistence disabled. Allowlisted JSON artifacts feed one `contents: write` publisher. Checksums/path sets checked before overlay; no code from artifacts is executed. |

## Storage budget

Measured under `docs/report/research`, including all indexes:

| Measure | Before | After |
|---|---:|---:|
| Tracked JSON file count | 10,389 | **12** |
| Uncompressed bytes | 158,722,480 | **103,409,630** |
| MiB | 151.37 | **98.62** |
| Reduction | — | **55,312,850 bytes / 34.85%** |

The after size is the total payload added by this PR under that directory; main has no research directory. The largest individual file is the listed Taiwan institutions market at 33,466,411 bytes. Shape tables remove repeated field names while one-record-per-line output keeps Git history delta-friendly. No per-symbol files remain. 7,317 volume, 1,966 institutional, 971 SEC stock records and 130 dated turnover records were compared with the preceding commit, excluding only run clocks. Observation dates, official publication dates, negative values, nulls and historical calculations are unchanged.

**Expected daily Git growth:** zero bytes/commits for an unchanged run; plan for approximately **2–4 MB per normal trading day after delta packing**. The reproducible latest-session replay measured a **1,907,836-byte thin pack**. The planning range doubles that result to allow for omitted rolling volume aggregate changes and source corrections. Before GitHub's server-side delta packing, all current snapshot blobs compress to **22,583,239 bytes**; budgeting about **23 MB/day of new compressed blobs** is more conservative. Backfills, new issuers, corrections and changes in packing can exceed either estimate. These are measured planning estimates, not a guaranteed storage cap.

The replay removes the latest real session, recomputes institutional estimates using archived official rows, rewinds the 21 volume features, removes the most recent event/session and daily world observation, then compares that state to the actual committed payload. It holds volume aggregate fields fixed because the complete private historical price cache is unavailable locally; this limitation is recorded in the receipt. It does not fabricate or publish historical prices.

Existing PR commits and their objects are retained; this is a normal push, **no force-push/history rewrite**. Compaction reduces the checked-out payload and future growth, not the bytes already stored in those commits. A later squash merge can avoid importing those intermediate commits; this follow-up does not perform or authorize a merge.

Reproduce:

```sh
python -m unittest discover -s tests -p test_research_operations.py -v
python scripts/research_storage_audit.py --out /tmp/research-storage-audit.json
python scripts/research_coverage.py
```

Receipts: [storage audit](research-verification/storage-audit.json), [full-data no-change run](research-verification/noop-run.json), [coverage](research-verification/coverage.json), and [desktop/iPhone real Pages requests](research-verification/pages-live-smoke.json).

## Validation

The new operations tests cover nested clocks, publication-date changes, reversible field tables, UTF-8 byte offsets, the US ticker TW/market-name collision, real-builder no-change reruns, missing SEC contact/no requests, SEC event receipts after rehydration, artifact allowlists/checksums/atomic rejection, schedules and permission boundaries. TypeScript tests additionally cover HTTP 206, HTTP 200 fallback, deployment races, malformed shapes and stock isolation. Desktop and iPhone WebKit read actual market byte ranges without mocks; no runtime errors or horizontal overflow.

Final test totals and the two required CI runs are recorded in the PR and the delivery response after execution. Main publication/deployment is intentionally not run before merge. After a future authorized merge, inspect `Research public data` artifacts and its single publish commit, then verify stock ranges return HTTP 206 and the four world URLs remain valid. For SEC, set the existing Actions secret if needed; an unset secret deliberately skips only that module.
