# Financial history audit and backfill status

The root report files describe the checked-in financial JSON as of this run. `baseline/` preserves the same inventory against `52a95f27b654db54b9a544082b4eb0625c7ed5de`. Period coverage uses each file's fiscal `fy`/`fq` labels; it does not remap fiscal years to calendar years.

The core completeness measure requires numeric Revenue, Net Income, Total Assets, Stockholders' Equity, and Operating Cash Flow values in every target period. Nulls remain unfilled. Legacy rows do not have filing dates, accepted timestamps, accession numbers, or revision vintages, so they are not certified for point-in-time backtests. Listing dates are absent, so tenure-adjusted eligibility remains unknown.

## Re-run the reports

```sh
python scripts/financial_history_audit.py \
  --out reports/financial-backfill \
  --github-repo-size-kb 322540 \
  --sec-retry-manifest reports/financial-backfill/sec-retry-batch-14.json \
  --compare-revision 52a95f27b654db54b9a544082b4eb0625c7ed5de
```

The script reads the financial index, market directory, scanner universe, current SEC queue, and per-company JSON files. It writes coverage, field-completeness, missing-period, quality, provenance, capacity, and HTML outputs. It does not update financial company files.
The period counters treat duplicate fiscal keys as invalid rather than counting the duplicate rows twice. Data-delta comparisons reject an invalid base revision and account for added, changed, and deleted financial files. Source checks are tied to the financial-file hash and current SEC CIK mapping; stale checks are excluded and reported as P1.

The compact retry manifest records the final 66-issuer SEC retry (63 metadata-unresolved, two SEC identity/schema failures, one issuer without US-GAAP facts). That batch made no financial-file changes; its full retry state is retained separately so a very large one-line queue snapshot does not need to be rewritten for audit reporting.

`source-crosschecks.csv` was produced by comparing SEC-labelled stored values to the current SEC Company Facts response using the existing SEC extractor:

```sh
python scripts/verify_financial_history_sources.py --limit 33
```

Use `--symbols XOM --cik-overrides XOM=0000034088 --append` to check a historical CIK when investigating issuer identity. SEC responses use the existing rate-limited client; no raw responses are committed.

## Resume the U.S. SEC queue

The existing `atlas/scripts/backfill_financials_us_market.py` persists the frozen listing cohort and per-issuer checkpoint under `docs/report/fundamentals/`. Run it locally with `--resume --batch-size 500`; use `--retry` only for prior unresolved metadata/failure states. It refuses to run in CI and keeps the existing SEC extraction, split, merge, and staging rules. Its progress log is separate from the 10-year completeness report.

## Source blocks

The current Taiwan fundamentals workflow contains recent growth metrics, not ten-year, three-statement company history. MOPS historical redirects returned result URLs, but the result fetch returned upstream HTTP 503 for the tested statements. TWSE OpenAPI is current-season only; TPEx reuse terms and MOPS historical redistribution terms were not verified. No Taiwan source values were added. The per-company and market reports continue to list these gaps as incomplete.

Do not use `quality.good`, a JSON file's existence, or a small number of recent quarters as a ten-year completion signal. Investigate every P0/P1 row before using or merging source data.
