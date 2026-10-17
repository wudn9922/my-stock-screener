## S0 baseline

- Step: S0 (handoff §3), as S2a-1 (a) of `docs/handoff/auto-s1-plan.md` §4. Read-only and offline: no SEC request,
  no workflow dispatch.
- Date (UTC): 2026-10-17.
- Main commit(s): computed at 95184ade77cd30c565d3873e9f9050f3ec7c0054 (data and code), recomputed on origin/main
  4acf053c668fdd8b9317008ba4e9b4b2931c40a8 (data and code). No commit to main.
- Workflow run ids and artifact names: none.
- Input counts: `docs/report/financials/*.json` at 95184ade: 5,483 files, of which 4,968 US and 515 non-US.
  `git diff --name-only 95184ade origin/main -- docs/report/financials` is empty, and `atlas/scripts/build_growth.py`
  and `atlas/scripts/build_valuation_us.py` are identical in both commits, so the recompute uses the same inputs.
- Output files: none. The computation script is a scratch file and is not committed.
- Method: every file is read with `git show <ref>:<path>`. `build_growth.read_periods` is called with
  `eps_usable` wrapped by `functools.partial(..., strict=True)` for the strict count and `strict=False` for the
  default count. US means `market != "TW"` and the key does not end in `.TW` or `.TWO`. A Q4 row is `quarterly.fq[i] == 4`
  with `epsQuality[i][field] == "A"`. A-in-TTM means a US file with a Q4 period among its newest four quarterly
  periods whose `epsDiluted` or `epsBasic` grade is A.

| Number | Expected | At 95184ade | On origin/main |
|---|---|---|---|
| Diluted A Q4 rows (files) | 2,649 | 2,649 (563) | 2,649 (563) |
| Basic A Q4 rows (files) | 2,387 | 2,387 (531) | 2,387 (531) |
| Strict TTM | 50 | 50 | 50 |
| Default TTM | 3,082 | 3,082 | 3,082 |
| A-in-TTM | 56 | 56 | 56 |
| A-in-TTM not strict | BNC, LFCR, MRDN, NTRP, SPHR, TULP | BNC, LFCR, MRDN, NTRP, SPHR, TULP | same |

- Diluted A rows by calendar year of the period end: 2010 7 · 2011 128 · 2012 173 · 2013 200 · 2014 218 · 2015 238 ·
  2016 258 · 2017 267 · 2018 288 · 2019 284 · 2020 227 · 2021 100 · 2022 69 · 2023 67 · 2024 57 · 2025 53 · 2026 15.
  This is the §0 distribution exactly. The count by `quarterly.fy` differs (for example 2014: 223), so the §0
  distribution is by period-end year.
- Reasons for the gap (first failing check, with the periods as the data labels them):
  - BNC: not consecutive. FY2024Q4, FY2025Q3, FY2025Q4, FY2026Q2. FY2025Q1 and Q2 are absent between the first
    two, and FY2026Q1 between the last two.
  - LFCR: not consecutive. FY2025Q4, FY2026Q1, FY2026Q3, FY2026Q4; FY2026Q2 is absent.
  - MRDN, SPHR: no EPS for the period ending 2025-12-31 (FY2026Q2).
  - NTRP: no EPS for the period ending 2026-02-28 (FY2026Q1).
  - TULP: no EPS for the period ending 2026-06-30 (FY2026Q2).
- Deviations from the handoff: none in the numbers. The §0 explanation of the gap (2026Q2 missing for MRDN, SPHR,
  TULP; 2026Q1 for NTRP; non-consecutive for BNC and LFCR) matches the data.
- Stop check: the diluted A count is 2,649, which is 0 away from 2,649 (limit 5). No stop.
- Stop: none.

## S1 run 20

