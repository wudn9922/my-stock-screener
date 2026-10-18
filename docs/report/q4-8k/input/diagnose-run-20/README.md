# 8-K Q4 EPS diagnose: run 20 (input for S1 classification)

Source: GitHub Actions run 39039050667 (run 20, `q4-8k-backfill.yml`, mode diagnose, dispatched on main at 14:02 UTC, budget 6000).

Output commit on `runs/q4-8k`: e30ff10788057af3a028b11c104150245412c3a9, path `docs/report/q4-8k/runs/39039050667/`.

Files on `runs/q4-8k` (SHA-256):
- `run-meta.json`: ea74e41a4f076040b333e916badb85dee238be4480aa8479bf414396ab7d0145
- `s1-classes.json`: 350503306acac809db827177c9c29ab9e70e086f35f6c649ebe00dc1cb94a53c
- `summary.json`: 142719fd74ffbd9aa39ba53b351bd807fda66703c76f6880977dc130b805b97b
- `verbose.json.gz`: 4e163577758aeea52bf7f5023cb741dec0705c7e6704029c773c72ac1fca6ded (stays on `runs/q4-8k` only, not copied here)
- `fixtures/`: 17 HTML files, copied byte-for-byte into `tests/fixtures/q4_8k/`. Their sha256 values are in the manifest `tests/fixtures/q4_8k/s1_fixtures.json`.

Counts:
- sourceRows 235, diagnosed 235, status complete, stopped null, SEC requests 2630, L9 13 (limit 23).
- classCounts: L1 13, L2 74, L3 7, L4 27, L5 8, L6 8, L7 0, L8 62, L9 13, L10 23.

Fixtures: 17 (12 positives: L1 3, L2 3, L3 3, L8 3; 5 negatives).

Fixture errors: 7.
- TRMB 2011-12-30: "too large".
- WSM 2012-01-29, 2013-02-03, 2014-02-02, 2015-02-01, 2016-01-31, 2017-01-29: "L2 variant rule vi is never an auto-positive (S1-CF C13)".

Source report: `docs/report/q4-8k/input/run-17/q4-8k-report.json`.

S1 output files on main:
- `docs/report/q4-8k/s1-no-table-layouts.json` (byte-identical to run 20's `s1-classes.json`).
- `tests/fixtures/q4_8k/` (17 fixtures plus `s1_fixtures.json`).
