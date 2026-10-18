# US Q4 EPS from 8-K releases: S1–S5 instructions for Codex

Author: Claude Opus (reviewer and decider), 2026-10-17. Audience: Codex. This file is meant to be enough on its
own; you need no other context. Where it says "stop", stop and report (section 9). Do not work around a stop.

## 0. Context you need

### Repository and branches

- Repo: `wudn9922/my-stock-screener`. You write `main`. Claude writes branches.
- `data/opus-steps`, commit **95184ade**, is Claude's US Q4 EPS grading branch. It is **not** merged into main
  (main is at 6924701d). It:
  - moved 183 share-count estimates out of `quarterly.values.epsDiluted` into `q4EpsEstimates` (grade C), so
    those cells are null again on that branch;
  - wrote `quarterly.epsQuality[i] = {"epsDiluted": "A"|"Y", "epsBasic": …}` (A = direct SEC 3-month fact,
    stored in `secQ4EpsFacts[end][field]`; Y = derived as annual − Q1..Q3);
  - accepted 5,009 grade-A values, applied 5 null→A rows and 2 overrides (old value in `epsOverrides`);
  - wrote `docs/report/q4-eps-acceptance-ledger.json`, and added `atlas/scripts/grade_q4_eps_provenance.py`,
    `atlas/scripts/accept_q4_eps_sec_facts.py` and their tests.
- Financials files (`docs/report/financials/<KEY>.json`) are **single-line JSON**. Any edit by two branches to the
  same file conflicts. That is why S4 waits for the merge (S4 precondition P2).

### The pipeline

- Script: `atlas/scripts/backfill_q4_eps_8k.py`.
- Workflow: `.github/workflows/q4-8k-backfill.yml` (manual; modes `backtest` and `fill`). SEC is reachable only
  from Actions.
- `fill` commits to branch `backfill/q4-8k`, which does not exist yet; no fill has ever run.
- The latest run is **run 17** (id 38951674240, backtest, artifact `q4-8k-report-backtest`).
- Round 11 measured about 90% precision against *stored* Q4 values, which are mostly derived and noisy. The gate
  has **not** been met.

### The answer set

The only accepted reference ("answer") for Q4 EPS is the **SEC direct grade-A** values at 95184ade. Values graded
Y, values with no grade, and C values are **not** answers. My count at 95184ade, from the working tree:
- `epsDiluted` A: **2,649** Q4 rows in **563** files, all with `netIncome`;
- `epsBasic` A: 2,387.
- By fiscal year of the period end (diluted): 2010 7 · 2011 128 · 2012 173 · 2013 200 · 2014 218 · 2015 238 ·
  2016 258 · 2017 267 · 2018 288 · 2019 284 · 2020 227 · 2021 100 · 2022 69 · 2023 67 · 2024 57 · 2025 53 ·
  2026 15.

### The 49 vs 55 strict TTM question

It is unexplained in the repo. My probable explanation, from my own definition (see S0): the strict count is
**50** and the count of tickers with an A-graded Q4 among their newest four quarters is **56**. The gap is 6
tickers:
- BNC and LFCR: the newest four quarters are not consecutive;
- MRDN, SPHR and TULP: 2026Q2 EPS is missing;
- NTRP: 2026Q1 EPS is missing.

The reported 49 / 55 probably used a slightly different definition (it reported 3,068 for the default gate where
I get 3,082). S0 fixes the definition so that every later number is comparable.

### Standing rules (from the user; do not change them)

- A fill writes **only null fields** and **records a source** for every written value.
- No third-party sites (TradingView, Futu, …) for comparison or data.
- Fill gate on accepted fill values: **n ≥ 300; Wilson 95% lower bound ≥ 97%; zero sign errors; zero
  independent-check conflicts; no fiscal year below 90% precision when that year has n ≥ 20.**
- SEC: truthful User-Agent from the `SEC_USER_AGENT` secret, at most about 5 requests per second, stop at the
  first HTTP 403. Never rotate the User-Agent or IP, and never use a proxy to get around a 403.
- The workflow calls no AI service.

## 1. Must not do (each of these is a stop)

1. Do not write fill values before the S3 gate passes **and** all S4 preconditions hold. Do not run the current
   `fill` mode at all: today it writes C-graded values into `epsDiluted`.
2. Never overwrite a non-null value in any financials file. Never edit or delete existing `epsQuality`,
   `secQ4EpsFacts`, `q4EpsEstimates` or `epsOverrides` entries.
