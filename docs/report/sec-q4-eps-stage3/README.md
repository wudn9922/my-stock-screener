# SEC Q4 EPS stage-three evidence run

This package contains one frozen, read-only SEC evidence run. It uses the stage-two SEC filing selector and the fixed 8-K parser regression, reuses the existing stage-two SEC response cache, and records this run's own request attempts and SEC responses in `source-ledger.jsonl`.

The sample is frozen before SEC requests. The extraction JSON and SHA-256 are committed before `controls-comparison.json` is generated. The comparison command refuses to read answer-set values until those committed hashes match.

To reproduce the selection and verification checks locally without network access:

```sh
python -m unittest tests.test_sec_q4_eps_stage2_regression tests.test_sec_q4_eps_stage3
```

To inspect the frozen run artifacts, start with `report.md`. `sample.json` and `extraction.json` are checked by their `.sha256` sidecars; the controls comparison has a matching sidecar as well. The extraction is intentionally a single run and the runner refuses to start another stage-three SEC extraction after the first output or ledger has been created.
