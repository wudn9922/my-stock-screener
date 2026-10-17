# S1 decision (Opus), 2026-10-17

Author: Claude Opus (decider). Audience: Codex and Claude. Applies on top of `q4-eps-s1-s5-codex.md` and
`q4-eps-s1-s5-amendment-1.md`, which are not edited. Where this file differs from them, this file wins.

Reviewed: `origin/codex/s1-diagnose` at 58384fd9 (base e66c5d79), the run 18 diagnose report
(`docs/report/q4-8k/input/diagnose-run-18/q4-8k-report-diagnose.zip`, sha256 d7e9898b…, 235 rows), and the
answer set at 95184ade.

## 1. Verdict: S1 is not complete (fail as submitted)

The verdict rests on three findings. Each finding can be reproduced from the run 18 JSON.

**F1. `other` is over the limit.** L9 has 36 rows against a limit of 23 (10% of 235). This alone blocks S1.

**F2. The 36 L9 rows are one mechanism, not "other".** Every one of them has a table in the nearest candidate
filing that has all three of these:
- the table is period-named;
- an accepted NI label (`is_net_income` true) matches the target;
- the match is in one column.

In that case `table_eps` returns `(None, None)` only when it finds no recognised diluted-EPS row in the table.
The other possibility is that it rejected the table at a check, and `find_quarter` then reported the *last*
filing's generic reason, hiding that rejection.

Examples:
- URBN, UFI and WMT use a heading "Net income per common share:". It does not contain "per share", so the
  Basic/Diluted rows under it are never read.
- WRLD labels the row "Net income per common share, diluted".
- TXT uses a heading "Diluted earnings per share:" followed by a row labelled "Net income".

