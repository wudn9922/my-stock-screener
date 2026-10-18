# SEC Q4 diluted EPS stage-two test

This is a read-only evidence test. It uses the frozen local sample, SEC submissions metadata, selected 8-K earnings-release exhibits, and same-fiscal-year 10-K/10-K/A filings. It does not fetch FSDS ZIP datasets or write production financials.

## Freeze and extraction order

`sample.json` and `sample.sha256` must be committed before any SEC request. The extractor enforces this by comparing the working files with `HEAD` before opening the SEC client.

After that commit, run:

```sh
python docs/report/sec-q4-eps-stage2/stage2_poc.py extract
sha256sum docs/report/sec-q4-eps-stage2/extraction.json > docs/report/sec-q4-eps-stage2/extraction.sha256
git add docs/report/sec-q4-eps-stage2/extraction.json docs/report/sec-q4-eps-stage2/extraction.sha256 docs/report/sec-q4-eps-stage2/source-ledger.jsonl
git commit -m "Freeze SEC stage-two Q4 EPS extraction"
```

Only after that extraction commit, run:

```sh
python docs/report/sec-q4-eps-stage2/compare_controls.py
python docs/report/sec-q4-eps-stage2/stage2_poc.py render-report
```

The comparison program checks that the extraction and its SHA-256 sidecar are committed and match before it reads answer-set values.

## Reproduce offline tests

```sh
python -m unittest discover -s docs/report/sec-q4-eps-stage2/tests -p 'test_*.py'
```

All tests use local fixtures and make no network requests. SEC transport is limited to four requests per second, persists every request attempt, stops on its first 403, and refuses to exceed 12,000 cumulative attempts recorded in the request ledger.