3. Never write to `data/opus-steps`: no commit, push, rebase, reset or force-push, and no merge of it.
   - Read it only, with `git fetch origin data/opus-steps`, `git show 95184ade:<path>`, or
     `git worktree add --detach <tmpdir> 95184ade` (remove the worktree afterwards).
   - Do not create files on main at these paths: `atlas/scripts/grade_q4_eps_provenance.py`,
     `atlas/scripts/accept_q4_eps_sec_facts.py`, `tests/test_grade_q4_eps_provenance.py`,
     `tests/test_accept_q4_eps_sec_facts.py`, `docs/report/q4-eps-acceptance-ledger.json`.
   - Do not hand-copy or cherry-pick the branch's file changes onto main. Merging it is the user's decision.
4. Do not edit `docs/report/financials/*.json` on main by hand or by script, except through S4/S5 after the
   merge.
5. Do not change Q4 gating in `atlas/scripts/build_growth.py` (`eps_usable`, `read_periods`, `Q4_EPS_STRICT`).
6. Do not loosen `NI_TOLERANCE` (0.002), `SHARES_TOLERANCE`, the sign rule, the |EPS| ≥ 1000 rejection, the
   non-GAAP exclusions, or any gate number in this file.
7. Do not put grade C, unconfirmed, "unavailable" or conflict values into `quarterly.values`. They go into the
   ledger only (S4).
8. Do not write `epsBasic` or `dilutedShares` values from 8-K data. S4 writes `epsDiluted` only, and the other
   two stay as evidence in the source record.
9. Do not tune parser rules on answer-set rows after you have seen S3 results for them, except through the one
   allowed revision round (S3.6).
10. Do not touch Taiwan files or code. That review is separate: `docs/handoff/tw-finmind-review.md`.
11. These two handoff files are untracked in the shared container. Do not edit them; your report goes to its
    own file (section 9).

## 2. The job that is running now

- If the 8-K job you are running now is `backtest`, let it finish and keep its artifact as an S1 input.
- If it is `fill`, cancel it and do not push or merge anything it produced. If it already pushed
  `backfill/q4-8k`, leave the branch, do not merge it, and report it.

## 3. S0: baseline numbers (read-only, offline)

**Input:** `git worktree add --detach /tmp/opus-ro 95184ade`.

**Do:**

1. Count answer rows per field and per fiscal year. A row is a Q4 period (`quarterly.fq[i] == 4`) in a US file
   (`market != "TW"`, not `.TW` or `.TWO`) with `epsQuality[i][field] == "A"`.
2. Compute the **strict TTM count**: the number of US files whose newest four quarterly periods (sorted by end
   date, as returned by `build_growth.read_periods` with `eps_usable(..., strict=True)`) are four consecutive
   fiscal quarters (fy·4+fq) **and** each has `epsD` or `epsB` not null.
   - Compute the same with `strict=False` (the default count).
   - Note: `eps_usable` takes its `strict` default when the module is imported, so pass `strict` explicitly
     (wrap or patch the function). Setting the env var after import does nothing.
3. Compute the number of US files with an A-graded Q4 (either field) among their newest four quarterly periods,
   and list every file in that set that fails the strict count, with the reason.

**Expected:** diluted A 2,649 / basic A 2,387; strict 50; default 3,082; A-in-TTM 56; gap = BNC, LFCR, MRDN,
NTRP, SPHR, TULP.

**Done when:** the numbers are in the report. If they differ from the expected ones, give the cause of each
difference. From now on, strict / default TTM means this definition.

**Stop if:** the diluted A count differs by more than 5 from 2,649 and you cannot explain it.

## 4. S1: why 235 rows have "no table"