**F3. L2 (134 rows) is contaminated by spurious matches.** `target_matches` and `table_eps` accept a value if
it is within `max(NI_TOLERANCE·|T|, 0.5·scale)` of the target T. At scale 1e9 the slack is ±500M, so any
per-share value in a row labelled "Net income" "matches" any target under about $500M.
- Example: TXT 2016-12-31, target 214M. The Q1 2017 release (0001104659-17-024320, table 2, "Three Months Ended
  April 1, 2017") "matches" through EPS 0.37 and 0.55.
- All 9 TXT L2 rows quote a next-quarter header ("Three Months Ended March 31, 2012 …") as their L2 evidence.
  Other rows built on another quarter's header: TRAK 2023-06-30, UNFI 2013-08-03 and UNFI 2015-08-01.
- With a strict match (defined in §2), 47 of the 134 L2 rows also have a period-named table that holds the
  target NI under an accepted label. The parser's miss is then in that table, not in the header wording.
- Eight L2/L3 rows have no strict match at all and belong to L4.
- 5 of the 6 positive fixtures (TXT and ULTA for L1, WOR for L2, UFI and UI for L3) are rows of this kind. Their
  assigned class is not the cause of the miss.

My re-derivation from run 18 data (strict match, fixed precedence, assuming every such period-named table is an
EPS table) puts about 104 rows in that mechanism. This is an **upper bound**, not a class count:
- reconciliation, segment and cash-flow tables carry NI without EPS by design (UI 2014-06-30 table 15 is one);
- run 18 kept only the first 3 rows of each table, so it cannot separate those tables from income statements
  with a missed EPS label.

That is why the fix below is a code change plus one more run, not a hand re-classification.

**Decision: it is both a code change and a classification change.** The class is no longer assigned by hand. A
diagnose-only classifier assigns it, and the JSON is that classifier's output. No hand overrides.

## 2. Fixed class set and precedence (replaces the S1 class table)

**Strict match** (used by the classifier): |v·s − T| ≤ max(0.002·|T|, 0.5·s·10^−d), where d is the number of
decimals shown in the cell. Example: 0.37 at scale 1e9 gets a slack of 5M, not 500M.

**EPS table:** a table with at least one row whose label contains "diluted" and one of "per share",
"per common share", "per diluted share", "eps" or "earnings per", or such a row under a per-share heading. A
period-named NI table that is not an EPS table (a reconciliation, segment or cash-flow table) is ignored by
rules 3–5.

**Precedence:** the first rule that holds assigns the class. NI matches are strict; "accepted" means
`is_net_income` is true.

| # | Class | Rule | Disposition |
|---|---|---|---|
| 1 | L7 `no-release` | as before | out of scope |
| 2 | L6 `not-html-table` | as before | out of scope |
| 3 | **L10 `check-reject`** (new) | A period-named EPS table holds the target NI under an accepted label, and `table_eps` rejects it with a specific reason (more than one column or unit, column counts, EPS empty/zero/≥1000, sign, shares). The run records the reason. | Out of scope for new rules. S2 only fixes the reason reporting and the slack (§5). |
| 4 | **L8 `eps-row`** (new) | A period-named EPS table holds the target NI under an accepted label in one column, and `table_eps` finds no diluted-EPS row. This covers a diluted per-share row `is_eps` misses, or a heading `table_eps` does not recognise. | S2 handles: label and heading variants covered by fixtures only. |
| 5 | L3 `ni-label` | A period-named EPS table holds the target NI only under a label that is not accepted. | S2 handles |
| 6 | L1 `period-in-earlier-table` | An accepted NI match in a table that is not period-named, where one of the 3 preceding non-empty tables names the period with quarter wording. | S2 handles |
| 7 | L2 `period-wording` | An accepted NI match in a table that is not period-named, whose own header names the target quarter in a variant form and names no other quarter end. | S2 handles |
| 8 | L5 `no-quarter-column` | as before | out of scope |
| 9 | L4 `ni-value` | No strict NI match anywhere in the read documents. Record the closest value. | out of scope |
| 10 | L9 `other` | Anything else. Each row needs its own specific one-line note; the generic "does not meet L1–L7" is not accepted. | needs Opus |

Further rules:
- A class is "S2 handles" only if it has at least 2 real positive fixtures. Otherwise it is "out of scope" for
  this round.
- If run 19 still gives L9 > 23, stop and report the L9 notes. Do not add classes yourself; Opus decides.

## 3. Merge decision for `origin/codex/s1-diagnose`: merge with named changes

What I checked:
- The diff touches no financials, growth, answer-set or state file.
- Diagnose mode prints to stdout, and the workflow commits only in `fill`.
- `find_quarter`, `from_filing`, `table_eps`, `process_ticker` and `apply_fill` are unchanged, so fill behaviour
  does not change.
- `html_tables_of` mirrors `tables_of`, including the nested-table replacement.
- 50 unit tests pass (`test_q4_eps_diagnose`, `test_backfill_q4_eps_8k`).

The workflow always runs diagnose from `origin/main`, so the changes below must be on main before run 19. Codex
adds them as commits on `codex/s1-diagnose`. Claude reviews against this list, runs the tests, and merges with a
normal merge commit.

- **M1. Classifier (diagnose-only).**
  - For every period-named table, record the outcome of `table_eps`: result yes/no, its reason, whether an EPS
    row was found, and the diluted per-share labels.
  - Record strict-match flags.
  - Add `classify_row()`, implementing §2 exactly, with unit tests for each precedence step.
  - Nothing outside diagnose mode calls it.
- **M2. Multi-table export.** An L1 fixture must contain the target table and its up to 3 preceding non-empty
  tables, in document order, with an HTML comment marking the target. The selection spec gets `tableIndices`.
- **M3. Trim and round-trip.**
  - Strip every attribute except `colspan` and `rowspan`.
  - Cap each fixture at 64 KB; a larger one becomes a `fixtureError`.
  - For each fixture, record `roundTrip`: `tables_of(fixture)` must equal `tables_of(source)[i]` cell for cell.
    A false value is a fixture error.
- **M4. Small artifact.**
  - The diagnose step writes a compact `s1-classes.json` (one record per row: key, end, class, accession, url,
    tableIndex, table_eps reason, note) and a `fixtures/` directory, and uploads only those.
  - The full verbose dump stays off by default.
  - Limit: zip ≤ 1.0 MB. If it would be larger, split the upload into two artifacts of at most 1.0 MB each.
- **M5. Selection manifest and the files the run produces.**
  - Replace `s1_fixture_selection.json` with a manifest that has `autoPositives: {"L1":3,"L2":3,"L3":3,"L8":3}`.
    The run picks, per class, the first rows (sorted by key, then end) whose *computed* class is that class: at
    most one per ticker, preferring tickers already selected.
  - It also lists these negatives explicitly:
    - TXT 2016-12-31, 0001104659-17-024320, `a17-11537_1ex99d1.htm`, table 2. It is the next-quarter table, a
      negative for both the slack and L2.
    - WLY 2018-04-30 (L4) and TRAK 2020-06-30 (L5), as today.
  - The run chooses these negatives from its own data and records them in the manifest:
    - one L1 negative: the preceding header is a twelve-month or misaligned header;
    - one L8 negative: a diluted per-share row that must stay rejected (non-GAAP, adjusted, or continuing
      operations).
  - Drop `docs/report/q4-8k/s1-no-table-layouts.json` from this merge. The run 18 draft is superseded, and the
    regenerated file comes after run 19.
  - Replace `S1ClassificationReportTests`. Tests must not assert `L9 == 36` or `otherLimit.passed == False`.
    Re-add spec assertions once the regenerated file exists:
    - L9 ≤ 23 and the limit check passes;
    - every fixture's class equals its row's class in the report.

## 4. Fixtures

- **How and when.** One more Actions diagnose run (run 19, the second S1 run of the 3 allowed), dispatched on
  main after the M1–M5 merge. It re-diagnoses all 235 rows (run 18 used about 2,630 requests, and the budget is
  6,000). In the same pass it classifies the rows and exports the fixtures, so no third run is needed.
- **Size.**
  - Artifact zip ≤ 1.0 MB, well under the ~1.6 MB size at which earlier uploads were truncated.
  - Each fixture ≤ 64 KB after trimming; all fixture HTML together ≤ 1.2 MB uncompressed.
  - Raw HTML is exported only for the selected cases: up to 12 auto-positives and 5 negatives.
- **Where.**
  - Fixture files: `tests/fixtures/q4_8k/<KEY>-<end>-<class>.html`, and
    `tests/fixtures/q4_8k/<KEY>-<end>-<class>-negative.html` for negatives.
  - Each file starts with `<!-- Source URL: … -->`.
  - Manifest: `tests/fixtures/q4_8k/s1_fixtures.json`, recording for each file the key, end, class, role,
    accession, document, tableIndices, sha256, and (for positives) the diluted EPS that Codex read from the table.
  - Claude commits the uploaded artifact under `docs/report/q4-8k/input/diagnose-run-19/` with its sha256, as for
    run 18.
- **S1 done when:**
  1. `s1-no-table-layouts.json` has been regenerated by `classify_row` from run 19, has status `complete`, and
     has 235 rows, each in exactly one class.
  2. L9 ≤ 23, each with a specific note.
  3. Each class has a count, 2 example URLs and a disposition.
  4. Each "S2 handles" class has at least 2 real positive fixtures, and at least 5 negatives exist. All fixtures
     pass the round-trip.
  5. The dev tickers are recorded; they are the tickers of every fixture.
  6. The tests pass.
  7. No more than 3 S1 runs were used, and there was no 403.
- The synthetic fixtures already on main (`L1-earlier-table.html` and the others) remain unit-test inputs. They
  do not count as S1 fixtures.

## 5. Next steps

### Codex, now

1. M1–M5 on `codex/s1-diagnose`, then hand it to Claude.
2. In parallel, on a separate branch `codex/s2a`, work that needs no fixtures and only tightens behaviour:
   - (a) S0 section of `docs/handoff/q4-eps-s1-s5-codex-report.md`, with the numbers in §6.
   - (b) S3.1 `build_q4_eps_answer_set.py`, pinned to 95184ade, with the answer value from
     `secQ4EpsFacts[end].epsDiluted.value` (amendment 1). Check 2,649 rows, 563 files, and 2,387 basic.
   - (c) `--answer-set`, `--shard` and `--mode gate` (S2.6, S3.3).
   - (d) The S2.5 confirmation tolerance.
   - (e) In `table_eps`, the decimals-aware half-unit slack from §2. It only removes matches.
   - (f) `find_quarter` keeps the first specific reason across filings instead of the last generic one.

   List every existing test whose expectation changes, with the reason. Do not run S3.

### Codex, after S1 is done (S2b)

Add one rule per commit, each with its real fixtures (at least 2 positives and at least 1 negative). Order: by
the final S1 count, largest first. The expected order is L8 (EPS label and heading variants), then L2, then L1
(the look-back, exactly as in S2.1), then L3.

For L2, a fiscal-year label one year below the end year (e.g. TRMB "Fourth Quarter of Fiscal Years 2013" for
2014-01-03) is accepted only when the period ends within the first 7 days of January. It needs a fixture.

S2b must not start before the run 19 fixtures are committed. Then run S3 once.

### Claude

1. Review the amended `codex/s1-diagnose` against M1–M5, run the tests, and merge it (go given, conditional on
   that list).
2. After the upload, verify the zip's size and sha256, and commit it under `input/diagnose-run-19/` via a
   branch.
3. Check S1 done against §4.
4. Review and merge `codex/s2a`, then S2b.

Merge nothing that touches financials, growth or the answer set.

## 6. S4 preconditions (re-verified 2026-10-17)

- **P2 holds.** 9cbc805a ("Merge pull request #102 from wudn9922/data/opus-steps") and 95184ade are both
  ancestors of `origin/main` (e66c5d79). No file under `docs/report/financials/` differs between 95184ade and
  main.
- **Answer set holds.** At 95184ade: `epsDiluted` A = 2,649 Q4 rows in 563 US files, all with `netIncome`;
  `epsBasic` A = 2,387. Pinning is still to be implemented: `answer-set.json` and its builder do not exist yet
  (S2a item b), and they must use ref 95184ade.
- **P3 baseline: values confirmed, record missing.** I recomputed S0 at 95184ade: strict TTM 50, default 3,082,
  A-in-TTM 56, gap BNC, LFCR, MRDN, NTRP, SPHR, TULP, matching the expected numbers. They are not recorded in any
  file on main or on any remote branch: `q4-eps-s1-s5-codex-report.md` does not exist. Codex must write them
  (S2a item a) before S4. The post-merge recount on main gives the same numbers today, since the financials are
  identical.
- **P1:** pending S3.
