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
