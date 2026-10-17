# S1 run 20: Opus review of the by-eye EPS reads (plan §3.4)

Reviewer: Claude Opus. Reviewed: `origin/claude/s1-run20-output` at 644aed20. Date: 2026-10-17.
Inputs: `tests/fixtures/q4_8k/s1_fixtures.json` and the fixture HTML next to it on that branch,
`docs/handoff/auto-s1-plan.md` §3.4/§3.5, and `docs/handoff/auto-s1-decision-2.md` on main.

Method: I dumped each fixture's table text with an HTML parser that uses the same flat table order as
`tables_of`. I did not run `table_eps`. A fixture holds only the tables in `tableIndices`, in ascending order, so
fixture table k is source table `tableIndices[k]`. The reader's table numbers are source indices.

Integrity checks:
- All 17 fixture sha256 values match the files.
- Commit 644aed20 changes only `epsDilutedRead`, `epsLabelRead` and `epsReadNote`, and only on positives.
- The top-level manifest keys are unchanged.

## Verdict: PASS, with three manifest edits

10 of the 12 reads match the HTML. The two TXT nulls are not misreads. They are a rule question, which item 2
decides. Once the edits below are applied, all 12 positives hold a numeric diluted EPS read from the HTML.

## 1. Read check (value, the reader's table, the target table)

- WLY-2023-04-30 (L1), 1.22: **OK.** Table 4 is "Reconciliation of US GAAP EPS to Non-GAAP Adjusted EPS".
  - The row "US GAAP Earnings Per Share - Diluted" shows $1.22 under "Three Months Ended April 30, 2023".
  - Target table 6 (the Net Income to EBITDA reconciliation, NI 68,344) has no EPS row. See item 3.
- URBN-2016-01-31 (L1), 0.61: **OK.** Table 3 (the target), under "Net income per common share:", row "Diluted",
  three months ended January 31, 2016: $0.61. The basic value is also 0.61.
- VNCE-2024-02-03 (L1), -0.37: **OK.** Table 1 (the target), row "Diluted (loss) earnings per share", three
  months ended February 3, 2024: $(0.37).
- TRAK-2019-06-30 (L2), 0: **OK.** Table 1 (the target), row "GAAP EPS, Diluted", three months ended 6/30/19:
  $0.00.
- TXT-2022-01-01 (L2), null: **not a misread.** Table 1 (the target) has no row labelled diluted. Item 2 sets
  the value.
- URBN-2014-01-31 (L2), 0.59: **OK.** Table 2 (the target), row "Diluted", three months ended January 31, 2014:
  $0.59.
- UI-2014-06-30 (L3), 0.55: **OK.** Table 14 (the target), row "Diluted", three months ended June 30, 2014: $0.55.
- UNFI-2019-08-03 (L3), 0.36: **OK.** Table 13 (the target), row "Diluted income (loss) per share", fourth
  quarter ended August 3, 2019 (14 weeks): $0.36. This is the net income total, not the continuing-operations
  row (0.02).
- VIAV-2021-07-03 (L3), -0.01: **OK.** Table 6 (the target), row "Net (loss) income per share - diluted", three
  months ended July 3, 2021: $(0.01). The net result is a loss of (1.9).
- TXT-2012-12-29 (L8), null: **not a misread.** Table 2 (the target) has no row labelled diluted. Item 2 sets
  the value.
- UNFI-2013-08-03 (L8), 0.65: **OK.** Table 10 (the target), under "Diluted per share data:", row "Net income",
  three months ended August 3, 2013: $0.65.
- URBN-2012-01-31 (L8), 0.27: **OK.** Table 2 (the target), row "Diluted", three months ended January 31, 2012:
  $0.27.

## 2. TXT-2022-01-01 and TXT-2012-12-29: decision (b), set a value by a stated rule

