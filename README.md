# Permanent full-market daily K-lines

This orphan branch is independent of main, generated and GitHub Pages.
Never delete symbols, squash history, force-push, or expire records.

Layout: tw/<YAHOO_SYMBOL>/<YEAR>.csv and us/<YAHOO_SYMBOL>/<YEAR>.csv.
Date,Open,High,Low,Close,Volume; Yahoo split-adjusted OHLC, NOT dividend-adjusted.
Prices have four decimals; volume is integer shares or empty when unknown.
Ordinary daily updates only append completed sessions accepted by universe-full.
Backfill inserts missing dates with audit records, never replaces disagreements.
Only a cache-proven history adjustment may correct existing dates; every change
has per-year old/new SHA-256 audits in docs/report/kline-corrections.json.
Dates are never removed.
state/ retains resumable backfill progress and deduplicated conflicts.
manifest.json reports the actual symbols, market date bounds, rows and byte size.

Run the verification/publishing tools from main: scripts/kline_archive.py and
scripts/kline_archive_git.py. See docs/KLINE_ARCHIVE.md on main for operations.

## ETFs and website indices (archive scope v1)

tw/ contains Taiwan stocks, catalog ETFs and ^TWII/^TWOII.
us/ contains US stocks, selected public ETFs and US indices.
global/ contains the other website indices, separate from US date statistics.
Directories retain canonical Yahoo symbols (including ^); HTTP paths encode each
segment, e.g. tw/%5ETWII/2021.csv. Never interpolate symbols in shell commands.
Per-symbol index JSON gives exact dates, yearly SHA-256, encoded path, kind and venue.
state/instruments.json retains every previously public ETF/index; leaving a list
does not delete its history. Backfill inserts missing dates only; existing rows win.
^TWOII uses the existing website TPEx cash-index OHLC source (Yahoo has no history);
cash indices have no split/dividend adjustment. Missing source volume stays blank.
See docs/KLINE_ARCHIVE.md on main for the scope rule and resumable workflow.

## Per-symbol indexes (v1)

manifest.json v3 contains small aggregate totals, not per-year bySymbol details.
index/{tw|us|global}/{URL-encoded-symbol}.json contains one symbol's metadata and
all annual rows/bytes/date bounds/SHA-256. Unchanged index files are not rewritten.
CSV directory names remain canonical Yahoo symbols; index filenames are percent
encoded on disk. Encode the filename again for HTTP (e.g. index/tw/%255ETWII.json).
Verification compares every index with validated CSVs and rejects missing/extra files.
