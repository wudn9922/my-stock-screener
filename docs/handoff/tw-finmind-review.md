# Taiwan FinMind backfill: Opus review

Date: 2026-10-17. Reviewer: Claude Opus. Scope: `docs/report/fundamentals/tw-finmind.json` (version 1) and how
`atlas/scripts/build_growth.py` uses it. `tw-finmind.json`, `build_fundamentals_tw_finmind.py`,
`build_financials_tw_finmind.py` and `build_growth.py` are the same on `origin/main` (6924701d) and on
`data/opus-steps` (95184ade). I checked this with `git diff`, so every finding below applies to main.

Method: all checks ran offline against the files in the repo. I imported the producer's own functions
(`load_store`, `detect_modes`, `cross_check`, `field_views`, `company_periods`, `growth_candidates`) and compared
the results with `docs/report/fundamentals/tw.json` (the official MOPS / OpenAPI file: year-to-date, NT$ thousand,
seasons 2021Q4–2026Q2). I ran `python atlas/scripts/build_fundamentals_tw_finmind.py --verify`, which writes
nothing, and it printed **VERIFY PASS** on all 5 criteria. I wrote no code and no data.

The reported figures reproduce exactly: verdict `quarter` for rev, eps and ni; cross-check over 23,786 pairs;
median scale 1000.0; revenue within 2% = 1.000; `epsWithin2c` = 0.931; 1,971 FinMind growth rows; 1 excluded (4438).

---

## Decisions

| # | Question | Decision |
|---|---|---|
| a | Are the 10,048 null season rows legitimate? | **Yes.** All of them come before listing. None is a fetch gap. |
| b | Are the 6.9% of EPS checks outside 2 cents an error, and which source wins? | **Neither file is wrong.** The check compares a sum of single-quarter EPS with the reported cumulative EPS. For cumulative and annual EPS the official MOPS figure (`tw.json`) wins. FinMind single quarters are fine for quarterly YoY. The check does reveal one real consumer defect (F1). |
| c | Are 4438 and the missing-data symbol handled correctly? | **Yes, both are safe.** 4438 is a false positive of the contradiction rule (optional fix F3). The missing-data symbol is 3718, which FinMind had nothing for (correct). |
| d | Is the backfill complete for its purpose? | **Yes for `tw-finmind.json`**, the growth-history file. **No** for the FinMind statements backfill into `financials/*.TW(O).json`, which has not started. |
| e | Must anything be fixed before the next growth rebuild? | **Yes: F1 and F2.** Both are offline code changes in `build_fundamentals_tw_finmind.py` and need no refetch. F3 and F4 are optional. |

Note on (e): `fundamentals.yml` lines 102–105 rebuild growth.json on every run (`if: always()`). The defects
below are already in the current growth.json, and another rebuild will not make them worse. The fixes have to
land before Taiwan EPS YoY from FinMind counts as accepted.

---

## (a) The 10,048 null rows

The stored rows are aligned to 46 seasons. All 1,976 data rows are 46 entries long and end at 2026Q2. Trailing
nulls are cut by `dump_store` (L319–320), so this also means no row is behind.

