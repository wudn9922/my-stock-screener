# S2b decision (Opus), 2026-10-17

Author: Claude Opus (decider). Audience: the lead session, the Haiku implementer and the Opus reviewer.
This file applies on top of `q4-eps-s1-s5-codex.md`, `s1-opus-decision.md`, `auto-s1-decision-2.md` and
`auto-s1-plan.md`. Where it differs from them, this file wins. It loosens nothing:
- handoff §1.6 is unchanged: `NI_TOLERANCE`, `SHARES_TOLERANCE`, the sign rule, the zero-EPS and |EPS| ≥ 1000
  rejections, the `NI_AVOID` / `EPS_AVOID` / non-GAAP exclusions, and every gate number;
- the S3 gate and the answer set (pinned to 95184ade) are unchanged.

Inputs:
- Haiku's S2b stop report;
- the WIP commit 1623c6fd on `claude/s2b` (base c40ebbf2);
- the 17 fixtures in `tests/fixtures/q4_8k/s1_fixtures.json`;
- the final S1 counts on main: L2 74, L8 62, L1 13, L3 7.

I checked every claim below on the fixture HTML, with the stored `netIncome` and the annual NI of each file as
the target. I used a scratch prototype (WIP plus the changes in §3), which was not committed:
- the full suite runs 1,188 tests, all green unchanged;
- table outcomes change only on the 4 positives named in §2.

## 1. Verdicts on the six questions

Terms: (a) is a spec-compatible implementation of an existing S2 item; (b) is a new rule the spec does not
contain; (c) is a loosening of handoff §1.6, which is a stop for that rule.

| # | Question | Kind | Verdict |
|---|---|---|---|
| 1 | "per common share" heading as an L8 item | (a) | **Accept** |
| 2 | Unit rule for the shares check (TXT-2012) | (b); (c) if it tries several units | **Reject** |
| 3 | Diluted heading followed by a net income row (UNFI-2013) | (a) | **Accept** |
| 4a | Zero EPS (TRAK-2019, $0.00) | (c) | **Reject (stop for that rule)** |
| 4b | Continuing-operations EPS (TXT-2022) | (c) | **Reject (stop for that rule)** |
| 5 | EPS from another table of the same document (WLY-2023) | (b) | **Reject** |
| 6 | L3 accepted-label list; VIAV-2021 stored NI | (b) | **Reject for this round**; VIAV-2021 becomes a negative |

No (b) item is accepted, so this file adds no new spec rule.

### Item 1. Accept, (a)

`s1-opus-decision.md` §2 defines L8 as including "a heading `table_eps` does not recognise". F2 names this exact
heading: URBN, UFI and WMT use "Net income per common share:". S2 handles L8 "label and heading variants covered
by fixtures only", and URBN-2012 is that fixture.

The implementation is limited to the fixtures:
- **Heading test.** In `_labelled_rows`, a heading is a labelled row whose values are all None and whose label
  contains `per share` or `per common share`.
- **Not added.** Do not add `per diluted share` or `earnings per` as heading tokens; no fixture covers them. The
  WIP's use of the full `HEADING_TOKENS` is not accepted.
- **Unchanged.** `HEADING_TOKENS` stays diagnose-only, with its comment unchanged. The 2-row window and the
  |v| < 1000 guard are also unchanged.

### Item 2. Reject, (b)

This is the TXT-2012 case: NI is in $ millions and the share count is printed in units.
1. Once an EPS row is found, the table is rejected by the shares check. That is the L10 `check-reject` mechanism,
   which `s1-opus-decision.md` §2 rule 3 puts "out of scope for new rules".
2. A unit rule that tries more than one share unit (1, 1e3, 1e6) and keeps any that passes weakens the check. At
   |EPS| ≤ about 0.4, `EPS_FLOOR` (0.02) dominates, so a share count 1,000 times too large still "passes". That
   is a loosening of the shares check in effect, so it is (c).
3. Only one ticker (TXT) has this layout among the fixtures.

Consequence: the S1-CF C6 TXT layout ("Earnings per share:" / "Net income" / diluted shares, no basic row) is
**not** added to `table_eps` this round. Every fixture it reaches ends in a shares-check reject, so the rule
would have 0 accepted positives. C6 stays diagnose-only, as `auto-s1-decision-2.md` §5 wrote it. Drop
`_heading_net_row` from the WIP. TXT-2012 is not an L8 positive.

### Item 3. Accept, (a)