- Step: S1, run 20 output (automated S1 plan §3.4), with the decided manifest edits from `docs/handoff/s1-run20-review.md`.
- Date (UTC): 2026-10-17.
- Main commit: 298e23866778ad27cdbe8f5b071f07f3d5227809 (`git merge-base origin/main HEAD`, the branch's base).
- Workflow run ids and artifact names: run 20: 39039050667, artifact `q4-8k-report-diagnose` on the run. Output on
  `runs/q4-8k` commit e30ff107 (path `docs/report/q4-8k/runs/39039050667/`).
- Input counts: sourceRows 235.
- Output files: `docs/report/q4-8k/input/diagnose-run-20/README.md`; `docs/report/q4-8k/s1-no-table-layouts.json`;
  the 17 fixtures in `tests/fixtures/q4_8k/`; the manifest `tests/fixtures/q4_8k/s1_fixtures.json`.
- Key numbers: classCounts L1 13, L2 74, L3 7, L4 27, L5 8, L6 8, L7 0, L8 62, L9 13, L10 23. L9 13 ≤ 23, and
  otherLimit passed true. Fixtures 17, positives 12, negatives 5.
- Deviations: the decided manifest edits for the two TXT rules and WLY's table note, with reason "Opus review,
  decision in docs/handoff/s1-run20-review.md".
- Stop: none.

## S2a-2

- Step: S2a-2 (automated S1 plan §4): items (c), (d), (e), (f) and the workflow delta (Appendix B). Code, tests and workflow only. No SEC request, no workflow dispatch, no S3 run, no merge.
- Date (UTC): 2026-10-17.
- Main commit: 60545951 (base of `claude/s2a-2`). Branch commits: 4965aca6 (c), e490ecb7 (d), 2cecb468 (e), dc145c67 (f), d09edbd4 (workflow); this section is the last commit.
- Workflow run ids and artifact names: none. The workflow was not dispatched.
- Input counts: `docs/report/q4-8k/answer-set.json`: 2,649 rows, 563 keys, 2,387 basic rows. A local rebuild from 95184ade with the committed builder into the scratchpad is byte-identical to the committed file, so the workflow's `cmp` and pin assertions pass. `tests/fixtures/q4_8k/s1_fixtures.json` devTickers: 8.
- Output files: `atlas/scripts/backfill_q4_eps_8k.py`, `tests/test_backfill_q4_eps_8k.py`, `.github/workflows/q4-8k-backfill.yml`, this section.
- Key numbers: full suite 1,188 tests, OK (1,153 before, plus 35 new).
- Changed tests (expectation changed): none. Every existing test in `tests/test_backfill_q4_eps_8k.py` and `tests/test_q4_eps_diagnose.py` passes unchanged. `tests/test_q4_eps_diagnose.py` is not touched.
- Changes by item:
  - (c) `--answer-set`, `--shard k/N`, `--mode gate --reports`. Tests: ShardTests, AnswerSetFileTests, GateTests, AnswerSetBacktestTests.
  - (d) `verify_quarter` confirms only with |checked − eps| ≤ max(0.011, 0.005·|eps|). Test: IndependentCheckToleranceTests.
  - (e) `table_eps` matches net income with the capped `strict_scales`. Checked offline: no table in `tests/fixtures/q4_8k/` changes its result for any target tried. Test: StrictNetIncomeTableTests (three of them fail on the old code).
  - (f) `find_quarter` returns the first specific reason across filings; the generic reason only when none is specific. Test: FindQuarterReasonTests (two fail on the old code).
- Gate cases (GateTests): all keys in one report, exit 0, summary written (pass); partition across two shard reports (pass); key in no report (exit 3, no summary); key in two reports (exit 3, no summary); split shard repeating a key of the full shard (exit 3, no summary); sign error counted and listed; dev ticker excluded from the accuracy count; conflict counted over extracted rows; row outside the answer set counted as extra and left out; gate creates no SEC client; usage errors exit 2.
- Shards: every key in exactly one shard for N = 1, 2, 8, 16 on the real answer set (563 keys).
- Deviations and open points for Opus:
  - Gate, report row outside the answer set: the spec does not say. It is counted as `extraRows` and left out of every metric. It does not cause exit 3.
  - Gate output goes to `--report`. There is no default path. Claude passes `--report docs/report/q4-8k/s3-backtest-summary.json`.
  - The gate summary has no pass/fail verdict. S3.4 is applied by the reviewer.
  - Usage checks I added: `--answer-set` needs `--mode backtest` and no `--tickers`; `--shard` needs `--answer-set`; `--mode gate` needs `--answer-set` and `--reports` and no `--out-dir`.
  - Functions touched: `run` (c), `verify_quarter` (d), `table_eps` (e), `find_quarter` (f). Not touched: `_labelled_rows`, `from_filing`, `process_ticker`, `apply_fill`, `summarize`, and the listed constants. `table_eps`, `find_quarter` and `run` are on the S1-CF unchanged list in `auto-s1-decision-2.md` §5, but `auto-s1-decision-2.md` §4 and plan §4 name them as S2a-2 changes, so I read them as authorised.
  - `table_eps` (e) can now accept a table that was rejected as ambiguous, when the decimals narrow the matching scales to one. Seen only on synthetic input.
  - Backtest with `--answer-set`: a key whose financials file is missing is skipped without a row. The gate then exits 3 for the missing pairs.
- Stop: none.

## S2b

- Step: S2b (automated S1 plan §4 and `q4-eps-s1-s5-codex.md` §5 S2 items 5–6): L8 (heading words) and L1 (diluted heading, look-back to the period header). Decision: `docs/handoff/s2b-opus-decision.md`. Review: `docs/handoff/s2b-review.md` (Opus, PASS, no required changes).
- Date (UTC): 2026-10-17.
- Merged: PR #121 (`claude/s2b-2`, merge commit e0cb3816) and PR #122 (review document, merge commit e2a520e9). Branch commits: 263d527e (L8), b39e176d (L1), 18b68e78 (merge of the WIP save eb81c963, tree unchanged).
- Workflow run ids and artifact names: none. No workflow dispatch, no SEC request.
- Files changed on main: `atlas/scripts/backfill_q4_eps_8k.py`, `tests/test_backfill_q4_eps_8k_s2.py` (new, 31 tests). Nothing else.
- Changed tests (expectation changed): none. `git diff 1294b446 e2a520e9 -- tests/test_backfill_q4_eps_8k.py tests/test_q4_eps_diagnose.py` is empty.
- S1 matcher unchanged: `_table_evidence` calls `table_eps(..., s2=False)`. Run 20 replay of `classify_row`: 235 rows, 0 mismatches against `s1-classes.json`, on main and on the branch.
- Fixture results on the S2 path (review §5): URBN-2012-01-31-L8 accepted (EPS 0.27); UNFI-2013-08-03-L8 accepted (0.65); URBN-2016-01-31-L1 accepted (0.61); VNCE-2024-02-03-L1 accepted (−0.37). The four negatives (TXT-2020-01-04, TXT-2016-12-31, VNCE-2026-01-31, VIAV-2021-07-03), TXT-2012-12-29 (not a positive) and the documented miss WLY-2023-04-30 give no result, as do the five out-of-scope fixtures (URBN-2014, TRAK-2019, TXT-2022, UI-2014, UNFI-2019). On main all 15 give no result.
- Deviations, all accepted by Opus: (a) annual plumbing in the L8 commit (decision §3 change 4); (b) `process_answer_rows` passes `annual`, required on the S3 path; (c) the WIP commit eb81c963 is in history through merge 18b68e78, tree unchanged; (d) S1 evidence uses `s2=False` (decision §3).
- Scope not done this round: L2 (74 answer rows) and L3 (7 rows). Stop rules in the decision are unchanged. Open points for a later round: L2 is blocked by TRAK zero EPS, TXT-2022 continuing-operations EPS and the URBN-2014 dependency; L3 has one usable positive (UI-2014) against the two required; UNFI-2019 matches only the NCI-inclusive label, and VIAV-2021 is not an L3 row but a stored-data defect, now a negative (decision item 6).
- Stored data defect, not edited: VIAV-2021-07-03 has stored Q4 net income 3.3M and EPS 0.01; the release shows −1.9M and −0.01. Financials are not changed by S2. Needs a decision from the user before any fill.
- Stop: none.

## S2 acceptance

- Step: S2 acceptance (automated S1 plan §4; S2a-1, S2a-2, S2b all merged). Bookkeeping only. No code change, no SEC request, no workflow dispatch, no S3 run.
- Date (UTC): 2026-10-17.
- Main commit: e2a520e9 (after PR #122).
- PRs: #111 (S2a-1, answer set), #118 (S2a-2), #119 (S2 review documents), #120 (S2b decision), #121 (S2b implementation), #122 (S2b review document).
- Full suite on main: `python3 -m unittest discover -s tests -p "test_*.py"`: 1,219 tests, OK. Count history: 1,153 before S2a-2, 1,188 after S2a-2 (+35), 1,219 after S2b (+31).
- Changed tests across S2 (expectation changed): none. Every existing test in `tests/test_backfill_q4_eps_8k.py` and `tests/test_q4_eps_diagnose.py` passes unchanged.
- Remaining before S3: an Opus go on this section. S3 has not started; no backtest has been dispatched.
- Workflow run ids and artifact names: none.
- Input counts: answer set unchanged, 2,649 rows, 563 keys, 2,387 basic.
- Output files: this section only.
- Deviations: none.
- Stop: none.

## S3 (stopped at the gate)

- Step: S3 backtest and gate (plan §5). Stopped by Opus decision (agent a69ff5fdff8e3bf27, 2026-10-17): the gate fails on conflicts. No S3.6 revision, no further dispatch, no gate run.
- Date (UTC): 2026-10-17. Main commit: d3360a27 (PR #124). Answer set unchanged: 2,649 rows, 563 keys, 2,387 basic.
- Runs (shard, `runs/q4-8k` commit, outcome):
  - run 21, 39055646603, 0/8, c3aa572f: accepted, 347 periods, 4,304 requests, 1 conflict (SNDA 2019-12-31).
  - run 22, 39057185890, 1/8, 8572920a: accepted, 341 periods, 4,025 requests, 0 conflicts.
  - run 23, 39058821815, 2/8, 4dc39349: accepted, 318 periods, 3,659 requests, 0 conflicts.
  - run 24, 39060384310, 3/8 first attempt, 0a21d561: NetworkError (sec.gov, index-headers) after 26 of 344 periods, 401 requests, 1 conflict (ACNT 2022-12-31).
  - run 25, 39062001066, 3/8 retry, 35b2c538: NetworkError (sec.gov, index.json) after 42 of 344 periods, 565 requests, 1 conflict (ACNT 2022-12-31).
  - Shards 4/8 to 7/8: not dispatched.
- Requests: runs 21 to 25 total 12,954 (cap 60,000). Today's UTC total is about 18,214 including diagnose runs 19 and 20 (cap 40,000).
- Partial numbers, shards 0 to 2 (1,006 answer rows): extracted 349; accepted (confirmed) 208; correct 197; precision 0.947; Wilson 95% lower bound 0.908 (gate needs 0.970); sign errors 0; conflicts 1 (2 with shard 3); incorrect 11.
- Incorrect rows (11), by layout class:
  - Split-basis mismatch (9). Estimate × splitFactor equals the answer in 8 rows. CNC 2012 matches only if the later split alone is applied.

    | Key | End | Extracted | Answer | Filing |
    |---|---|---|---|---|
    | QMCO | 2015-03-31 | 6.4 | 0.04 | https://www.sec.gov/Archives/edgar/data/709283/000120677415001609/exhibit99-1.htm |
    | SCOR | 2011-12-31 | -2.0 | -0.1 | https://www.sec.gov/Archives/edgar/data/1158172/000119312512058328/d301278dex991.htm |
    | SCOR | 2012-12-31 | -1.0 | -0.05 | https://www.sec.gov/Archives/edgar/data/1158172/000119312513060531/d485435dex991.htm |
    | SCOR | 2014-12-31 | -1.6 | -0.08 | https://www.sec.gov/Archives/edgar/data/1158172/000115817215000008/exhibit991q42014.htm |
    | SUNE | 2016-12-31 | -125999.37 | -0.21 | https://www.sec.gov/Archives/edgar/data/22701/000089710117000304/csi170727_ex99-1.htm |
    | CNC | 2012-12-31 | 0.0425 | 0.085 | https://www.sec.gov/Archives/edgar/data/1071739/000107173913000015/exhibit991.htm |
    | CNC | 2015-12-31 | 0.45 | 0.9 | https://www.sec.gov/Archives/edgar/data/1071739/000107173916000175/exhibit991.htm |
    | IPI | 2010-12-31 | 2.4 | 0.24 | https://www.sec.gov/Archives/edgar/data/1421461/000110465911009385/a11-1982_4ex99d1.htm |
    | PENG | 2017-08-25 | -0.24 | -0.48 | https://www.sec.gov/Archives/edgar/data/1616533/000156459017019049/sgh-ex991_6.htm |

  - Parser error (2). Genuine misreads.

    | Key | End | Extracted | Answer | Filing |
    |---|---|---|---|---|
    | TRU | 2021-12-31 | 0.81 | 5.27 | https://www.sec.gov/Archives/edgar/data/1552033/000155203322000007/exhibit99112312021.htm |
    | TRU | 2022-12-31 | 0.78 | 0.24 | https://www.sec.gov/Archives/edgar/data/1552033/000155203323000014/exhibit99112312022.htm |

- Conflicts (2). A conflict cannot be undone by more rows (S3.6).

  | Key | End | Extracted | Answer | Next-year release | Filing |
  |---|---|---|---|---|---|
  | SNDA | 2019-12-31 | 5.10 | 5.10 | 5.05 | https://www.sec.gov/Archives/edgar/data/1043000/000156459020014381/csu-ex991_6.htm |
  | ACNT | 2022-12-31 | 0.01 | 0.01 | 0.43 | https://www.sec.gov/Archives/edgar/data/95953/000009595323000006/acnt-20221231xex991.htm |

- Findings:
  - The S3.3 "correct" test puts both values on today's split basis, but the answer value is on the basis of its own 10-K filing date. The comparison is mis-specified for the 9 split-basis rows. The answer set is pinned and was not edited.
  - The independent check is not independent for these 11 rows. The checked value equals the estimate, because the next year's release repeats the same layout and the same split conversion.
  - SUNE 2016 was accepted with EPS −125,999.37 (split factor 1.67e-6). The |EPS| ≥ 1000 rejection runs before the split division, so it never sees this value. Code fix needed before any fill.
  - Even with the split basis fixed, TRU and the 2 conflicts still fail S3.4.
- Not done: gate (exit 3 expected; `s3-backtest-summary.json` not committed), S3.6 revision, S4, S5, fill, answer-set edit, code change.
- Stop: yes. S3 failed (conflicts; partial Wilson 0.908). S3.6 revision needs an Opus decision.
