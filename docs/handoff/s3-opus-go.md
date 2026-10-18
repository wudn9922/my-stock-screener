# S3 go (Opus), 2026-10-17

Author: Claude Opus (reviewer and decider). Audience: the lead session.
Applies on top of `auto-s1-plan.md` §5–§7 and `q4-eps-s1-s5-codex.md` §6 and §9. It loosens nothing.

## 1. PR #123 (S2 acceptance record): PASS after corrections

Suite re-run on `claude/s2-acceptance` (a03b7396): 1,219 tests, OK. PR numbers, merge commits, the test counts
(1,153 → 1,188, +35 in S2a-2; → 1,219, +31 in S2b), the fixture outcomes and deviations (a)–(d) match the repo
and `s2b-review.md`. No existing line of `tests/test_backfill_q4_eps_8k.py` or `tests/test_q4_eps_diagnose.py` was
removed or changed by S2a-1, S2a-2 or S2b. Corrections (the lead applies them before merging #123):

- C1, line 94: replace `` `git diff origin/main -- tests/test_backfill_q4_eps_8k.py` is empty for the S2b change ``
  with `` `git diff 1294b446 e2a520e9 -- tests/test_backfill_q4_eps_8k.py tests/test_q4_eps_diagnose.py` is empty ``.
- C2, line 96: replace "The four negatives and the documented miss (WLY-2023-04-30) give no result. Out-of-scope
  fixtures give no result." with "The four negatives (TXT-2020-01-04, TXT-2016-12-31, VNCE-2026-01-31,
  VIAV-2021-07-03), TXT-2012-12-29 (not a positive) and the documented miss WLY-2023-04-30 give no result, as do
  the five out-of-scope fixtures (URBN-2014, TRAK-2019, TXT-2022, UI-2014, UNFI-2019)."
- C3, line 98: replace "L3 needs a label policy and the VIAV-2021 stored-data defect (below)." with "L3 has one
  usable positive (UI-2014) against the two required; UNFI-2019 matches only the NCI-inclusive label, and VIAV-2021
  is not an L3 row but a stored-data defect, now a negative (decision item 6)."
- C4, after line 110: add the missing §9 fields: "- Workflow run ids and artifact names: none." "- Input counts:
  answer set unchanged, 2,649 rows, 563 keys, 2,387 basic." "- Output files: this section only." "- Deviations:
  none." "- Stop: none."

## 2. S3: GO

Checked: answer set 2,649 rows / 563 keys / 2,387 basic rows / 531 basic keys, unique (key, end); a local
rebuild at 95184ade is byte-identical (the workflow pin step will pass). S2 is accepted once #123 merges.

Dispatch, one shard at a time, only when no q4-8k run is queued or in progress, k = 0..7:

```
gh api -X POST repos/wudn9922/my-stock-screener/actions/workflows/q4-8k-backfill.yml/dispatches \
  -f ref=main -f 'inputs[mode]=backtest' -f 'inputs[answer_set]=docs/report/q4-8k/answer-set.json' \
  -f 'inputs[shard]=k/8' -f 'inputs[budget]=6000'
```

`tickers` is omitted (the script refuses `--tickers` with `--answer-set`). Expected answer rows per shard:
0/8 347, 1/8 341, 2/8 318, 3/8 344, 4/8 331, 5/8 298, 6/8 324, 7/8 346 (sum 2,649). Expected about 3,700 requests
per shard. Output per run: `runs/q4-8k:docs/report/q4-8k/runs/<run_id>/{run-meta.json,summary.json,q4-8k-report.json}`
and artifact `q4-8k-report-backtest`.

Shard accepted only if: run-meta `mode` backtest, `shard` k/8, `answerSet` as above, `mainSha` has e0cb3816 as an ancestor,
`extractOutcome` success; summary `stopped` null and `periods` equal to the count above.

Then, offline: `python3 atlas/scripts/backfill_q4_eps_8k.py --mode gate --answer-set
docs/report/q4-8k/answer-set.json --reports <the 8 accepted reports> --report
docs/report/q4-8k/s3-backtest-summary.json` on `claude/s3-summary`. A stopped shard's partial report is never
passed to the gate.

## 3. Stop mapping (plan §6.5)

- `stopped` starts with `Forbidden` (the script exits 0 on a 403 in backtest; the run looks green): stop, no retry.
- `stopped` starts with `OutOfBudget`: re-run as `k/16` and `(k+8)/16`, at most 4 half shards; a half shard out
  of budget: stop.
- `stopped` `NetworkError`, runner error or timeout: one retry of that shard (≤ 4 infrastructure retries).
- Cumulative S3 `requests` + 6,000 > 60,000 before a dispatch: stop. Today's UTC requests + 6,000 > 40,000: wait
  for the next UTC day (not a stop).
- `periods` below the expected count with `stopped` null (a key missing from the SEC ticker map): the gate would
  exit 3; stop and report the keys. No answer-set edit.
- Pin step fails (`cmp` or counts): stop.
- Gate exits 3, or any S3.4 item fails: stop. No S3.6 revision without a new Opus decision.
- Any commit outside `docs/report/q4-8k/runs/<run_id>/`: stop. More than 16 S3 dispatches or the tick/date limit:
  stop.

## 4. S3.4 checks I apply to `s3-backtest-summary.json`

1. Gate exit 0; my re-run is byte-identical; `answerRows` 2,649; `reportRows` 2,649; `extraRows` 0.
2. `all.accepted` ≥ 300.
3. `all.wilsonLower95` ≥ 0.970, recomputed from `correct` and `accepted`.
4. `all.signErrors` 0 and the `signErrors` list empty.
5. `all.conflicts` 0 and the `conflicts` list empty (a claimed restatement is still a fail).
6. Every `perFY` entry with accepted ≥ 20 at precision ≥ 0.90.
7. `excludingDevTickers.precision` ≥ 0.97.
8. `incorrect` list length equals `accepted − correct`; I spot-check every incorrect row's URL.
9. The report section carries the §9 key numbers: n, correct, precision, Wilson, sign errors, conflicts, per-FY
   table, excluding-dev precision, coverage, run ids and `runs/q4-8k` commits, total requests.
