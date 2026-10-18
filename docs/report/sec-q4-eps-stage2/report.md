# SEC Q4 diluted EPS stage-two evidence test

Sample: sec-q4-eps-stage2-60x60-20261018 (seed `20261018`); sample freeze SHA-256 `cb20f5d471b4a41297ac6e51e97928c49de4bf17886612f50508e2f4fe35d622`.
Extraction SHA-256 `2ca20bc6c01b2eff64838c19742437878d42f0a8bc38b37b90fff09adf7f4ddb`; extraction commit `e3453d3796e9e2dc0746238312f577314e389176`.
Run complete: **True**; stop reason: `completed`.

## Missing-period yield by class

| Main class | Sampled periods | Not skipped (includes unresolved mapping) | Skipped mismatch | SEC 10-K end mapping unresolved (subset) | A: verified direct | B: candidate | C: derived only | D: no direct found | E: technical/incomplete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `derivation-rejected` | 28 | 28 | 0 | 1 | 0 | 1 | 10 | 13 | 4 |
| `no-annual-diluted-eps` | 28 | 28 | 0 | 2 | 0 | 1 | 14 | 10 | 3 |
| `no-q4-net-income` | 23 | 23 | 0 | 1 | 0 | 1 | 7 | 12 | 3 |
| `q1-q3-incomplete` | 20 | 20 | 0 | 6 | 0 | 1 | 8 | 4 | 7 |

## Missing-period yield by period-end year

| Period-end year | Sampled periods | Not skipped (includes unresolved mapping) | Skipped mismatch | SEC 10-K end mapping unresolved (subset) | A: verified direct | B: candidate | C: derived only | D: no direct found | E: technical/incomplete |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2012 | 5 | 5 | 0 | 1 | 0 | 0 | 0 | 4 | 1 |
| 2013 | 6 | 6 | 0 | 1 | 0 | 0 | 0 | 5 | 1 |
| 2014 | 6 | 6 | 0 | 1 | 0 | 0 | 0 | 5 | 1 |
| 2015 | 5 | 5 | 0 | 1 | 0 | 0 | 0 | 4 | 1 |
| 2016 | 5 | 5 | 0 | 1 | 0 | 0 | 0 | 3 | 2 |
| 2017 | 6 | 6 | 0 | 1 | 0 | 1 | 0 | 4 | 1 |
| 2018 | 12 | 12 | 0 | 0 | 0 | 1 | 1 | 8 | 2 |
| 2019 | 8 | 8 | 0 | 0 | 0 | 0 | 3 | 4 | 1 |
| 2020 | 4 | 4 | 0 | 1 | 0 | 0 | 0 | 1 | 3 |
| 2021 | 10 | 10 | 0 | 1 | 0 | 0 | 8 | 0 | 2 |
| 2022 | 10 | 10 | 0 | 1 | 0 | 0 | 9 | 0 | 1 |
| 2023 | 7 | 7 | 0 | 1 | 0 | 1 | 5 | 0 | 1 |
| 2024 | 7 | 7 | 0 | 0 | 0 | 0 | 6 | 1 | 0 |
| 2025 | 7 | 7 | 0 | 0 | 0 | 1 | 6 | 0 | 0 |
| 2026 | 1 | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |

Verified direct values: **0/99 sampled missing periods (0.0%)**. 0 were skipped for a confirmed fiscal-end mismatch; 10 of the 99 not skipped remain E because the same-FY SEC 10-K report end could not be mapped. These mapping-incomplete periods are not evidence of no disclosure.

## Control-set comparison

Controls selected: 60. SEC extraction accepted 13 direct values; 13 matched the answer set within ±0.005; 0 sign errors; 47 not comparable because the extraction was not verified direct, including 1 fiscal-end mismatch skip(s) and 3 unresolved SEC 10-K report-end mapping row(s).
Accuracy among accepted controls: 1.0.

## Source contribution

| SEC source | Unique missing periods with a verified direct value |
|---|---:|
| 8-K/8-K-A results exhibits (EX-99.1 preferred) | 0 |
| 10-K/10-K-A fallback | 0 |
| Cumulative unique periods | 0 |

Positive controls with a verified direct value by source: 8-K/8-K-A **0**; 10-K/10-K-A **13**.

## Fiscal-period mismatches skipped

Skipped **1** sampled periods before fetching EPS filing bodies because local Q4 end differs from the same-FY SEC 10-K report end.

| Sample | Ticker | Class | FY | Local Q4 end | SEC 10-K report end |
|---|---|---|---:|---|---|
| control | UA | control | FY2023 | 2023-12-31 | 2023-03-31 |

## Requests and bytes

SEC wire request attempts: **2697 / 12,000**; decoded bytes: **770822186**; wire bytes: **190548586**; cache hits: **0**; retries: **0**; configured limit: **4 requests/second**.
SEC Financial Statement Data Sets and Financial Statement and Notes ZIP datasets were not downloaded. No request was sent to S3 shards 4–7.

## Outcome definitions and limits

A means a direct, as-filed, single-quarter Q4 value passed the EPS tag/label, diluted, GAAP, period-end, share-class/scope, and split-basis checks. B means a candidate remains unresolved. C means only an annual or non-quarter EPS fact was found; no subtraction was performed. D means the scoped filings were successfully searched with no direct Q4 disclosure. E means a technical failure or incomplete search. Fiscal-end mismatches are separately excluded before source documents are fetched.

This is a fixed, stratified sample: 60 companies split 15 per missing class, with up to two periods per company, plus 60 answer-set controls from key-index shards 4–7. It is not a random sample of all 7,755 missing rows. Its yield must not be extrapolated to those rows or to the whole market.

The extraction does not read answer-set values. The control comparison was generated only after the extraction file and SHA-256 were committed.
