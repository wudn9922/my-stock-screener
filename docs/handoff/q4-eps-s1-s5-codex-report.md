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