**Input:** the rows of run 17's report with reason `no table shows this quarter's net income` (expected 235; if
the artifact has another count, use that one and note it). Use their `debug` fields (`period_net_rows`,
`orphans`, `net_like_text`).

**Code (main):** add `--mode diagnose` to `backfill_q4_eps_8k.py` and the workflow. It is read-only: no
financials or state writes. For each given (ticker, end) it walks the same candidate filings and documents as
`find_quarter` and writes per row:
- the accession and document URLs;
- the number of tables;
- for every table that has a net-income-like row or names the period: its index, the first 3 rows of text,
  column counts, NI labels, and whether `period_named` is true;
- whether the stored Q4 net income (at scales 1, 1e3, 1e6, 1e9, within `NI_TOLERANCE`) appears anywhere in the
  document, and where;
- the nearest preceding non-empty tables (up to 3) and whether they name the period.

It saves the trimmed `<table>` HTML of up to 3 example rows per class into the artifact.

**Classify every row into exactly one layout class:**

| Class | Meaning |
|---|---|
| L1 `period-in-earlier-table` | Target NI is in a table without the period name; a three-months / quarter header for the period is in one of the 3 preceding non-empty tables. |
| L2 `period-wording` | The table holding target NI has a quarter header that `shows_period` misses ("Q4 2019", "Fourth Quarter 2019", "Quarter ended Dec. 31" with the year elsewhere, "Thirteen/14 weeks ended …"). |
| L3 `ni-label` | The period table exists but the NI row label is not recognised or is caught by `NI_AVOID` ("Net income available to common stockholders", "Net earnings applicable to …"). |
| L4 `ni-value` | No number in the documents matches the target NI at any scale. Record the closest value and its relative difference (restated, continuing operations only, …). |
| L5 `no-quarter-column` | Only annual or year-to-date columns. |
| L6 `not-html-table` | Text/`<pre>`, PDF or image release, or the table sits in a document beyond `MAX_EXHIBITS` / `EXTRA_DOCS`. |
| L7 `no-release` | No results in the 8-K (webcast notice etc.), or the right filing falls outside `FILING_DAYS` / `MAX_FILINGS`. |
| L9 `other` | Anything else, described in one line. |

**Output:**
- `docs/report/q4-8k/s1-no-table-layouts.json`, with one entry `{key, end, class, accession, url, note}` per
  row and a class count summary;
- fixtures in `tests/fixtures/q4_8k/<KEY>-<end>-<class>.html`: trimmed real SEC tables only (public filings),
  with the source URL in an HTML comment at the top. At least 2 per class that S2 will handle, plus at least 2
  negative cases.

**Done when:**
- all rows are classified;
- `other` ≤ 10% of rows;
- each class has a count and 2 example URLs;
- for each class the report says "S2 handles", "out of scope" or "needs Opus".
- The list of fixture tickers is recorded; these are the **dev tickers** for S3.6.

**Stop if:** the SEC budget runs out repeatedly (more than 3 runs for S1), or a 403 occurs.

## 5. S2: wider net-income matching (code and tests only, no SEC)

Change `table_eps` / `from_filing` on main. Every rule must have a real-sample fixture from S1 and a negative
fixture.

1. **Look-back for the period header.** When a table holds the stored Q4 NI in exactly one (column, scale) but
   `period_named(table, end)` is false, look at **up to 3 preceding non-empty tables** in the same document. A
   table is non-empty if at least one row has a non-blank cell. Use the nearest one that names the period
   (month, day, year of `end`) **with quarter wording** in the matched column's header.
   - Quarter wording, case-insensitive: `three months`, `3 months`, `quarter`, `fourth quarter`, `q4`,
     `thirteen weeks`, `fourteen weeks`, `13 weeks`, `14 weeks`.
   - The header's period columns must align one-to-one with the NI table's value columns. If they cannot be
     aligned, reject with reason `period header not aligned`. Never guess a column.
2. **Wording variants (L2)** are added to `shows_period` only as far as fixtures cover them. The year must still
   match.
3. **Not the full year.** Reject when the matched column header has year wording (`twelve months`,
   `year ended`, `fiscal year`, `full year`). Also reject when the stored Q4 NI equals the same fiscal year's
   annual NI within `NI_TOLERANCE` (`annual.values.netIncome` at the matching `annual.fy`); a column match is
   then ambiguous.
4. NI must match within the unchanged `NI_TOLERANCE`. All existing checks still apply after a look-back match:
   one column, sign, shares, |EPS| < 1000, non-GAAP exclusions, and tables in one filing agreeing.
5. **Stricter confirmation.** In `verify_quarter`, "confirmed" now requires
   |checked − eps| ≤ max(0.011, 0.005·|eps|) (the S3 correctness tolerance) instead of 2%.
6. Add the `--answer-set`, `--shard` and `--mode gate` options needed by S3 (section 6).

**Done when:**
- `python -m pytest tests/` passes;
- every existing test in `tests/test_backfill_q4_eps_8k.py` still passes unchanged, except tests whose behaviour
  is meant to change, which you list one by one with the reason;
- the new fixtures are committed.

**Stop if:** a rule can only pass its fixtures by loosening something in section 1.6.

## 6. S3: backtest against the answer set, and the gate

### S3.1 Answer-set file

Add `atlas/scripts/build_q4_eps_answer_set.py`. It is read-only and takes a git ref, default `95184ade`.
- It writes `docs/report/q4-8k/answer-set.json`: `{"ref": "95184ade", "field": "epsDiluted", "rows":
  [{key, end, fy, value, netIncome, accession, filed, form}]}` from `secQ4EpsFacts[end].epsDiluted` and the
  stored value, for every Q4 row with `epsQuality[i].epsDiluted == "A"`.
- It also writes the `epsBasic` rows to a separate `rowsBasic` list, which is informational.
- It is a new file on main; it does not conflict with `data/opus-steps`.

### S3.2 Backtest mode with `--answer-set docs/report/q4-8k/answer-set.json`

- Process only (key, end) pairs in the set.
- The target NI is the row's `netIncome`.
- The reference is the row's `value`, not the stored main value.
- `--shard k/N` splits the answer-set tickers (sorted, index mod N) so that each run stays inside `--budget`
  (6,000 requests). Run as many shards as you need, and upload each report.

### S3.3 Gate (offline)

Add `--mode gate --reports <files…>`. It merges the shard reports and writes
`docs/report/q4-8k/s3-backtest-summary.json` with the definitions below.

| Term | Definition |
|---|---|
| extracted | the parser returned a value |
| accepted | extracted **and** `verified.status == "confirmed"`, i.e. it would be grade A |
| correct | \|estimate − answer\| ≤ max(0.011, 0.005·\|answer\|), both on today's split basis |
| precision | correct ÷ accepted |
| Wilson lower bound | (p + z²/2n − z·√(p(1−p)/n + z²/4n²)) ÷ (1 + z²/n), with z = 1.96 and n = accepted |
| sign error | an accepted row with sign(estimate) ≠ sign(answer), answer ≠ 0 |
| independent-check conflict | an **extracted** row with `verified.status == "conflict"` (counted over all extracted rows, not only accepted ones) |
| per-FY precision | precision by `quarterly.fy` of the period; checked only where accepted n ≥ 20 |

Also report:
- coverage = accepted ÷ answer rows;
- the reason counts;
- every incorrect, sign-error and conflict row with both values and the URLs;
- the same metrics **excluding dev tickers** (S1 fixtures).

### S3.4 Pass

All of these must hold:
- accepted **n ≥ 300**;
- Wilson lower bound **≥ 0.970**;
- **0** sign errors;
- **0** conflicts;
- every fiscal year with n ≥ 20 at **≥ 0.90**;
- precision excluding dev tickers **≥ 0.97** as a point estimate.

For scale: at n = 300 the lower bound allows at most 3 incorrect rows.

### S3.5 Fail

No S4. Report the failing numbers and the incorrect and conflict rows grouped by layout class.

### S3.6 One revision round

You may run one S2 revision followed by a fresh S3 run. The revision may be based only on dev-ticker rows and S1
diagnostics. A second fail is a stop: report and wait for Opus. A conflict that you believe is a legitimate
restatement is still a fail; list it for Opus, and do not exclude it yourself.

## 7. S4: fill code and preconditions

### Preconditions

All must hold. If any does not, stop and report which one.

- **P1:** the committed `s3-backtest-summary.json` passes S3.4.
- **P2:** `git merge-base --is-ancestor 95184ade origin/main` succeeds, i.e. the user has merged
  `data/opus-steps`. Otherwise report "S4 waiting for the data/opus-steps merge" and do nothing else for S4.
- **P3:** S0 baseline numbers are recorded. Recompute strict and default TTM on main after the merge and record
  them as the pre-fill baseline.

### Code (main; may be written and tested before P2, but not run)

1. `apply_fill` writes **`epsDiluted` only**, only into a **null** cell, only for **accepted** (confirmed, grade
   A) results. It also skips the period if `epsQuality[i].epsDiluted` already exists.
2. On write:
   - set `epsQuality[i].epsDiluted = "A"` and `epsQuality[i].epsDilutedFiled = <filing date>`, merging into any
     existing dict without changing other keys;
   - set `q4EpsSources[end] = {method: "8-K EX-99.1", accession, url, filingDate, grade: "A", verifiedUrl,
     asReported, splitFactor, epsBasicSeen, dilutedSharesSeen, estimateC}`, where `estimateC` is the
     `q4EpsEstimates[end]` value if one exists (evidence only).
3. Every other result goes **only** to `docs/report/q4-8k/fill-ledger.json` as `{key, end, value, grade:
   "C"|"conflict"|"unavailable", reason, url}`. This covers unconfirmed, unavailable, conflict and any
   field-blocked result. Add that path to the workflow's `git add`.
4. Add `atlas/scripts/check_q4_fill_diff.py <base-ref> <head-ref>`. It fails unless every change in
   `docs/report/financials/*.json` is one of:
   - `quarterly.values.epsDiluted[i]` null → number, at an index with `fq == 4`;
   - an `epsQuality[i]` dict gaining exactly `epsDiluted` and `epsDilutedFiled`;
   - a new `q4EpsSources[end]` key.

   It also fails on any change to another file except the ledger and `q4-8k-state.json`.
5. Tests:
   - null-only;
   - A-only;
   - C, conflict and unavailable go to the ledger and never into values;
   - `epsBasic` and `dilutedShares` are never written;
   - existing `epsQuality`, `secQ4EpsFacts`, `q4EpsEstimates` and `epsOverrides` stay byte-identical;
   - a re-run is idempotent;
   - `check_q4_fill_diff.py` rejects an overwrite and accepts a valid fill.

**Done when:** tests pass, P1–P3 hold, and the report says "S4 ready".

## 8. S5: fill in batches

- **Candidates:** US files on main (after the merge) with a Q4 period whose `epsDiluted` is null and whose
  `netIncome` is not null. That includes the 183 cells nulled by the share-estimate move.
- **Batches:** in ticker order, each batch is at most 60 tickers or 150 written values, whichever comes first.
  Run the workflow `fill` with `tickers=<batch>`; it commits to `backfill/q4-8k`.
- **Per batch:**
  1. Run `check_q4_fill_diff.py origin/main origin/backfill/q4-8k`. It must pass.
  2. **Spot check:** pick 20 random written rows (all of them if fewer than 20), with `random.seed(<batch
     number>)`.
  3. For each row, open the filing exhibit at `q4EpsSources[end].url` and read the table yourself, not by
     re-running the parser. Confirm:
     - the column is the three months (or 13/14 weeks) ending on `end`;
     - the row is total GAAP **diluted** EPS (not continuing operations, not adjusted or non-GAAP);
     - the value equals the stored value after `splitFactor`;
     - its sign equals the sign of NI.

     If your environment cannot reach SEC, add a read-only workflow step that saves those 20 exhibits as an
     artifact and read them from there.
  4. **Pass = 20 of 20.** Then merge `backfill/q4-8k` into main with a normal merge commit (no squash, no
     force). Record the strict and default TTM counts after the merge, and the batch's ledger counts.
  5. **Any failure:** stop all batches. Do not merge. Revert that batch's commit on `backfill/q4-8k` with
     `git revert` (no force-push). Report the failing rows.
- **Write per batch:** `docs/report/q4-8k/s5-batch-<NN>.json` with the tickers, rows written, ledger rows, the
  20 sampled rows with each verdict and URL, the diff-check result, and the TTM counts before and after.

**Done when:** all candidates have been processed, or a stop happened.

## 9. Stop conditions and reporting

### Stop conditions

Any of these stops all work:
- HTTP 403 from SEC: stop for the day, no retry with changed identity;
- a section 1 item;
- an S0 count mismatch;
- an S3 second fail;
- a missing S4 precondition;
- a failed diff check;
- a spot-check failure;
- unexpected changes to files you did not intend to touch;
- new instructions from the user or Opus.

### Report file

Write `docs/handoff/q4-eps-s1-s5-codex-report.md` on main and append one section per step. Each section has
exactly these fields:
- step;
- date (UTC);
- main commit(s);
- workflow run ids and artifact names;
- input counts;
- output files;
- key numbers (for S3: n, correct, precision, Wilson lower bound, sign errors, conflicts, per-FY table,
  excluding-dev precision, coverage);
- deviations from this file, with the reason;
- the stop, if any, and what you need from Opus or the user.

Keep the machine-readable outputs under `docs/report/q4-8k/`.

### When you stop or finish

Reply to the user in at most 15 lines: the step reached, pass or fail, the key numbers, the file paths, and the
open decision. The user relays it to Opus.
