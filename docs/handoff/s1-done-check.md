# S1 done check (plan §3.5), run 20

Reviewer: Claude Opus. Date: 2026-10-17.
Reviewed: `origin/claude/s1-run20-output` at e44f58f46ed440f988ff2809419887a1b096a2ed (PR #116, base origin/main).
Run: Actions run 39039050667 (S1 run 20), read from `origin/runs/q4-8k:docs/report/q4-8k/runs/39039050667/`.
Spec: `s1-opus-decision.md` §4, `auto-s1-plan.md` §3.4/§3.5, `auto-s1-decision-2.md` §3/§4.

## Verdict: PASS. S1 is done.

All seven "S1 done when" items hold. The offline replay reproduces the run's output exactly, and the test
suite passes with the skip-guarded S1 tests running.

## 1. "S1 done when" items (`s1-opus-decision.md` §4)

1. **OK.** `s1-no-table-layouts.json` has generator `classify_row` and status `complete`. It has rowCount 235 and
   235 distinct (key, end) rows. Each row has exactly one class. (Decision-2 §4 moves the source run from 19 to 20.)
2. **OK.** L9 is 13, against a limit of 23 (`otherLimit.passed` true). Each L9 row has a specific note:
   - TXT 2011, 2017, 2018 and 2020 (b): net income only in a period-named table without EPS rows;
   - VIAV 2015-06-27 (f): only the next-quarter table was found ("October 3, 2015 September 27, 2014");
   - VIRC 2025-01-31 (d): a prose money match only;
   - WOR 2012–2017 and 2019 (f): the header also names "3Q".
3. **OK.** Each class has a count, a disposition and 2 example URLs. The exception is L7, which has 0 rows and so
   has no examples; this is not a failure. Dispositions:
   - L1, L2, L3 and L8: S2 handles;
   - L4, L5, L6, L7 and L10: out of scope;
   - L9: needs Opus.
4. **OK.** Each S2-handles class has 3 positives: L1, L2, L3 and L8. There are 5 negatives. All 17 fixtures have
   `roundTrip` true, are at most 64 KB, and total 316,877 bytes.
5. **OK.** The dev tickers are TRAK, TXT, UI, UNFI, URBN, VIAV, VNCE and WLY. They are the same in the manifest and
   in the layouts file, and they are exactly the tickers of the 17 fixtures.
6. **OK.** `python3 -m unittest discover -s tests -p 'test_*.py'` on the branch ran 1153 tests: OK, none skipped.
   The skip-guarded `s1_report_is_classify_row_output` tests ran.
7. **OK.** The S1 runs are 18 (39028455462), 19 (39035520759) and 20 (39039050667), which is 3. The workflow run
   list shows no other run after 18. Each of the three has stopped null, and none was Forbidden or 403.

## 2. Run 20 integrity

- `run-meta.json`: extractOutcome is `success`, mode is diagnose, and mainSha 73449806 is the S1-CF merge (#115).
- `s1-classes.json`: status is `complete`, and `diagnoseRun.stopped` is null.
- The verbose file has sourceRows 235, diagnosed 235 and 2630 requests.
- The sha256 values match the run 20 README:
  - run-meta: ea74e41a…7d0145;
  - s1-classes: 35050330…a94a53c;
  - summary: 142719fd…b97b;
  - verbose: 4e163577…ded6.
- `docs/report/q4-8k/s1-no-table-layouts.json` is byte-identical to run 20's `s1-classes.json` (cmp).
- All 17 fixture HTML files in `tests/fixtures/q4_8k/` are byte-identical to run 20's `fixtures/`.
- Each manifest sha256 and byte count matches its file.
- The branch manifest differs from the run's manifest only in `epsDilutedRead`, `epsLabelRead` and
  `epsReadNote`, and only on positives.
- The branch changes no code relative to 73449806.

## 3. Offline replay

I extracted `verbose.json.gz` outside the repository. Using the branch's `classify_row`, I classified all 235
rows with the stored `classification` removed.
- The 8 keys match row for row, with 0 mismatches.
- The full classification dict matches the verbose file's for every row.
- `build_s1_classes` on the replayed rows, with the run's manifest, run id and commit, rebuilds a document equal to
  `s1-no-table-layouts.json`.
- The counts are L1 13, L2 74, L3 7, L4 27, L5 8, L6 8, L7 0, L8 62, L9 13, L10 23. This is as expected.

## 4. Spot check (2 per class, random.sample seed 20261017; L7 has no rows)

Each row below was checked by hand against the verbose evidence: header, NI rows, strict matches, the EPS-table
flags, `tableEps` and the preceding tables. The rows that are also fixtures were checked against the fixture HTML
too.

- **L1, WLY 2023-04-30 t6: OK.**
  - The header "EndedApril 30" is not period-named.
  - Net Income 68,344K equals the target.
  - Table 5 is period-named with quarter wording.
- **L1, TRMB 2011-12-30 t19: OK.**
  - The header "Dec-30,2011" is not period-named.
  - GAAP NI 29,403K equals the target.
  - Table 18 is period-named with quarter wording.
- **L2, WMT 2021-01-31 t13: OK.**
  - The years are in a separate row (C5).
  - "…attributable to Walmart" (−2,091M) equals the target.
  - No preceding table names the period.
- **L2, ULTA 2014-02-01 t26: OK.**
  - The header has "February 1,2014" with no space.
  - Net income 70,682K equals the target.
- **L3, UNFI 2019-08-03 t13: OK.**
  - The accepted label (18,937K) is 1.4% off the target and is not strict.
  - 19,160K is under "…including noncontrolling interests", which is not accepted. It is within the slack
    (37K ≤ 38.4K).
- **L3, UI 2014-06-30 t14: OK.**
  - 49,418K is only under "Net income and comprehensive income", which is not accepted.
  - The accepted "Net income" is in table 15, which is not an EPS table.
- **L4, WLY 2018-04-30: OK.**
  - The closest value is 54,060K against 54,200K (0.26%, outside the 0.2% slack).
  - There is no prose money match.
- **L4, ZM 2019-01-31: OK.**
  - Only the Q1 FY20 release is present, and it has no strict NI match against 5,697K.
  - There is no prose money match.
- **L5, TRAK 2025-06-30 t20: OK.** The only EPS table is "For the Years Ended June 30". (The prose match does not
  matter, because L5 comes before L4.)
- **L5, TRAK 2024-06-30 t18: OK.** This is the same layout.
- **L6, ZUMZ 2012-01-28: OK.** `zumiezinc.htm` is a text-only release with 0 tables.
- **L6, WLYB 2017-04-30: OK on the evidence.** Only the index pages were read. See the observation below.
- **L8, ZWS 2024-03-31 t1: OK.**
  - Net income 34.3M equals the target.
  - The heading is "Diluted net income per share:", but `epsRowFound` is false.
- **L8, UNFI 2015-08-01 t7: OK.**
  - Net income 36,098K equals the target.
  - The table has a "Diluted per share data:" heading, and `epsRowFound` is false.
- **L9, WOR 2012-05-31 t0: OK, (f).**
  - The header is "4Q 2012 3Q 2012 …".
  - Net earnings is 52.1M against 52.077M.
- **L9, WOR 2014-05-31 t0: OK, (f).** This is the same layout: 33.2M against 33.163M.
- **L10, ZUMZ 2021-01-30 t0: OK.** The NI and EPS rows have different column counts. NI 42,797K equals the target.
- **L10, UI 2024-06-30 t3: OK.** Two columns match: 103,804K and 103,677K are both within the slack.

## 5. Fixture counts and the manifest review (`s1-run20-review.md` on `origin/claude/s1-run20-review`)

- The positives are L1 3, L2 3, L3 3 and L8 3, so every class with positives has at least 2.
- There are 5 negatives: TXT-2016 (L2), WLY-2018 (L4), TRAK-2020 (L5), VNCE-2026 (L1-year-header) and TXT-2020
  (L8-non-gaap-diluted-row). That meets the minimum of 5, so this is not a stop.
- Each fixture's class equals its row's class in the report.
- I re-read all 12 positive values from the fixture HTML myself. All 12 match review items 1 and 4:
  - WLY 1.22 (table 4);
  - URBN-16 0.61;
  - VNCE −0.37;
  - TRAK 0;
  - TXT-22 0.93;
  - URBN-14 0.59;
  - UI 0.55;
  - UNFI-19 0.36;
  - VIAV −0.01;
  - TXT-12 0.51;
  - UNFI-13 0.65;
  - URBN-12 0.27.
- Neither TXT table has a basic row.
- Review edits 1–3 (the two TXT values and the WLY note) are applied word for word, and `targetTableIndex` stays 6.

## 6. Fixture errors (7)

- TRMB 2011-12-30 is "too large". Decision-2 §3 accepts this failure, and the 64 KB cap stays.
- The six WSM rows have the error "vi is never an auto-positive". This is required by decision-2 §4 and S1-CF C13.
- Neither is a stop condition, because the fixture counts still meet the rules (L1 and L2 have 3 each).

## Observation (not blocking)

WLY and WLYB 2017-04-30 are L6 "only EDGAR index pages". In both filings the two `EXTRA_DOCS` slots went to the
index pages, so the 8-K body (`fy17q4-8k.htm`, `fy18q1-8k.htm`) was never read. The release may be in that body.
L6 is out of scope, and S1 is not affected. S2 or later work may want index pages excluded from the extra-document
slots.
