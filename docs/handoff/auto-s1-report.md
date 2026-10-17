# Automated S1 report

The loop keeps this file on branch `claude/auto-s1-report`. Each milestone appends one section (plan §6.6).

## Stop: stage 3.3 (run 19), L9 above the limit

- Date: 2026-10-17 13:23 UTC.
- Stage: 3.3 (run 19 check). Plan §6.5 stop: "L9 > 23 after run 19".
- Outcome: STOP. No S1 output PR, no fixture import, no S2b. Loop not re-armed.
- PRs merged before this stop: #110 (automated plan, handoff only), #111 (S2a-1: S0 baseline,
  answer-set builder, answer-set.json pinned to 95184ade; Opus PASS), #112 (S1 M1–M6; Opus PASS at 866acb8a).
- Run 19: Actions run 39035520759, dispatched on main at 13:11 UTC with mode diagnose, budget 6000.
  extractOutcome success; diagnoseRun.stopped null; SEC requests 2,630; no 403.
- Commit on runs/q4-8k holding the run output: bfe6a1e485b7b41d818d00c4d86e8cf2953d14cc (head at fetch time).

### Numbers

- sourceRows 235, rowCount 235, status complete.
- classCounts: L1 14, L2 43, L3 7, L4 0, L5 0, L6 0, L7 0, L8 56, L9 92, L10 23.
- otherLimit: {'limit': 23, 'count': 92, 'passed': False}.
- fixtureCount 17; fixtureErrors: 1 (TRMB 2011-12-30, positive, 'too large').
- Note: L4 is 0 in this run. Opus interpretation 1 says rule 9 (L4) applies whenever there is no
  strict match, so L4 should not be empty on the run-17 rows. This may be a precedence or code gap.
  Opus to check.

### L9 notes (all 92 rows, verbatim)