UNFI-2013's table has the heading "Diluted per share data:" followed by a "Net income" row of per-share values.
This is an L8 heading variant, covered by this fixture. It is not the C6 rule: the table has a basic block, and
the heading itself names diluted.

The rule, in `_labelled_rows`, applies only on the S2 path (not `s2=False`):
- **Diluted heading.** When a heading (item 1 test) matches `\bdiluted\b` and does not match `\bbasic\b`, the
  first row within its 2-row window that meets all of these is returned as `diluted earnings per share`:
  - `is_net_income(label)` is true;
  - the label has no `EPS_AVOID` word;
  - at least one value is not None;
  - every non-None value has |v| < 1000.
- **Once only.** The rule renames at most one row per heading.
- **Other headings unchanged.** A heading with `basic`, or one with both `basic` and `diluted` (for example
  "Basic and diluted net income per share:"), is unchanged by this rule.
- **Checks still apply.** NI matching (raw cells), one column, column counts, zero, |EPS| ≥ 1000, sign and shares
  all apply. For UNFI-2013 the shares check passes: 32,077 / 49,646 = 0.646 against 0.65.

### Item 4a. TRAK-2019 zero EPS: reject, (c)

`table_eps` rejects EPS == 0, and `test_zero_eps_is_never_accepted` pins that. A $0.00 against positive NI
(182,303) also fails the sign rule `(eps > 0) != (net > 0)`. Accepting it means changing the sign rule, which
§1.6 forbids. In addition, the printed $0.00 reflects NI to common after preferred dividends (35,692), not the
matched row. TRAK-2019 stays rejected. The zero-EPS test is unchanged.

### Item 4b. TXT-2022 continuing-operations EPS: reject, (c)

The only per-share row is "Income from continuing operations". `continuing` is in `EPS_AVOID`, and that tuple
carries the §1.6 exclusions: it holds the non-GAAP words plus `continuing`, for "total EPS". The S5 spot check
also defines a correct row as "total GAAP diluted EPS (not continuing operations…)". A rule such as "continuing
equals total when discontinued is nil" still selects a row that S5 fails by definition. TXT-2022 stays rejected.

### Item 5. WLY-2023: reject, (b)

The target NI is in an EBITDA reconciliation (table 3) with no EPS row. The GAAP diluted EPS is in a different
table (table 1), which also carries non-GAAP rows. The spec pairs NI and EPS within one table only (handoff S2.1
and S2.4). Pairing across tables would be a new rule with one fixture and no NI anchor beside the EPS row. L1
passes its gate without it (§2). WLY-2023 stays a documented miss.

### Item 6. L3: reject for this round, (b). VIAV-2021 becomes a negative

The spec has no accepted-label list, so any L3 rule is (b). None of the 3 positives supports one:
- **UI-2014.** "Net income and comprehensive income" is the only clean case (49,418 = the stored NI). It is 1
  positive, and the gate needs 2.
- **UNFI-2019.** The stored NI (19,197K) matches only "Net income (loss) including noncontrolling interests"
  (19,160K), and only at 0.19% against the 0.20% tolerance. The company's NI (18,937K) is 1.35% off. Accepting
  NCI-inclusive labels matches a different concept, and this fixture is a near-tolerance coincidence. Rejected.
- **VIAV-2021.** This is not an L3 row. The stored Q4 NI is 3.3M and the stored `epsDiluted` is 0.01, both
  graded Y (derived). The release shows a Q4 net loss of −1.9M and EPS −0.01. The 3.3M matches only "Interest
  income and other income, net" in the **full-year** column. This is a stored-value defect.
  - Record it for the user. Do not edit financials (handoff §1.4).
  - Use the fixture as a **negative**: it must stay rejected.

With fewer than 2 usable positives, L3 is "out of scope for this round". That follows the
`s1-opus-decision.md` §2 rule: a class is "S2 handles" only with at least 2 real positive fixtures. Here, a
fixture that can pass only through a rejected or §1.6-loosening rule does not count.

## 2. Class outcome

| Class | Positives accepted | Negative(s) rejected | S2b |
|---|---|---|---|
| L2 (74) | URBN-2014 at most (it needs item 1 plus C7 rule "v"); TRAK-2019 (4a) and TXT-2022 (4b) cannot pass | TXT-2016 | **out of scope this round** (1 < 2); no L2 rule and no wording change |
| L8 (62) | URBN-2012 0.27 (item 1), UNFI-2013 0.65 (item 3); TXT-2012 not (item 2) | TXT-2020, TXT-2016 | **rule, commit 1** |
| L1 (13) | VNCE-2024 −0.37, URBN-2016 0.61 (needs item 1, so it comes after L8); WLY-2023 not (item 5) | VNCE-2026 | **rule, commit 2** |
| L3 (7) | none usable (item 6) | VIAV-2021 (new role) | **out of scope this round** |

