# S2a-2 review (Opus)

- Branch reviewed: `origin/claude/s2a-2`, head 0b412235, base origin/main 60545951 (merge-base confirmed).
- Commits: 4965aca6 (c), e490ecb7 (d), 2cecb468 (e), dc145c67 (f), d09edbd4 (workflow, Appendix B), 0b412235 (report).
- Spec: `docs/handoff/auto-s1-plan.md` §4 S2a-2, §5 and Appendix B; `docs/handoff/auto-s1-decision-2.md` §4 and §5
  (C1); `docs/handoff/q4-eps-s1-s5-codex.md` S3.1–S3.3.
- Review only. No code was changed, and nothing was pushed to the reviewed branch. No SEC request, no workflow dispatch, no S3 run.

## Verdict: PASS

No change is required. One fix round was available; it is not used.

## Checks

1. **Scope: OK.** Four files changed: the script, `tests/test_backfill_q4_eps_8k.py`, the workflow and the report.
   Nothing changed under `docs/report/financials/` or `docs/report/fundamentals/`, and no growth.json, answer-set.json
   or Taiwan file changed.
2. **(c) gate: OK.** `run_gate` counts every report row by (key, end) across all `--reports`. If any answer-set pair
   is in zero rows or in more than one row, it prints the gaps to stderr and returns 3 before it writes anything.
   `shard_keys` takes `sorted(set(keys))` and keeps the keys whose index mod N == k, so the shards form a partition.
   The half shards k/16 and (k+8)/16 together equal index mod 8 == k. The dev tickers come from `devTickers` in
   `tests/fixtures/q4_8k/s1_fixtures.json` (8 tickers). `main` returns from gate mode before `Sec(...)` is
   constructed, and `run_gate` never touches a client. A test pins this by patching `Sec` to raise. The metrics
   follow the S3.3 definitions. Accepted means confirmed. Correct means within max(0.011, 0.005·|answer|).
   Conflicts are counted over extracted rows. Sign errors are counted over accepted rows with answer ≠ 0. The
   Wilson formula uses z = 1.96. Per-FY results use the answer row's fy, with a flag where n ≥ 20. Coverage is
   accepted ÷ answer rows, and the summary also reports the same metrics excluding the dev tickers.
3. **(d): OK.** The only change in `verify_quarter` is that `within_tolerance` becomes the new `within_s3_tolerance`
   (|a − b| ≤ max(0.011, 0.005·|b|)). `within_tolerance` itself is unchanged, and so are the mismatch list and
   process_ticker, which still use it.
4. **(e): OK.** `table_eps` gets the net-income values with their decimals from `cells_with_decimals`, which uses
   the same label and value rules as `row_of`. It matches through the unchanged S1-CF `strict_scales`:
   max(0.002·|T|, min(0.5·s·10^−d, 0.01·|T|)). This is the capped C1 form, so a strict match is never looser than
   1% of the target. The uncapped main expression max(0.002·|T|, 0.5·s) is gone. Because 0.5·s·10^−d ≤ 0.5·s, the
   change can only remove matches, as decision-2 §4 requires. The rows considered are the same as before.
   `_labelled_rows` only renames Basic/Diluted rows under a per-share heading, and those are never net-income rows.
5. **(f): OK.** `find_quarter` keeps the first reason that is not `GENERIC_REASON`, and it falls back to the first
   generic reason only when no specific reason exists. With no candidate filing, it returns the window reason.
   `GENERIC_REASON` is the same text as the one in `from_filing`, and a test pins it.
6. **Appendix B: OK.** The workflow diff matches the plan line for line, and the YAML parses. The step order is
   Checkout, Select branch, Set up Python, Verify pin, Run extraction. The pin step does
   `git fetch --depth=1 origin 95184ade77cd30c565d3873e9f9050f3ec7c0054`, then rebuilds with the committed builder
   and `cmp`s the result with the committed file. It then asserts the ref, 2,649 rows, 563 keys and 2,387 basic
   rows, and all of this runs before the extraction step, which is the first one that makes SEC requests. In a
   local rebuild at the full SHA, `cmp` reported the file as identical. The `mode` input is still a choice of
   diagnose, backtest and fill, so gate mode cannot be reached from the workflow. Nothing was dispatched.
7. **Unchanged functions: OK.** The AST comparison with main shows exactly six changed top-level definitions:
   `find_quarter` (f), `table_eps` (e), `verify_quarter` (d), and `run`, `parse_args` and `main` (c). Everything
   else is unchanged, including `from_filing`, `_labelled_rows`, `period_named`, `shows_period`, `process_ticker`,
   `apply_fill`, `summarize`, `strict_scales`, `within_tolerance`, `row_of`, `QUARTER_WORDING`, `YEAR_WORDING`,
   `NI_RE`, `NI_AVOID`, `EPS_AVOID` and the value of `STRICT_UNIT_CAP` (only its comment changed). There are no
   other changes, and every added definition belongs to (c), (d) or (f).
