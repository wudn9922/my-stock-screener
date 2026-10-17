# Amendment 1 to the S1–S5 instructions for Codex

Author: Claude Opus (decider), 2026-10-17. Applies on top of `docs/handoff/q4-eps-s1-s5-codex.md`, which is
not edited. Where this file and that one differ, this one wins. Both files are on `main` now (PR #102).

## 1. S3.1: what the answer value is

The answer `value` for an end date is `secQ4EpsFacts[end].epsDiluted.value`: the SEC fact on today's split
basis. The stored value in `quarterly.values.epsDiluted` goes in a separate `stored` field. Do not take the
answer from the stored cell.

## 2. What must not be merged, and where S1/S2 go

- PR #89 (`codex/q4-eps-backfill`) and PR #99 must not be merged in any form that carries financials JSON or
  #99's `build_growth.py` gating change. #99 overlaps #102 in 720 files and is based on the old main c0f84f34.
- S1 and S2 are code-only commits on `main`, based on `main` after #102 (merge commit 9cbc805a or later).
  They touch no file under `docs/report/financials/` and no `docs/report/growth.json`.
- The answer set is frozen at commit 95184ade. Do not regenerate it.

## 3. Taiwan statements (for Codex, not for the US work)

- PR #98 (`codex/tw-financials-finmind-2`) holds a completed run built on the old logic. Do not merge it as is.
- The re-run is approved only after all of these hold:
  1. PR #101 (merged, 0e89116f) is on main;
  2. F1 and F2 are ported into `build_financials_tw_finmind.py`, reusing `fmo.official_annual_eps` and
     `stable_share_count` from `build_fundamentals_tw_finmind.py`, with tests;
  3. a 20-stock `--tickers` trial shows 0 quarter-after-gap single quarters and annual EPS within 2 cents of
     official in at least 99% of years.
- PR #100 (`codex/tw-finmind-review-fixes`) duplicates F1 and F2 and conflicts with #101. Do not merge it.
  Close it as superseded.
