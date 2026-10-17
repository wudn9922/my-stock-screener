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
