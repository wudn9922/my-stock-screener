# Automated S1: decision 2 (Opus), 2026-10-17

Author: Claude Opus (decider). Audience: the lead session, the Haiku implementer and the Opus reviewer.
This file applies on top of `auto-s1-plan.md` and `s1-opus-decision.md`. Where they differ, this file wins.
It does not loosen any gate, tolerance or standing rule:
- fill writes only null fields, and only with a recorded source;
- the answer set stays pinned to 95184ade;
- the S3 gate is unchanged;
- Codex writes main and Claude writes branches;
- Haiku implements and Opus reviews.

Inputs:
- the stop report `docs/handoff/auto-s1-report.md` on `claude/auto-s1-report` (PR #113);
- run 19 = Actions run 39035520759, read from `origin/runs/q4-8k:docs/report/q4-8k/runs/39035520759/`;
- `classify_row` on main (d773c713).

Replaying `classify_row` from main offline on run 19's `verbose.json.gz` reproduces `s1-classes.json` row for row (0 mismatches). Every number below comes from that file. I computed them with a scratch reference implementation of §5. Nothing was committed from it.

## 1. Verdict: (b). Resume after a named classifier fix (S1-CF), then run 20

The 92 L9 rows are mostly not a real "other" population. Three spec defects in plan §M1 cause them. The code implements those defects faithfully. They are mine (the M1 text), not the implementer's.

1. **Strict match has no precision cap.** The slack is max(0.002·|T|, 0.5·s·10^−d). At scale 1e9 with d = 0 that is ±500M, so:
   - a cell or token "0" matches every target with |T| ≤ 500M;
   - a single digit matches larger targets.

   Examples:
   - TWIN 2012 and WLY 2018: "0" under "Impairment charge" or "Journal Subscriptions";
   - VIAV 2016: "−0.1" under "Operating income (loss)";
   - VNCE 2019: "1" under "Earnings (loss) per share".
2. **`rawStrictTextMatch` scans raw HTML.** The scan includes CSS and markup ("margin-top:0", "#000000") and the EDGAR index pages. In run 19 it is true in 1,245 of 1,597 documents, and in at least one document of **every one of the 235 rows**.
3. **Rule 9 counts a strict match under any label.** Decision §2 says "no strict *NI* match". Coincidental matches under "Sale of property and equipment" or "Deferred income taxes" therefore block L4.

**Sub-case (d) (44 rows) is a classifier gap, not a population.** I checked the verbose evidence of several rows:
- TRAK 2014-06-30: ex99 has 0 tables. Its "raw" match is "0" in `<body style="margin-top:0">` and in the index page. The release has no HTML table, so the row is L6.
- ZUMZ 2012-01-28: a text-only release (`zumiezinc.htm`, 0 tables). The only "table" is the EDGAR index page. The row is L6.
- VIRC 2022-01-31: the income statement shows a net loss of −15,136K against a target of −16,312K (7.2% off). The raw match is a CSS "0". The row is L4.
- TWIN 2012-06-30: "0" under "Impairment charge" at scale 1e9. The closest NI is 1,251K against 1,263K (0.95% off). The row is L4.

**Sub-case (a) (39 rows) is mostly a header-reading gap.** It is mostly not a missing period. The checked rows:
- ULTA, VRA, VNCE 2015 and TXT 2022 write "February 2,2013" with no space, or "EndedFebruary". `shows_period` fails on that text. The prior-year comparative date ("January 28,2012") then trips `other_quarter_end`.
- WOR 2022, 2023 and 2026 use "4Q 2022", which is not in the quarter wording.
- WMT, ZUMZ 2024/2025, WRLD 2019 and TRMB 2018 put the years in a labelled year row, for example "2018 | 2017* As Adjusted". `header_text` stops at that row, so the year is lost.
- WSM uses the retail fiscal-year label "FOURTH QUARTER 2011" for the quarter ending 2012-01-29.

Real "other" rows remain:
- WOR 2012–2019 (7 rows): the header also names the sequential "3Q".
- VIAV 2015-06-27: only the next-quarter release was found.

**Sub-case (b) (9 rows, all TXT) is an EPS-table test gap.** TXT prints "Earnings per share:" followed by a "Net income" per-share row, plus "Diluted average shares outstanding", and no "diluted" in any per-share label. (This is from the run 19 fixture `TXT-2016-12-31-L2-negative.html`, the same layout.) This is exactly L8's "heading `table_eps` does not recognise". The EPS-table test just never saw it.

Runs 17 and 18 do not contradict this:
- the L9 count of 36 in run 17 was Codex's hand classification;
- the 85 in run 18 was the `layout_class_hint` heuristic.

Different methods give different counts. Neither is evidence of drift.

**Offline replay of S1-CF on run 19's evidence** (the parts computable without SEC):
- L1 14, L2 60, L3 7, L4 28, L5 8, L6 8, L7 0, L8 56, L9 31, L10 23;
- no row that run 19 put in L1, L2, L3, L8 or L10 changes class.

The 31 remaining L9 rows:
- WOR (f): 7;
- VIAV 2015 (f): 1;
- TXT (b): 9, fixed by the EPS-table rule (C6);
- the year-row headers: 14 (WMT 10, ZUMZ 2, WRLD 2019, TRMB 2018), fixed by C5.

C5 and C6 need table rows that run 19 did not record (it keeps 3 rows per table), so they need run 20. Expected L9 in run 20: about 8 to 12, plus any prose-only rows (C4; I estimate 0 to 2). The worst case, with C5 and C6 both failing, is 31. In that case §4 stops for good.

Why not (a): resuming as is fails S1 done item 2 by rule. Why not (c): the cause is identified and bounded, the fix is diagnose-only, and run 20 is within the plan (the third and last S1 run, about 2,630 SEC requests).

## 2. The L4 anomaly: a code gap (a spec gap in M1), not a precedence error

Precedence is correct: L5 comes before L4, and L4 before L9. Rule 9 can never hold, for two reasons:
- defects 1 and 2 make `rawStrictTextMatch` true on every row;
- defect 3 lets coincidental non-NI matches block it.

I am amending interpretation 1. Rule 9 (L4) holds when:
- no table of a release document (non-index) has a strict match under an **NI-value label**;
- and no release document has a **prose money match** (C4).

An NI-value label matches `NI_RE` and contains no per-share token. Strict matching uses the capped slack (C1).

The fix is C1, C3, C4 and C9. After it, the run 19 replay gives L4 = 28. Those 28 are genuine "NI value differs" rows: TRAK, TWIN, VIRC, WRLD, WLY 2018 (closest value off by 0.26%) and others. The explicit L4 negative WLY 2018-04-30 now falls in L4 itself.

## 3. TRMB 2011-12-30 fixture: accept the failure, no re-selection

It was an L1 auto-positive candidate: 4 tables, more than 65,536 bytes. M5 skipped it as designed, and L1 still got 3 positives (TXT, WLY, URBN). The failure is recorded and is not a stop. If run 20 repeats it, same result. The cap (64 KB) stays.

## 4. The next tick and the stages (amends plan §6.3 and §3)

The lead first opens and merges the PR for this file (docs only), so that ticks read it from main. The lead also merges or closes PR #113 (report only). Then it re-arms the loop with the next tick number. The 7-day and 200-tick limits are unchanged.

Stage order from here:

| Stage | Done when | Next action if not done |
|---|---|---|
| 3a S1-CF | the head of `claude/s1-cf` is an ancestor of `origin/main` | Haiku implements §5 on `claude/s1-cf` from origin/main, then Opus reviews (§6), at most 1 fix round (2 FAILs → stop), then normal merge |
| 3b Run 20 | a diagnose run-meta on `runs/q4-8k` with mainSha ⊇ the 3a merge, `extractOutcome` success, status complete | dispatch exactly as plan §3.1 (`mode=diagnose`, `budget=6000`, `ref=main`), only if no q4-8k run is queued or in progress |
| 3.3 on run 20 | — | `Forbidden` → stop. L9 > 23 → **final stop**: report to the user, no run 21, S1 not done. Run 20 failing for any reason (including infrastructure) → stop (§6.5 "run 19 and run 20 both fail"). Otherwise 3.4. |
| 3.4 / 3.5 | as plan §3.4 and §3.5 | apply §3.4 and §3.5 to run 20: branch `claude/s1-run20-output`, `docs/report/q4-8k/input/diagnose-run-20/README.md`, offline replay on run 20's verbose |
| 4 S2a-2 | merged | starts after the 3a merge, because it edits the same file. Its item (e) must use the new capped `strict_scales`, which still only removes matches. |
| 5 onwards | unchanged | unchanged |

**Exact next tick:** stage 3a. Spawn the Haiku implementer with §5 and §6 of this file. No SEC request until 3b.

Run 20 must:
- re-diagnose all 235 rows from run 17's report with the S1-CF code;
- write the same outputs as run 19, to `docs/report/q4-8k/runs/<run_id>/`.

The workflow is unchanged, and there are no new request types. Expected: about 2,630 requests, under the 40,000 per day cap together with run 19.

**S2b constraint (unchanged rule, now explicit).** The S2 L2 rule must not accept a fiscal-year label one year below the end year unless the end is within January 1–7 (decision §5). L2 rows with `variantRule == "vi"` (WSM) are diagnosed L2 but stay misses after S2. That is why they are never L2 auto-positives (C13).

## 5. Spec S1-CF (Haiku implements; diagnose-only)

**Files:** only `atlas/scripts/backfill_q4_eps_8k.py` and `tests/test_q4_eps_diagnose.py`.

**Unchanged:**
- `find_quarter`, `from_filing`, `table_eps`, `_labelled_rows`, `period_named`, `shows_period`, `verify_quarter`, `process_ticker`, `apply_fill`, `run` (non-diagnose) and `summarize`;
- the constants `QUARTER_WORDING`, `YEAR_WORDING`, `NI_RE`, `NI_AVOID` and `EPS_AVOID`;
- the workflow.

Every test in `tests/test_backfill_q4_eps_8k.py` passes unchanged. Use `python3 -m unittest discover -s tests -p 'test_*.py'`. No network in tests. Use one commit per item group (C1–C4, C5–C6, C7–C8, C9–C13), each ending with the session's attribution lines.

**C1. `strict_scales`.**
- A scale s is kept when |v·s − T| ≤ max(0.002·|T|, min(0.5·s·10^−d, 0.01·|T|)).
- Add the constant `STRICT_UNIT_CAP = 0.01`.
- The result is still [] for a None value or target.
- Everything else is unchanged.

**C2. `classify_row` recomputes strict matches.**
- Before applying any rule, for every recorded table: replace each `strictMatches` entry's `scales` with `strict_scales(value, decimals or 0, targetNetIncome)`, and drop entries whose scales become [].
- Recompute `acceptedStrictColumns` (sorted unique [column, scale] over the entries with `acceptedNiLabel`).
- Work on copies; do not mutate the input row.
- This keeps `classify_row` a pure function that can be replayed on run 19's evidence.

**C3. EDGAR index pages are not release documents.**
- `classify_row` ignores every document whose name matches `^\d{10}-\d{2}-\d{6}-index(?:-headers)?\.html?$` (case-insensitive), in every rule and every sub-case.
- `diagnose_filing` still reads and records them, so no request count changes.
- In rule 2 (L6), if no non-index document remains, the note is `no release document in the candidate filings (only EDGAR index pages)`.

**C4. Prose money match (new evidence, diagnose only).**
- In `diagnose_filing`, for each document, compute the visible text:
  - remove `<script>…</script>`, `<style>…</style>` (case-insensitive, dotall) and `<!-- … -->`;
  - replace every remaining tag `<[^>]+>` with a space;
  - `html.unescape`, then collapse whitespace.
- Money tokens:
  - `MONEY_WORD = \$\s*\(?\s*(\d[\d,]*(?:\.\d+)?)\s*\)?\s*(thousand|million|billion)\b` (case-insensitive). The scale is 1e3, 1e6 or 1e9 from the word.
  - `MONEY_FULL = \$\s*\(?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?![\d,.])(?!\s*\)?\s*(?:thousand|million|billion)\b)` (case-insensitive). The scale is 1.
- d is the number of digits after the point. A token matches when ||v|·s − |T|| ≤ max(0.002·|T|, min(0.5·s·10^−d, 0.01·|T|)). This uses absolute values, because prose says "net loss of $5.3 million".
- Record `rawMoneyMatch` (bool) and `rawMoneyContext`: the 160 characters of visible text around the first matching token, or null.
- Keep `rawStrictTextMatch` as it is (recorded, no longer used by the classifier).
- `classify_row` reads only `rawMoneyMatch`; a missing value counts as false.

**C5. `header_text`.**
- A row with numeric values still counts as a header row when every cell whose `number(text)` is not None has stripped text matching `^(?:19|20)\d{2}$`, and there is at least one such cell. This is a year row, labelled or not.
- Count at most 6 **non-empty** rows. A row is empty when all its cells are blank.
- Cut the result to 400 characters, as now.
- `2,013` (with a comma) is a data value, not a year.

**C6. `diluted_per_share_labels`.** A label also qualifies when all of these hold:
- it is among the 4 rows after a per-share heading (the existing heading test: labelled, all values None, contains a `HEADING_TOKENS` token);
- `is_net_income(label)` is true;
- every non-None value has |v| < 1000;
- the table has a row with `is_diluted_shares(label)` true;
- the table has no labelled row matching `\bbasic\b`.

The `EPS_AVOID` exclusion still applies. This covers the TXT layout ("Earnings per share:" / "Net income" / "Diluted average shares outstanding").

**C7. Variant period (`variant_rule(header, end) -> "i"|"ii"|"iii"|"iv"|"v"|"vi"|None`).** `variant_period(header, end)` becomes `variant_rule(...) is not None`.

Definitions:
- `normalize_header(h)`: insert a space at every `([a-z])([A-Z])` boundary, then replace `\b(\d{1,2}),(\d{4})\b` with `\1, \2`.
- `VARIANT_QUARTER = \b(?:three\s+months|3\s+months|quarters?|fourth\s+quarter|4th\s+quarter|q4|4q|thirteen\s+weeks|fourteen\s+weeks|13\s+weeks|14\s+weeks)\b` (case-insensitive).

The checks, in this order (the first that applies gives the result):
1. If `shows_period(header, end)` on the raw header: None.
2. Normalise h.
3. If `shows_period(h, end)`: "v".
4. (i): the old test on h, using `VARIANT_QUARTER` instead of `QUARTER_WORDING`.
5. (ii), (iii) and (iv): unchanged, on h.
6. (vi): all of these hold:
   - end month is January, or February with day ≤ 7;
   - `\b(?:fourth\s+quarter|4th\s+quarter|q4|4q)\b` matches;
   - Y−1 appears as a word;
   - Y does not appear.

**C8. `other_quarter_end`.**
- Work on `normalize_header(header)`.
- First blank out every date mention `\b(month name or 3-letter or "sept")\.?\s*(\d{1,2})\b(?:,?\s*(\d{4})\b)?` (case-insensitive) whose date lies within 7 days of `end`, or within 7 days of `end` one year earlier (Feb 29 → Feb 28). For the year, use the written year, or else try Y and Y−1.
- Then apply the existing month test, including the December exception, to what is left.
- The quarter-token test adds `1q`, `2q` and `3q`.

**C9. Rule 9 (L4).** It holds when both are true over non-index documents:
- no table has a (C2-recomputed) strict match whose lowercased label matches `NI_RE` and contains no `HEADING_TOKENS` token;
- no document has `rawMoneyMatch`.

The note is unchanged.

**C10. Rule 8 (L5).**
- The relevant tables are the recorded tables of non-index documents that have at least one `niRows` entry with a non-None value and whose `headerText` is not `other_quarter_end`.
- L5 holds when the relevant set is non-empty and every relevant table satisfies all of these:
  - `YEAR_WORDING_L5` matches its `headerText`, where `YEAR_WORDING_L5 = \b(?:twelve\s+months|years?\s+ended|fiscal\s+years?|full\s+year|year\s+to\s+date|ytd|for\s+the\s+years?)\b` (case-insensitive);
  - `quarterWording` is false;
  - `VARIANT_QUARTER` does not match its `headerText`.
- The evidence table is the first relevant table.

**C11. L9 sub-case (f), tried before (a).** A table with:
- non-empty `acceptedStrictColumns`;
- `periodNamed` false;
- `variant_period(headerText)` true;
- `other_quarter_end(headerText)` true.

The note is `(f) table N of DOC: accepted net income match; header names the quarter in a variant form and also another quarter end: '<headerText[:120]>'`. Sub-case (d)'s raw branch uses `rawMoneyMatch` and quotes `rawMoneyContext`.

**C12. L2 records the rule.**
- The L2 note says `in a variant form (<rule>)`.
- The classification dict gains the key `variantRule` (L2 only; None otherwise).
- `build_s1_classes` row records keep exactly their 8 keys.

**C13. Fixture selection.** An L2 row with `variantRule == "vi"` is never an L2 auto-positive. Skip it like a fixture error. Nothing else in M5 changes.

**Unit tests (add; list every changed existing test with its reason in the PR):**
- `strict_scales`:
  - (0, 0, −486275) → [];
  - (−0.4, 1, −400000) → [1e6];
  - (0.21, 2, 214e6) → [];
  - (18.7, 1, 18.737e6) → [1e6];
  - the existing 214 and 0.37 cases still pass.
- `variant_rule` and `other_quarter_end` (each case is a header, an end date, the expected rule and, where given, the expected `other_quarter_end` value):
  - "14 Weeks Ended 13 Weeks Ended February 2,2013 January 28,2012", 2013-02-02: "v", oqe False;
  - "FourteenWeeks EndedFebruary 2,2013 ThirteenWeeks EndedJanuary 28,2012", 2013-02-02: "v", oqe False;
  - "Three Months Ended December 31,2022 January 1,2022", 2022-12-31: "v", oqe False;
  - "4Q 2022 4Q 2021 After-Tax Per Share", 2022-05-31: "i", oqe False;
  - "4Q 2012 3Q 2012 4Q 2011 12M 2012 12M 2011", 2012-05-31: "i", oqe True;
  - "FOURTH QUARTER 2011 2010 (13 Weeks)", 2012-01-29: "vi";
  - the same header with 2012-03-31: None;
  - "Fourth Quarter of Fiscal Years 2013", 2014-01-03: "iv";
  - "Three Months Ended October 3, 2015 September 27, 2014", 2015-06-27: oqe True;
  - "Three Months Ended April 1, 2017", 2016-12-31: oqe True (unchanged).
- `header_text`: a WMT-like table (two empty rows, "Three Months Ended | Fiscal Years Ended", "January 31, | January 31,", "(Amounts in millions) | 2013 | 2012 | 2013 | 2012", "Net sales | 127,919 | …") → the text contains "2013 2012" and not "Net sales". A row "Revenue | 2,013 | 1,990" ends the header.
- `diluted_per_share_labels`: the TXT layout → ["net income"]. Adding a "Basic" row under the heading, or removing the diluted-shares row → that label is not included.
- C4 on synthetic HTML:
  - `<p style="margin:0">Net income of $18.7 million</p>` with T 18.737e6 → true;
  - `<style>p{margin:0}</style><p>30 days</p>` with T 29.826e6 → false;
  - "net loss of $5.3 million" with T −5.3e6 → true;
  - "$1,376,813" with T 1,376,813 → true;
  - "$1.4 Million" with T 1,376,813 → false.
- `classify_row`:
  - a filing whose only document is an index page → L6 with the new note;
  - a "0" match at 1e9 under "Impairment charge" and no other match → L4;
  - an annual table ("For the Years Ended June 30, 2023 2022", NI row with values) plus a next-quarter table ("Three Months Ended September 30, 2023 2022") → L5;
  - a WOR-like row → L9 with a note starting "(f)";
  - `select_fixtures` skips a "vi" L2 row.
- All the old precedence pairs still pass.

## 6. Acceptance (Haiku runs it; the Opus reviewer re-runs it; results go in the PR body)

1. The full unittest suite is green. `tests/test_backfill_q4_eps_8k.py` is unchanged and green.
2. `git diff origin/main...HEAD --stat` shows only the two files.
3. **Offline replay on run 19.**
   - Extract `origin/runs/q4-8k:docs/report/q4-8k/runs/39035520759/verbose.json.gz` to a temp dir outside the repo, and call the new `classify_row` on every row.
   - Required class counts, exactly: L1 14, L2 60, L3 7, L4 28, L5 8, L6 8, L7 0, L8 56, L9 31, L10 23.
   - L9 sub-cases: (a) 14, (b) 9, (f) 8.
   - L2 `variantRule` counts: i 21, iii 1, iv 7, v 25, vi 6.
   - Every row that run 19 classed L1, L2, L3, L8 or L10 keeps its class.
   - Anchor rows:
     - L4: TWIN 2012-06-30, WLY 2018-04-30 and VIRC 2022-01-31;
     - L6: ZUMZ 2012-01-28, and WLY 2017-04-30 with the index-page note;
     - L5: TRAK 2020-06-30;
     - L2 (v): ULTA 2013-02-02, VRA 2013-02-02 and TXT 2022-12-31;
     - L2 (vi): WSM 2012-01-29;
     - L2 (i): WOR 2022-05-31;
     - L2 (iv): TRMB 2014-01-03;
     - L9 (f): WOR 2012-05-31;
     - L10 unchanged: ZWS 2016-03-31;
     - L3 unchanged: VIAV 2021-07-03.

   A mismatch fails the review. The reviewer finds the cause in the code or in this spec before any fix round.
4. Reviewer only:
   - `table_eps` output is byte-identical to main on every table of every fixture under `tests/fixtures/q4_8k/` (as in plan §2.1 item 3);
   - no function listed as unchanged in §5 differs from main.

Verdict format and merge rule are as in plan §2.1 item 6.