| Where the nulls are | Rows | Codes |
|---|---:|---:|
| Leading (before a code's first stored season) | 7,513 | 522 |
| Interior | 2,535 | 488 |
| Trailing | 0 | 0 |
| **After a code's first complete Q1–Q4 year** | **0** | **0** |

All 2,535 interior nulls fall before the code's first complete calendar year. They follow the reporting
pattern of companies before listing (興櫃 and earlier issuers file only half-year and annual reports).
Year patterns over all codes (1 = season present, in Q1..Q4 order):

`1111` 18,395 · `1100` 1,975 (2026, the current year) · `0101` 977 (half-year reporter) · `0001` 497
(annual only) · `0111` 261 (listed during the year) · `0011` 17 · `1101` 15 · `0100` 2 · `1011` 1.

Interior gap sizes: 1 season (2,078 gaps), 2 (12), 3 (135), 13 (1), 15 (1). Q1 and Q3 dominate.

Against the official file there is only one season that `tw.json` has and FinMind does not: 7835 2024Q4. It is
an annual report filed before listing (創新板); FinMind starts at 2025Q4, and its 2026Q2 single quarter equals the
official YTD difference: 207,475 − 100,171 = 107,304 thousand. The 364 `tw.json` codes that are not in FinMind are
all outside universe.json (0 of 364 are in it).

Field nulls inside present rows:
- **rev-null 87.** 18 of them fall inside `tw.json`'s seasons, and `tw.json` is null there too: five insurers
  (2816, 2832, 2850, 2851, 2852) in 2026Q1–Q2, plus 6785, 6872, 6875, 6885 and 7827. The insurers are most
  likely the IFRS 17 format change. Both sources agree, so this is not a FinMind defect; it is listed as F5. The
  other 69 are pre-2022 seasons of biotech companies with no revenue yet (6547, 6550, 4174, …).
- **eps-null 1:** 6693 2018Q2, before listing.

**The nulls are legitimate.** However, the same patterns show that FinMind stores cumulative figures in quarter
slots for these early years. That is defect F2 below.

## (b) The 6.9% of EPS checks outside 2 cents

How the check works: `cross_check` (`build_fundamentals_tw_finmind.py` L596–635, the EPS test at L624) compares
the official YTD EPS with FinMind's YTD EPS "read with the verdict". Under the `quarter` verdict that is
`field_views` → `cumulate`, i.e. **Q1+…+Qn of single-quarter EPS** (L508–512, L525–529). A sum of single-quarter
EPS equals the reported YTD EPS only when the weighted share count stays the same all year.

Evidence (23,786 pairs, 1,633 outside 2 cents, 694 codes):

1. **Q1 has 0 of 5,630 outside 2 cents.** By quarter: Q2 237, Q3 397, Q4 999. Nothing is summed in Q1, so the
   error appears only where summing starts.
2. **Net income agrees.** In 1,456 of the 1,633 mismatches FinMind NI and official NI agree within 0.5%. In 160
   of the rest the official NI is null; only 17 disagree.
3. **The share count moves.** Of the mismatches where quarterly implied shares (NI ÷ EPS) can be computed, only
   19 of 1,381 have a spread across the year's quarters below 1%. Every other mismatch has a share count that
   moved during the year.
4. **How FinMind derives Q4.** FinMind Q4 EPS = official annual EPS − official 9-month YTD EPS in 3,826 of
   3,851 cases (99.35%), which is MOPS's own figures subtracted. FinMind Q2 and Q3 single-quarter EPS equal the
   official YTD differences in 8,875 of 9,604 cases. The other 729 are the reported 3-month EPS on a different
   share basis, which is expected.
5. Revenue, which does not depend on a share basis, agrees within 2% in 100% of the 23,786 pairs.

**Which source wins:**
- Cumulative or annual EPS: **official MOPS (`tw.json`)**, the reported figure.
- Single-quarter EPS for quarterly YoY: **FinMind as stored** is acceptable. Q1–Q3 are the published 3-month
  figures and Q4 = annual − 9M. I could not check Q1–Q3 against the filings themselves (no MOPS access here).
- The 25 Q4 cases (0.65%) that break the identity are FinMind anomalies. For example, 2474 2024Q4 stores 19.40,
  which is the annual EPS, not 19.40 − 13.88. **Official wins** for these.

**The consumer defect this exposes (F1):** under the `quarter` verdict FinMind's *annual* EPS is the sum of the
four quarters (`company_periods` L567–575, through `field_views` `ytd[4] = cumulate(4)` at L529). Against
official annual EPS for 2021–2025:
- level: 1,007 of 9,011 company-years (11.2%) are more than 2 cents off;
- annual EPS YoY: 469 of 5,594 pairs (8.4%) are more than 5 pp off, and 42 change sign.

`pick_block` (L700–712) chooses the FinMind annual block whenever it has more slots, so these values are live:
1,695 Taiwan rows in growth.json have `srcY: finmind`.

## (c) 4438 and the missing-data symbol

**4438 廣越** is excluded by `contradicting()` (L458–470). Its revenue looks YTD-like (Q1<Q2<Q3 and Q3≥2×Q1) in
10 of 10 years, but it is a strongly seasonal apparel maker: **Q4 < Q3 in all 10 years**, which a YTD series
cannot do. The FinMind values are exact single quarters. Revenue for 2024Q1–2026Q2 equals the official YTD
differences in 10 of 10 seasons; for example 2024Q2 is 4,132,785 = 6,500,420 − 2,367,635. The exclusion is safe,
because growth.json falls back to the `mops` block (row `4438.TW`, src `mops`), but it drops the longer annual
history. Optional fix: F3.

**3718 中光電投控** is the code in `fetch` but not in `data`: `["2026-10-15", null, 0]`. It is a newly formed
holding company. FinMind returned no rows, and `tw.json` has nothing for it either. Handling is correct: it is
planned as `empty` and re-checked every `SLOW_RECHECK_DAYS` = 30 (`plan_fetches` L358–361), and it has no growth
row. Nothing to do.

Related facts, no action needed:
- **8908** has data and a fetch entry but left universe.json on 2026-10-16. That is why the universe count is
  1,976 and the fetch count 1,977. `symbol_map` has no symbol for it, so growth ignores it, and `plan_fetches`
  never refetches it.
- 3717, 7827 and 7835 have data but no growth row: too few quarters for a YoY.

## (d) Is the backfill complete?

The stated purpose is about 10 years of quarterly revenue, EPS and NI history in growth.json. For that,
**complete**. `--verify` passes:
- 1,976 of 1,976 universe stocks fetched;
- 1,975 of 1,976 have data;
- 1,975 of 1,975 are current to the due season 2026Q2;
- the verdict is decided;
- the official cross-check passes: 23,786 pairs, median 1000.0, 100% within 2%.

The informational Yahoo annual-revenue check: 1,981 company-years, median 1.0, 95.9% within 2%.

**Not complete: the FinMind statements backfill** (`build_financials_tw_finmind.py`). Its state file
`docs/report/fundamentals/tw-financials-finmind.json` does not exist. All 515 Taiwan financials files have
`source = "Yahoo Finance fundamentals-timeseries"` and no `finmind` periods: 392 `.TW` files plus 123 `.TWO`
files (the 392 figure covers only `.TW`). The daily step (`fundamentals.yml` L93–100,
`--only-after-backfill`, `BACKFILL_SHARE` 0.95) does nothing until someone runs that backfill by hand. If the
user meant that backfill, it is at 0%.

## (e) Required before the growth rebuild is relied on

### F1 (required): annual EPS must not be a raw sum of quarters

File: `atlas/scripts/build_fundamentals_tw_finmind.py`, functions `company_periods` (L539–576) and
`growth_candidates` (L638–672).

Change:
1. In `growth_candidates`, build `official_annual = {code: {year: eps}}` from the `official` document that is
   already passed in: `tw.load_store(official)["data"][code]["<year>Q4"][1]` when it is not null. On a
   `StoreError`, use `{}`.
2. Pass `official_annual.get(code, {})` into `company_periods(row, modes, official_annual=…)`.
3. In the annual period, only when `modes["eps"] == "quarter"`:
   - if `official_annual` has the year: `epsB` = that official EPS. Keep the share-basis units consistent with
     the FinMind periods: `shares` = FinMind ΣNI (NT$) ÷ official EPS. **Never** use the official implied shares,
     which are in thousands; mixing units would trip `share_count_jump` (`build_growth.py` L229, ratio 1.5).
   - else: keep the sum of the four quarters only if quarters Q1–Q3 each have implied shares (|EPS| ≥ 0.1, NI
     and EPS with the same sign) and max/min − 1 < 0.02. Otherwise `epsB = None` for that year. Revenue is not
     affected.
4. Leave `ytd` and `q4annual` mode behaviour unchanged.

Evidence for the 2% threshold, on 2021–2025 against official:
- the gated sum matches the level within 2 cents in 4,392 of 4,453 years (98.6%), against 88.8% ungated;
- annual YoY within 2 pp in 2,146 of 2,185 (98.2%), against 83.2% ungated.

Before 2021 the gate leaves about half of the years without annual EPS. That is the intended cost: null, not a
known-wrong value.

Tests (`tests/test_fundamentals_tw_finmind.py`):
- `test_annual_eps_prefers_the_official_full_year`: share count rises in Q3 so ΣQ ≠ annual. With official Q4
  YTD EPS given, annual `epsB` equals the official value. YoY between a year with official EPS and one without
  is not nulled by `share_count_jump`.
- `test_annual_eps_sum_needs_a_stable_share_basis`: no official data. Quarter shares spread 5% → `epsB` None;
  spread 0.5% → the sum.
- Update `test_q4_from_the_full_year_and_annual_eps_as_the_sum_of_quarters` (L259) for the gate.
- Acceptance on the stored file: rebuild growth to a scratch path and confirm that FinMind annual EPS matches
  official annual EPS within 2 cents in ≥ 99% of the overlapping years.

### F2 (required): a quarter that follows a missing quarter is cumulative, not a single quarter

File: same module, `field_views` (L499–536), `quarter` and `q4annual` branches.

Change: put `raw[q]` into `single[q]` only when `q == 1` or `q - 1` is present in `quarters_of_year`. This
applies to rev, eps and ni. `ytd` mode already requires `q - 1`.

Evidence. Revenue ratio of a quarter to the same quarter one year later; normal full years give 0.98:

| Year pattern | Quarter | Ratio | Years | What the slot holds |
|---|---|---:|---:|---|
| `0101` | Q2 | 1.71 | 207 | first half |
| `0101` | Q4 | 1.79 | 207 | second half |
| `0111` | Q2 | 1.76 | 211 | first half |
| `0111` | Q3, Q4 | 0.95, 0.96 | 212, 209 | fine |
| `0001` | Q4 | 3.08 | 30 | full year |
| `0011` | Q3 | 2.88 | 15 | 9 months |
| `1101` | Q4 | 1.84 | 4 | second half |

Today these feed **661 of 22,693 quarterly YoY slots (2.9%) in FinMind blocks, across 212 codes**, and 43 of
them sit in the newest slot (2026Q2 vs 2025Q2) for stocks that listed recently.

Tests:
- `test_a_quarter_after_a_missing_quarter_is_not_a_single_quarter`: a `0101` year (Q2 = H1, Q4 = H2) followed by
  a full year. The quarterly periods of the first year have `rev` and `epsB` None, and the next year's Q2 and Q4
  YoY are None.
- A `0111` year keeps Q3 and Q4 and drops Q2.
- A `0001` year drops Q4.
- Existing `test_all_three_styles_give_the_same_quarters_and_years` (L235) still passes.

### F3 (optional): fewer false contradictions

In `contradicting()` (L458–470), for `mode in ("quarter", "q4annual")`, count a year as YTD-like only if
`ytd_like(quad) and quad[3] > quad[2]`. With this rule no stock is flagged on the current file, and 4438 gets its
FinMind history back. Test: a seasonal stock (Q1<Q2<Q3, Q3≥2×Q1, Q4<Q3 every year) is not excluded, and a real
YTD stock (Q4 > Q3) still is.

### F4 (optional): make the EPS cross-check measure something real

`epsWithin2c` mixes in the share-basis effect, so its 0.931 hides any real error. Replace it, or report next to
it:
- Q1 direct EPS agreement (now 5,630 of 5,630);
- Q4 identity agreement, FinMind Q4 = official annual − official 9M (now 3,826 of 3,851 = 99.35%).

Add the identity rate to `--verify` as a criterion at ≥ 99%.

### F5 (later, not FinMind-specific): insurer revenue under IFRS 17

Both sources have null revenue for the five insurers in 2026Q1–Q2. Look at the MOPS IFRS 17 line names before
2026 annual YoY is needed. This does not block anything.

### Who implements the fixes

F1–F4 are code changes on main. Codex writes main and Claude writes branches, so the user assigns the owner.
They are not part of the Q4 EPS S1–S5 hand-off (`docs/handoff/q4-eps-s1-s5-codex.md`) and touch no file that
`data/opus-steps` changes. When they are done:
- `python -m pytest tests/test_fundamentals_tw_finmind.py tests/test_growth.py` passes;
- `python atlas/scripts/build_fundamentals_tw_finmind.py --verify` passes;
- a growth rebuild to a scratch path shows `srcY:finmind` annual EPS agreeing with official within 2 cents in
  ≥ 99% of the overlapping years, and no quarterly FinMind slot built on a cumulative figure (the F2 count is 0).

## Not verified, and why

- I could not check FinMind against the MOPS filings themselves. There is no MOPS or FinMind network access here,
  and third-party sites are not allowed. "Official" here means `tw.json`.
- The pre-listing explanation of the leading nulls rests on the reporting pattern and the first-season
  distribution. Universe.json has no listing dates, so I could not check those dates.
- I did not check the effect of stock dividends on quarterly YoY across years (FinMind keeps prior quarters as
  published). The official `mops` path has the same property, and `share_count_jump` catches only ≥ 1.5×
  changes. This is unchanged existing behaviour, not a regression.