The stop rule in plan §6.5 ("An S2 rule needs a §1.6 loosening") is resolved by not writing those rules. The
L8 and L1 rules need no loosening, so the loop is not stopped for good.
- `s1-no-table-layouts.json` is S1 output and is not edited.
- The S2 report section records the changed dispositions (L2 and L3 out of scope for this round) and cites this
  file.
- The TRMB 7-day rule (decision §5) is moot this round, because no L2 rule is written.

## 3. The WIP commit 1623c6fd: keep as the code base, do not merge it as is

Keep:
- the `_Table` grid (colspan and rowspan);
- `_grid`, `_header_row_indices` and `_period_columns`;
- `_look_back` (S2.1 alignment, the S2.3 year-wording and annual-NI rejections);
- the `preceding` / `annual` plumbing through `from_filing`, `find_quarter`, `process_ticker` and
  `verify_quarter`;
- `annual_net_income`;
- the `s2=False` path for the S1 classifier evidence, so `classify_row` replays are unaffected;
- the `header_text` refactor, which must stay behaviour-identical.

Change:
1. Remove `_heading_net_row` and its call (item 2).
2. Restrict the heading words to `("per share", "per common share")`, and restore the `HEADING_TOKENS` comment
   (item 1).
3. Add the item 3 rule.
4. Apply the S2.3 annual-NI rejection also to a result whose EPS row came from an item 1 or item 3 rename
   (reason `net income equals the full-year net income`).

Rebuild `claude/s2b` from **origin/main** (not from c40ebbf2) as exactly two rule commits:
- **Commit 1, L8.** Items 1 and 3, with their tests.
- **Commit 2, L1.** The look-back, the grid and the annual plumbing, with their tests.

Each commit ends with the session's attribution lines. 1623c6fd itself is not merged and is not a review target.
Do not force-push it away before commit 2 exists. After that, `claude/s2b` may be reset to the rebuilt history,
because it is unmerged WIP.

## 4. Tests

**Existing tests that change: 0.** All 1,188 existing tests stay green unchanged, including
`test_zero_eps_is_never_accepted` and all of `tests/test_backfill_q4_eps_8k.py` (checked on the prototype).

New tests (the fixtures are read from `tests/fixtures/q4_8k/`; the target and annual NI are inlined from the
financials, with no file reads of `docs/report/financials/` in tests):
- **Commit 1, L8:**
  - URBN-2012 → 0.27 and UNFI-2013 → 0.65;
  - TXT-2020 and TXT-2016 stay rejected;
  - TXT-2012 gives no result;
  - synthetic: a "Basic per share data:" heading plus "Net income" gives no EPS row;
  - synthetic: "Basic and diluted net income per share:" plus "Net income" is not renamed;
  - synthetic: a diluted heading plus "Income from continuing operations" is not renamed;
  - synthetic: a "per diluted share" heading is not a heading;
  - the annual-NI rejection on a renamed row.
- **Commit 2, L1:**
  - VNCE-2024 → −0.37 and URBN-2016 → 0.61;
  - VNCE-2026 stays rejected;
  - WLY-2023 gives no result;
  - synthetic: a misaligned header gives `period header not aligned`;
  - synthetic: a twelve-month column is rejected;
  - synthetic: annual NI equal to the target is rejected;
  - synthetic: a 4th preceding table is not used.
- **Out-of-scope pins (commit 2):**
  - URBN-2014, TRAK-2019, TXT-2022, UI-2014 and UNFI-2019 give no result;
  - VIAV-2021 stays rejected.

## 5. Next step

1. Haiku rebuilds `claude/s2b` per §3 and §4: L8 first, then L1. It opens the PR and lists "0 existing tests
   changed" in the body.
2. Opus reviews against this file. The checks:
   - the suite is green;
   - the diff touches only `atlas/scripts/backfill_q4_eps_8k.py` and `tests/`;
   - the fixture outcomes equal §2 exactly;
   - `classify_row` on run 20's verbose output reproduces `s1-classes.json` with 0 mismatches;
   - no §1.6 constant differs from main.

   At most 1 fix round (2 FAILs → stop). Then a normal merge.
3. Then S2 acceptance (plan §4): the S2 report section records the changed-test list (0), the L2/L3
   dispositions, and the VIAV-2021 stored-value defect. Then S3 as planned. No SEC request before S3.