8. **Tests: OK.** `python3 -m unittest discover -s tests -p 'test_*.py'` ran 1,188 tests, OK. The diff of
   `tests/test_backfill_q4_eps_8k.py` removes no lines. Its hunks add imports and new classes only, so no existing
   expectation changed. There are 35 new tests: Strict 3, FindQuarterReason 6, ToleranceCheck 3, Shard 3,
   AnswerSetFile 4, Gate 12 and AnswerSetBacktest 4. They cover the gate cases the report lists: a pass, a shard
   partition, a key in no report, a key in two reports, a split shard that repeats a key, sign errors, dev
   exclusion, conflicts over extracted rows, the tolerance, extra rows, the dev source, no SEC client, and usage
   errors.
9. **table_eps old vs new on the fixtures: OK, with one correction to the report wording.** All 34 tables in all 24
   HTML fixtures were tested.
   - **Real targets.** I used the stored Q4 net income at each fixture's own (key, end) for the 17 manifest fixtures.
     Old and new code give the same result on every table.
   - **Wide synthetic grid.** I tried about 1.35M calls: every numeric cell × 4 scales × ±1.5% and ±0.6 unit
     offsets. 1,245 calls differ. In every one of them, the old code accepted and the new code finds no match,
     because the target lies outside half a unit of the shown digit (for example, VIAV shows 28.7 against a target
     of 28.31M). No call went from rejected or ambiguous to accepted.
   - **Grid inside the shown precision.** 28 calls differ. These are removals of the same kind: the target came from
     another cell.
   - **The report wording.** The report says "no table changes its result for any target tried". That holds for
     real targets, but on a wider target set the new code removes matches. This is the intended behaviour, so it
     is recorded here and the report does not need a fix.

## Decisions on the implementer's open points

1. **Is the change to `table_eps` and `find_quarter` authorised? Yes.** Decision-2 §5 is the S1-CF spec, and its
   "unchanged" list binds only the S1-CF change. Decision-2 §4 (stage 4) names S2a-2 (e) as the `table_eps` change
   that must use the capped `strict_scales`. Plan §4 names (c) for the run options, (d) for `verify_quarter` and (f)
   for `find_quarter`. `_labelled_rows`, `from_filing`, `process_ticker`, `apply_fill` and `summarize` are confirmed
   untouched by AST.
2. **Is it correct that extraRows are excluded and do not cause exit 3? Yes.** S3.3 defines every metric over
   answer rows, and the exit-3 condition in plan §4 (c) is about answer-set pairs only. Extra rows are counted and
   shown. In a correct S3, every report comes from `--answer-set` mode and so cannot have extra rows. The S3
   reviewer must therefore require `extraRows == 0`, and a nonzero value is a stop for Opus.
3. **Where does the gate write, and is the missing verdict acceptable? Use
   `--report docs/report/q4-8k/s3-backtest-summary.json`.** This is the path in plan §5 and handoff S3.3. Having no
   pass/fail field is correct, because the S3.4 verdict belongs to the S3 reviewer. For a byte-for-byte re-run, the
   summary records `reports` exactly as they were passed. Claude and the S3 reviewer must therefore pass the same
   repository-relative report paths, in the same order, from the repository root.
4. **May `table_eps` now accept a table it used to reject as ambiguous? Yes, accepted, and no change is required.**
   The capped slack removes only matches that are farther from the stored net income than half a unit of the shown
   digit (with the cap at 1%) and also farther than `NI_TOLERANCE`. If exactly one (column, scale) is left, it is
   the only cell that agrees with the stored value at the precision the table prints. Every other check still
   applies: period, column count, EPS empty, zero, range and sign, shares, agreement across tables in
   `from_filing`, and the stricter independent check (d). The S3 gate measures the result. It was not seen on any
   real fixture target.
5. **Are the usage checks correct? Yes.** All of them run in `main` before `Sec(...)`, so a usage error never costs
   a request.
   - `--answer-set` needs `--mode backtest` and no `--tickers`, and its file is loaded and validated up front.
   - `--shard` needs `--answer-set`.
   - `--reports` is allowed only in gate mode.
   - Gate mode needs `--answer-set` and `--reports`, and no `--out-dir`.
   - In the workflow, `tickers` defaults to "", and the pin step allows `answer_set` only with backtest, so a
     correct S3 dispatch (mode backtest, answer_set set, shard set, tickers empty) cannot hit a usage error. The
     only ways to reach one are wrong inputs, such as tickers given together with answer_set, or a shard without
     answer_set. Both fail before any SEC request, which is the intended behaviour.

## Notes for S3 (no change required)

- With `--answer-set`, a key is skipped without a row if its financials file is missing or if it has no CIK in
  today's SEC ticker map. All 563 answer-set keys have a financials file on main. The CIK map cannot be checked
  offline. Such a key makes the gate exit 3, which fails safely. To learn this early, check each shard report after
  its run: it must hold every answer pair of that shard. A gap that comes from a missing CIK, rather than from a
  stop, is a stop for Opus before more shards run.
- A shard that stops partway leaves partial rows in its report. Only its two half-shard replacements may go to the
  gate. If the stopped report is passed as well, the gate exits 3, which is correct.
