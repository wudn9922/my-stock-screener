# S2b review (Opus), 2026-10-17

Reviewer: Claude Opus. I did not write this code.
Target: `origin/claude/s2b-2` at 18b68e78. Its tree equals b39e176d (L1), which sits on 263d527e (L8), on main 1294b446.
Measured against: `s2b-opus-decision.md` (§1–§4), handoff `q4-eps-s1-s5-codex.md` §5, `s1-opus-decision.md` §2 and
`auto-s1-decision-2.md` §5.

## Verdict: PASS

No fix round is needed. Merge `claude/s2b-2` normally after the PR.

## Checks

| # | Check | Result |
|---|---|---|
| 1 | Scope | OK. `git diff --name-only origin/main...origin/claude/s2b-2` lists only `atlas/scripts/backfill_q4_eps_8k.py` and `tests/test_backfill_q4_eps_8k_s2.py`. No financials, growth.json, answer-set.json, fundamentals, workflow or Taiwan file. The WIP commit eb81c963 also touches only the script. |
| 2 | Rules against the decision | OK. The S2 heading words are exactly `S2_HEADING_WORDS = ("per share", "per common share")`, with no "per diluted share" and no "earnings per". `HEADING_TOKENS` and its comment are unchanged. The item 3 rule is exact: a heading that matches `\bdiluted\b` and not `\bbasic\b`; within the 2-row window, the first row with `is_net_income`, no `EPS_AVOID` word, at least one value and every \|v\| < 1000; at most one rename per heading; only when s2 is on. There is no `_heading_net_row` (C6), no zero-EPS rule, no continuing-operations rule, no other-table rule and no L2 or L3 rule. |
| 3 | Annual NI rejection on renamed rows | OK. `table_eps` returns `net income equals the full-year net income` when the EPS row index is in `renamed` and \|annual − target\| ≤ `NI_TOLERANCE`·\|target\|. `_look_back` applies the same check to every look-back match. Both are tested. |
| 4 | S1 classifier unchanged | OK. `_table_evidence` calls `table_eps(..., s2=False)` and `_labelled_rows(..., s2=False)`. With s2=False the heading words are ("per share",), there is no item 3 rename and no look-back, and a table that does not name the period still gives (None, None). `header_text` is the same through `_header_row_indices`. I replayed `classify_row` on run 20's `verbose.json.gz` (sha 4e163577…) against `s1-classes.json` (sha 35050330…): 235 rows, 0 mismatches on the branch and on main. `_table_evidence` and `header_text` output on all 17 real fixtures is byte-identical between the branch and main. Suite: 1,219 tests OK on the branch (1,188 on main). The L8 commit 263d527e alone passes both q4 test files (91 tests). |
| 5 | Fixture results | OK. Reproduced with my own script through `table_eps(annual=…, preceding=…)`, the S2 path, with the target and annual NI read from `docs/report/financials`. Each matches §2 and the inlined test values (table below). |
| 6 | Zero loosening | OK. Every module constant is the same as on main (only `S2_HEADING_WORDS` and `YEAR_TOKEN` are new). After a match, every check is still in the same order: one column or unit, column counts, empty, zero, \|EPS\| ≥ 1000, sign, shares (`SHARES_TOLERANCE`, `EPS_FLOOR`), and tables in one filing agreeing. Moving `period_named` after the EPS and NI scan changes nothing for a period-named table. A table that does not name the period is read only through `_look_back`, and only with exactly one (column, scale). |
| 7 | Deviations | All accepted (below). |
| 8 | Existing tests | OK. `git diff origin/main -- tests/test_backfill_q4_eps_8k.py` is empty. 0 existing tests changed. |

### Fixture results (branch, S2 path)

| Fixture | Role | Result | EPS | NI |
|---|---|---|---|---|
| URBN-2012-01-31-L8 | L8 positive | accepted | 0.27 | 39,255,000 |
| UNFI-2013-08-03-L8 | L8 positive | accepted | 0.65 | 32,077,000 |
| TXT-2020-01-04 | L8 negative | rejected (no result) | – | – |
| TXT-2016-12-31 | L8 negative | rejected (no result) | – | – |
| TXT-2012-12-29 | not a positive (item 2) | no result | – | – |
| VNCE-2024-02-03-L1 | L1 positive | accepted | −0.37 | −4,668,000 |
| URBN-2016-01-31-L1 | L1 positive | accepted | 0.61 | 72,878,000 |
| VNCE-2026-01-31 | L1 negative | rejected (no result) | – | – |
| WLY-2023-04-30 | documented miss (item 5) | no result | – | – |
| URBN-2014, TRAK-2019, TXT-2022, UI-2014, UNFI-2019 | out of scope | no result | – | – |
| VIAV-2021-07-03 | L3 negative | rejected (no result) | – | – |

On main, all 15 give no result. The S1 matcher (s2=False) gives no result on all 15, on the branch and on main.
VNCE-2026 is rejected because its NI table has no diluted EPS row. The year-header reason itself is pinned by the
synthetic test `test_a_full_year_header_before_the_table_is_rejected`.

## Deviations

- **(a) Annual plumbing in the L8 commit: accepted.** Decision §3 change 4 puts the annual rejection on renamed rows
  into L8. That check needs `annual` in `table_eps`, `from_filing`, `find_quarter`, `verify_quarter` and
  `process_ticker`, so the plumbing has to arrive with L8. The look-back and the grid are in the L1 commit, as decided.
- **(b) `process_answer_rows` passes annual: accepted, and required.** Without it, `annual` is None on the S3 path.
  S3 would then accept renamed-row and look-back matches whose NI equals the full-year NI, which the fill path
  rejects. The backtest would measure a looser rule than the one that fills. In the answer set, 2 of 2,649 rows have
  annual NI within `NI_TOLERANCE` of the target (ASPS 2019-12-31, MIND 2015-01-31), so the difference is real.
  The decision's list left this out by mistake.
- **(c) Branch `claude/s2b-2`, not `claude/s2b`: accepted.** The tree is what is reviewed. The merge commit
  18b68e78 (strategy ours) also brings the WIP commit eb81c963 into history. It touches only the script and does not
  change the tree. The PR body should say so.
- **(d) `classify_row` evidence uses `table_eps(s2=False)`: accepted.** Decision §3 requires this. The run 20 replay
  shows the same results as main.
- **Note (not a deviation):** the renamed-row annual check covers every rename made under the item 1 heading test,
  including the old "per share" Basic/Diluted renames. Decision §1 item 1 defines the heading test as "per share" or
  "per common share", so this matches §3 change 4. It can only reject, so it loosens nothing.

## Required changes

None.