I reject (a) and (c). In both files the HTML itself marks the EPS block as diluted. Setting a value here
applies a rule to the text, not a guess. The same layout is the one that decision-2 C6 describes and that
`diluted_per_share_labels` now accepts ("Earnings per share:" / "Net income" / "Diluted average shares
outstanding").

**Rule (TXT single-basis EPS block).** A per-share block with no basic/diluted split is diluted EPS when both of
these hold:
- the table's only per-share share-count row is labelled diluted;
- no row in the table is labelled basic.

Read the block's net income row. If the block has no net income row, read the continuing-operations row, but only
when that quarter's discontinued operations is nil ("—"). Net income then equals income from continuing
operations, so the row is net income per diluted share.

Arithmetic (NI ÷ diluted shares) is a check only. It is not the source of the value.

TXT-2012-12-29, table 2:
- The "Earnings per share:" block has three rows: "Income (loss) from continuing operations" 0.50,
  "Discontinued operations" 0.01, and "Net income (loss)" $0.51.
- The only share row is "Diluted average shares outstanding (d)" 291,562,000. No row is labelled basic.
- Value: **0.51**, label "Net income (loss)".
- Check: 148 / 291.562 = 0.508. storedEps is 0.51.

TXT-2022-01-01, table 1:
- The "Earnings Per Share:" block has only "Income from continuing operations" $0.93.
- The only share row is "Diluted average shares outstanding" 222,860,000. No row is labelled basic.
- The same table's reconciliation is titled "Income from continuing operations and Diluted earnings per share
  (EPS) GAAP to Non-GAAP Reconciliation", and its EPS section repeats "Income from continuing operations - GAAP"
  $0.93.
- For the quarter, "Discontinued operations, net of income taxes" is "—", and "Net income" 207 equals "Income
  from continuing operations" 207.
- Value: **0.93**, label "Income from continuing operations".
- Check: 207 / 222.86 = 0.929. storedEps is 0.93.

Both stay S1 positives. Neither value differs from storedEps.

Note for S2, which does not block S1: the TXT-2022 row label contains "continuing", which `EPS_AVOID` excludes.
S2's L2 test on this fixture therefore needs the nil-discontinued-operations case above. Otherwise it records the
row as a known miss. S2 decides which. S1 records the true value.

## 3. WLY-2023-04-30 targetTableIndex: keep 6, accept table 4 with a note

`targetTableIndex` is not "the EPS table". It is `classify_row`'s target table: the table that holds the
accepted strict NI match and is not period-named (plan M1.4 rule 6, L1).
- Table 6 holds "Net Income" 68,344 under a "Three Months Ended April 30, 2023" header.
- Table 6 is consistent with the run's `s1-classes.json` / `s1-no-table-layouts.json` and with the fixture
  selection.

Changing the field would make the manifest disagree with the byte-identical run output. The field is also not
editable under §3.4.

The reading rule reads the diluted EPS row wherever it sits among the fixture's tables. Table 4 is inside
`tableIndices` [3, 4, 5, 6]. It is the preceding period table, and the fixture contains no full income statement.
So the read from table 4 is accepted, and the note must say where it came from (edit 3).

## 4. epsReadNote check

Rule: a note is required when |read − storedEps| > max(0.011, 0.005·|read|).

- **Note required, and present:**
  - WLY: |1.22 − 1.24| = 0.02;
  - URBN-2016: |0.61 − 0.59| = 0.02;
  - VIAV: |−0.01 − 0.01| = 0.02.
- **No note required, and none present:**
  - TRAK: |0 − 0.009| = 0.009;
  - URBN-2012: |0.27 − 0.28| = 0.01;
  - all others: 0.
- TXT, after edits 1 and 2: 0. Their notes record the rule, not a difference.

The three differences are real. They are not read errors.
- WLY: the HTML's GAAP diluted EPS for the quarter is 1.22.
- URBN-2016: the HTML shows 0.61 for both basic and diluted (72,878 / 118,606 = 0.614).
- VIAV: the quarter is a net loss of (1.9), so the stored +0.01 has the wrong sign.

These differences must stay recorded. Per §3.4 they do not block.

## 5. Counts (§3.4 and §3.5)

- Positives: L1 3 (WLY, URBN-2016, VNCE-2024), L2 3 (TRAK-2019, TXT-2022, URBN-2014), L3 3 (UI, UNFI-2019,
  VIAV), L8 3 (TXT-2012, UNFI-2013, URBN-2012).
- Every "S2 handles" class (L1, L2, L3, L8) has at least 2 positives, and each has 3.
- Negatives: 5, which meets the minimum of 5. They are TXT-2016 (L2), WLY-2018 (L4), TRAK-2020 (L5), VNCE-2026
  (L1-year-header) and TXT-2020 (L8-non-gaap-diluted-row).
- After the edits, every positive has a numeric `epsDilutedRead`, which the M5 test requires.
- Without edits 1 and 2, that test fails, even though L2 and L8 would still have 2 numeric positives each.

## Required manifest edits (`tests/fixtures/q4_8k/s1_fixtures.json`; no other field changes)

1. `TXT-2022-01-01-L2.html`:
   - `epsDilutedRead`: null → `0.93`;
   - `epsLabelRead`: null → `"Income from continuing operations"`;
   - `epsReadNote`: the "could not read by eye…" text → `"diluted by rule: EPS block has no basic/diluted split;
     only share row is 'Diluted average shares outstanding', no basic row; reconciliation titled 'Diluted
     earnings per share (EPS)'; discontinued operations '—' so net income 207 = income from continuing
     operations 207"`.
2. `TXT-2012-12-29-L8.html`:
   - `epsDilutedRead`: null → `0.51`;
   - `epsLabelRead`: null → `"Net income (loss)"`;
   - `epsReadNote`: the "could not read by eye…" text → `"diluted by rule (decision-2 C6 layout): EPS block has
     no basic/diluted split; only share row is 'Diluted average shares outstanding (d)', no basic row; read the
     'Net income (loss)' per-share row"`.
3. `WLY-2023-04-30-L1.html`:
   - `epsReadNote`: `"differs from storedEps by 0.02"` → `"differs from storedEps by 0.02; read from table 4 (US
     GAAP EPS reconciliation, three months ended April 30, 2023), not targetTableIndex 6 (net income to EBITDA
     table, no EPS row)"`;
   - `targetTableIndex` stays 6.