- TRAK 2014-06-30 (table None): (d) ex99-10082014_021051.htm: strict match only in raw text, not in a table cell
- TRAK 2016-06-30 (table None): (d) ex99-11082016_021119.htm: strict match only in raw text, not in a table cell
- TRAK 2018-06-30 (table None): (d) ex99-1.htm: strict match only in raw text, not in a table cell
- TRAK 2020-06-30 (table 0): (d) table 0 of ex991.htm: strict match only under label 'Current portion of paycheck protection program loans', which is not an accepted net income label
- TRAK 2021-06-30 (table 10): (d) table 10 of ex_309173.htm: strict match only under label 'Income from operations', which is not an accepted net income label
- TRAK 2022-06-30 (table 12): (d) table 12 of ex_447772.htm: strict match only under label 'General and administrative', which is not an accepted net income label
- TRAK 2023-06-30 (table 20): (d) table 20 of ex_575930.htm: strict match only under label 'Sale of property and equipment', which is not an accepted net income label
- TRAK 2024-06-30 (table None): (d) ex_729117.htm: strict match only in raw text, not in a table cell
- TRAK 2025-06-30 (table None): (d) ex_865467.htm: strict match only in raw text, not in a table cell
- TRAK 2026-06-30 (table None): (d) ex_1020954.htm: strict match only in raw text, not in a table cell
- TRMB 2018-12-28 (table 8): (a) table 8 of d698387dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Fourth Quarter of Fiscal Years'
- TWIN 2012-06-30 (table 5): (d) table 5 of td8k07312012ex991.htm: strict match only under label 'Impairment charge', which is not an accepted net income label
- TWIN 2021-06-30 (table None): (d) ex_275501.htm: strict match only in raw text, not in a table cell
- TWIN 2022-06-30 (table None): (d) ex_418797.htm: strict match only in raw text, not in a table cell
- TWIN 2023-06-30 (table None): (d) ex_561226.htm: strict match only in raw text, not in a table cell
- TWIN 2025-06-30 (table None): (d) ex_855273.htm: strict match only in raw text, not in a table cell
- TXT 2011-12-31 (table 2): (b) table 2 of a12-3382_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2012-12-29 (table 2): (b) table 2 of a13-3441_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2013-12-28 (table 1): (b) table 1 of a14-4002_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2015-01-03 (table 2): (b) table 2 of a15-3145_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2016-01-02 (table 2): (b) table 2 of a16-2933_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2016-12-31 (table 2): (b) table 2 of a17-3147_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2017-12-30 (table 2): (b) table 2 of a18-5049_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2018-12-29 (table 1): (b) table 1 of a19-3355_1ex99d1.htm: net income is only in a period-named table without EPS rows
- TXT 2020-01-04 (table 4): (b) table 4 of tm206034d1_ex99-1.htm: net income is only in a period-named table without EPS rows
- TXT 2022-12-31 (table 1): (a) table 1 of q4228-kex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Twelve Months Ended December 31,2022 January 1,2022 December 31,2022 January 1,2022 REVENUES MANUFACT'
- ULTA 2013-02-02 (table 25): (a) table 25 of d502755dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '14 Weeks Ended 13 Weeks Ended February 2,2013 January 28,2012 (Unaudited) (Unaudited)'
- ULTA 2015-01-31 (table 30): (a) table 30 of d889169dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '13 Weeks Ended 13 Weeks Ended January 31,2015 February 1,2014 (Unaudited) (Unaudited)'
- VEEV 2018-01-31 (table None): (d) a51765017ex99_1.htm: strict match only in raw text, not in a table cell
- VIAV 2015-06-27 (table 12): (a) table 12 of viavq1fy16earningsrelease1.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended October 3, 2015 September 27, 2014'
- VIAV 2016-07-02 (table 28): (d) table 28 of viavq4fy168-kex991.htm: strict match only under label 'Operating income (loss)', which is not an accepted net income label
- VIRC 2022-01-31 (table None): (d) exhibit991q401312022pressr.htm: strict match only in raw text, not in a table cell
- VIRC 2023-01-31 (table None): (d) exhibit991q401312023pressr.htm: strict match only in raw text, not in a table cell
- VIRC 2024-01-31 (table None): (d) exhibit991q401312024pressr.htm: strict match only in raw text, not in a table cell
- VIRC 2025-01-31 (table None): (d) exhibit991q401312025pressr.htm: strict match only in raw text, not in a table cell
- VIRC 2026-01-31 (table None): (d) exhibit991q401312026pressr.htm: strict match only in raw text, not in a table cell
- VNCE 2015-01-31 (table 19): (a) table 19 of d894283dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Fiscal Year Ended January 31,2015 February 1,2014 January 31,2015 February 1,2014'
- VNCE 2019-02-02 (table 28): (d) table 28 of vnce-ex99_14.htm: strict match only under label 'Earnings (loss) per share', which is not an accepted net income label
- VNCE 2021-01-30 (table 36): (d) table 36 of vnce-ex991_6.htm: strict match only under label 'Earnings (loss) per share', which is not an accepted net income label
- VRA 2013-02-02 (table 5): (a) table 5 of d500741dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'FourteenWeeks EndedFebruary 2,2013 ThirteenWeeks EndedJanuary 28,2012 Fifty-ThreeWeeks EndedFebruary 2,2013 Fifty-TwoWee'
- VRA 2021-01-30 (table 1): (a) table 1 of vra-20210130xex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Thirteen Weeks Ended Fifty-Two Weeks Ended January 30,2021 February 1,2020 January 30,2021 February 1,2020'
- VRA 2024-02-03 (table 1): (a) table 1 of vra-20240203xex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Fourteen Weeks Ended Thirteen Weeks Ended Fifty-Three Weeks Ended Fifty-Two Weeks Ended February 3,2024 January 28,2023 '
- VRA 2026-01-31 (table 1): (a) table 1 of vra-20260131xex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Thirteen Weeks Ended Fifty-Two Weeks Ended January 31,2026 February 1,2025 January 31,2026 February 1,2025'
- WLY 2017-04-30 (table None): (d) 0000107140-17-000018-index.html: strict match only in raw text, not in a table cell
- WLY 2018-04-30 (table 40): (d) table 40 of exhibit99.htm: strict match only under label 'Journal Subscriptions', which is not an accepted net income label
- WLYB 2017-04-30 (table None): (d) 0000107140-17-000018-index.html: strict match only in raw text, not in a table cell
- WLYB 2018-04-30 (table 40): (d) table 40 of exhibit99.htm: strict match only under label 'Journal Subscriptions', which is not an accepted net income label
- WMS 2015-03-31 (table None): (d) d924084dex991.htm: strict match only in raw text, not in a table cell
- WMS 2016-03-31 (table None): (d) d307276dex991.htm: strict match only in raw text, not in a table cell
- WMS 2017-03-31 (table None): (d) wms-ex991_7.htm: strict match only in raw text, not in a table cell
- WMT 2013-01-31 (table 41): (a) table 41 of earningsrelease.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'SUBJECT TO RECLASSIFICATION Three Months Ended Fiscal Years Ended January 31, January 31,'
- WMT 2014-01-31 (table 34): (a) table 34 of earningsrelease-13114.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Fiscal Years Ended SUBJECT TO RECLASSIFICATION January 31, January 31,'
- WMT 2015-01-31 (table 63): (a) table 63 of earningsrelease-1312015.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Quarters Ended Fiscal Years Ended SUBJECT TO RECLASSIFICATION January 31, January 31,'
- WMT 2016-01-31 (table 50): (a) table 50 of earningsrelease-1312016.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Quarters Ended Fiscal Years Ended SUBJECT TO RECLASSIFICATION January 31, January 31,'
- WMT 2017-01-31 (table 50): (a) table 50 of earningsrelease-1312017.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Quarters Ended Fiscal Years Ended January 31, January 31,'
- WMT 2019-01-31 (table 119): (a) table 119 of earningsrelease-1312019.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Quarters Ended Fiscal Years Ended January 31, January 31,'
- WMT 2020-01-31 (table 75): (a) table 75 of earningsrelease-1312020.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Fiscal Years Ended January 31, January 31,'
- WMT 2021-01-31 (table 13): (a) table 13 of earningsreleasefy21q4.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Fiscal Year Ended January 31, January 31,'
- WMT 2022-01-31 (table 13): (a) table 13 of earningsreleasefy22q4.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Fiscal Year Ended January 31, January 31,'
- WMT 2023-01-31 (table 14): (a) table 14 of earningsreleasefy23q4.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'Three Months Ended Fiscal Year Ended January 31, January 31,'
- WOR 2012-05-31 (table 0): (a) table 0 of exh99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2012 3Q 2012 4Q 2011 12M 2012 12M 2011'
- WOR 2013-05-31 (table 0): (a) table 0 of exh99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2013 3Q 2013 4Q 2012 12M 2013 12M 2012'
- WOR 2014-05-31 (table 0): (a) table 0 of exh99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header '(U.S. dollars in millions, except per share data) 4Q 2014 3Q 2014 4Q 2013 12M2014 12M2013'
- WOR 2015-05-31 (table 0): (a) table 0 of exh99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2015 3Q 2015 4Q 2014 12M2015 12M2014'
- WOR 2016-05-31 (table 0): (a) table 0 of exh99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2016 3Q 2016 4Q 2015 12M 2016 12M 2015'
- WOR 2017-05-31 (table 0): (a) table 0 of exh_991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2017 3Q 2017 4Q 2016 12M 2017 12M 2016'
- WOR 2019-05-31 (table 0): (a) table 0 of exh_991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2019 3Q 2019 4Q 2018 12M 2019 12M 2018'
- WOR 2022-05-31 (table 0): (a) table 0 of exh_991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2022 4Q 2021 After-Tax Per Share After-Tax Per Share'
- WOR 2023-05-31 (table 0): (a) table 0 of exh_991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4Q 2023 4Q 2022 After-Tax Per Share After-Tax Per Share'
- WOR 2026-05-31 (table 0): (a) table 0 of wor-ex99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header '(U.S. dollars in millions, except per share amounts) 4Q 2026 4Q 2025 GAAP Financial Measures'
- WRLD 2016-03-31 (table None): (d) a51335783_ex991.htm: strict match only in raw text, not in a table cell
- WRLD 2019-03-31 (table 0): (a) table 0 of a51982191ex99_1.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'WORLD ACCEPTANCE CORPORATION AND SUBSIDIARIES CONSOLIDATED STATEMENTS OF OPERATIONS (unaudited and in thousands, except '
- WRLD 2020-03-31 (table 5): (d) table 5 of a52257849ex99_1.htm: strict match only under label 'Deferred income taxes, net', which is not an accepted net income label
- WRLD 2023-03-31 (table None): (d) a53393134_ex991.htm: strict match only in raw text, not in a table cell
- WRLD 2025-03-31 (table None): (d) ex_808151.htm: strict match only in raw text, not in a table cell
- WRLD 2026-03-31 (table None): (d) ex_953445.htm: strict match only in raw text, not in a table cell
- WSM 2012-01-29 (table 26): (a) table 26 of d312373dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'FOURTH QUARTER 2011 2010 (13 Weeks) (13 Weeks) % of % of $ Revenues $ Revenues'
- WSM 2013-02-03 (table 25): (a) table 25 of d503606dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'FOURTH QUARTER 2012 2011 (14 Weeks) (13 Weeks) % of % of $ Revenues $ Revenues'
- WSM 2014-02-02 (table 21): (a) table 21 of d691119dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4th Quarter 2013 (13 Weeks) 2012 (14 Weeks) $ % ofRevenues $ % ofRevenues'
- WSM 2015-02-01 (table 17): (a) table 17 of d894119dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4th Quarter 2014 2013 $ % ofRevenues $ % ofRevenues'
- WSM 2016-01-31 (table 17): (a) table 17 of d160621dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4th Quarter 2015 2014 $ % ofRevenues $ % ofRevenues'
- WSM 2017-01-29 (table 17): (a) table 17 of d339255dex991.htm: accepted net income match with no period header in this table or the 3 tables before it; header '4th Quarter 2016 2015 $ % ofRevenues $ % ofRevenues'
- YEXT 2017-01-31 (table None): (d) ex991_q1fy18earningsreleas.htm: strict match only in raw text, not in a table cell
- ZM 2019-01-31 (table None): (d) zm-ex991_6.htm: strict match only in raw text, not in a table cell
- ZS 2019-07-31 (table None): (d) zs-10312019991.htm: strict match only in raw text, not in a table cell
- ZUMZ 2012-01-28 (table None): (d) zumiezinc.htm: strict match only in raw text, not in a table cell
- ZUMZ 2013-02-02 (table None): (d) zumiezinc.htm: strict match only in raw text, not in a table cell
- ZUMZ 2014-02-01 (table None): (d) zumiezinc.htm: strict match only in raw text, not in a table cell
- ZUMZ 2016-01-30 (table None): (d) zumiezinc.htm: strict match only in raw text, not in a table cell
- ZUMZ 2017-01-28 (table None): (d) zumiezinc.htm: strict match only in raw text, not in a table cell
- ZUMZ 2024-02-03 (table 0): (a) table 0 of exh_991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'ZUMIEZ INC.CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS(In thousands, except per share amounts)(Unaudited) Three Mont'
- ZUMZ 2025-02-01 (table 0): (a) table 0 of exh_991.htm: accepted net income match with no period header in this table or the 3 tables before it; header 'ZUMIEZ INC. CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (In thousands, except per share amounts) (Unaudited) Three M'

### Open decision for Opus

1. Is the L9 rise (92 rows; 36 in the Codex classification, 85 in the heuristic hint) a real other-class
   population, or a classifier gap (sub-case (d) dominates: 44 rows, sub-case (a) 39, (b) 9)?
2. Why is L4 empty (rule 9 from interpretation 1)?
3. Fixture TRMB 2011-12-30 (positive) failed on size ("too large"); decide whether to re-select.

### Exact next step

Opus decides (decision file `docs/handoff/auto-s1-decision-2.md`). If Opus says continue, the loop
resumes at the step it names. Nothing is merged from this report.
