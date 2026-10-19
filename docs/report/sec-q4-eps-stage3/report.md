# SEC Q4 diluted EPS stage-three evidence run

Frozen sample `sec-q4-eps-stage3-60x60-20261018` used new seed `20261018-stage3`. Sample SHA-256 `e87eca23e4755b4975f0e61d37f86a4144b2a5368a8d31ffbb9e0b2c065d7844`; extraction SHA-256 `db26ef309297e71778ecbe25752af307bd6681b3f29a9b2bb2bdc58e24c37b69`.
Sample freeze commit `5f0dc27fffe47a79fbcd1bd4ef278e707eb7143b`; extraction freeze commit `601ef120910a64ca0b6cdbea9b37ba09aba20fe0`; comparison base commit `601ef120910a64ca0b6cdbea9b37ba09aba20fe0`.
Regression fix commit `8ead98cfd9428b1c429a73138218aa396ff65d28`; main base `37d90cc97b205add6232965065d94cd691648ae5`.
One SEC extraction run: complete **True**, stop reason `completed`.
The run completed its query plan, with status E (technical failure/incomplete) for **21 missing periods** and **8 controls**; a completed run does not mean every period was resolved.

## Missing-period yield by classification

| Main class | Sampled periods | A: verified direct | B: candidate | C: annual/non-quarter only | D: no direct found | E: incomplete | Skipped fiscal-end mismatch |
|---|---:|---:|---:|---:|---:|---:|---:|
| `no-q4-net-income` | 20 | 1 | 4 | 4 | 9 | 1 | 1 |
| `no-annual-diluted-eps` | 27 | 0 | 5 | 10 | 6 | 6 | 0 |
| `q1-q3-incomplete` | 19 | 0 | 0 | 4 | 4 | 11 | 0 |
| `derivation-rejected` | 28 | 0 | 4 | 8 | 13 | 3 | 0 |

## Missing-period yield by period-end year

| Period-end year | Sampled periods | A: verified direct | B: candidate | C: annual/non-quarter only | D: no direct found | E: incomplete | Skipped fiscal-end mismatch |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2012 | 9 | 0 | 1 | 0 | 5 | 3 | 0 |
| 2013 | 7 | 0 | 1 | 0 | 4 | 2 | 0 |
| 2014 | 7 | 0 | 0 | 0 | 6 | 1 | 0 |
| 2015 | 6 | 0 | 0 | 0 | 3 | 2 | 1 |
| 2016 | 5 | 0 | 2 | 0 | 1 | 2 | 0 |
| 2017 | 5 | 0 | 1 | 0 | 4 | 0 | 0 |
| 2018 | 5 | 1 | 1 | 0 | 2 | 1 | 0 |
| 2019 | 8 | 0 | 0 | 1 | 3 | 4 | 0 |
| 2020 | 9 | 0 | 1 | 2 | 3 | 3 | 0 |
| 2021 | 6 | 0 | 0 | 5 | 0 | 1 | 0 |
| 2022 | 7 | 0 | 1 | 5 | 0 | 1 | 0 |
| 2023 | 7 | 0 | 3 | 3 | 0 | 1 | 0 |
| 2024 | 5 | 0 | 1 | 4 | 0 | 0 | 0 |
| 2025 | 7 | 0 | 1 | 5 | 1 | 0 | 0 |
| 2026 | 1 | 0 | 0 | 1 | 0 | 0 | 0 |

Verified direct Q4 EPS: **1/94 sampled missing periods (1.1%)**; yield bar ≥20%: **FAIL**. The denominator includes periods skipped for a fiscal-end mismatch.

### Verified missing-period value

| Ticker | Fiscal period | EPS as filed | Source | SEC accession / URL | Filing date | Original row label | GAAP / diluted / period / scope evidence | Split basis |
|---|---|---:|---|---|---|---|---|---|
| RBC | FY2018 / 2018-03-31 | 1.09 | 8-K/8-K-A | [0001615774-18-004475](https://www.sec.gov/Archives/edgar/data/1324948/000161577418004475/s110456_ex99-1.htm) | 2018-05-30 | Net income per common share: Diluted | GAAP: GAAP or consolidated statement label in the table's source context; diluted=True (row label); period: Q4 single-quarter duration; consolidated statement/table heading; common share stated in the EPS row | as-reported in the cited SEC filing |

Unique missing-period values by source: 8-K/8-K-A **1**; 10-K/10-K-A **0**. Accepted control values by source: 8-K/8-K-A **9**; 10-K/10-K-A **16**.

## Control accuracy and acceptance bar

Controls accepted: **25/60**; correct within ±0.005: **24**; incorrect: **1**; sign errors: **0**; unverifiable: **35**; accuracy among accepted: `0.96`.
Control bar (≥50 accepted, every accepted value correct, zero sign errors): **FAIL**.
Controls were compared only after the extraction JSON and its SHA-256 were committed. No extraction rule was changed after the comparison.

### Control mismatches

| Ticker | Fiscal period | SEC value as filed / comparison basis | Answer-set value | Absolute difference | Source accessions |
|---|---|---|---:|---:|---|
| CTSO | FY2021 / 2021-12-31 | -0.21 as filed; current-basis comparator -4.2 | -0.25 | 3.95 | 0001104659-22-031614 vs 0001410578-24-000201 |

CTSO is the sole mismatch on the frozen split-normalized comparison basis: its 2022 8-K value is -0.21 as filed; the recorded future split factor is 0.05, which the frozen comparator converts to -4.20, while the answer set has -0.25. The cause of that difference remains unresolved. It is counted as incorrect, with no sign error. The comparison logic was not changed after this result.

## Fiscal-period mismatches skipped

Skipped **3** sampled periods because the local Q4 end differs from the same-FY SEC 10-K report end.

| Sample role | Ticker | Class | FY | Local Q4 end | SEC 10-K report end |
|---|---|---|---:|---|---|
| missing | JCI | no-q4-net-income | FY2015 | 2015-09-30 | 2015-09-25 |
| control | SFD | control | FY2011 | 2012-01-29 | 2011-05-01 |
| control | RLMD | control | FY2015 | 2015-12-31 | 2015-06-30 |

## SEC requests, bytes, and controls

Wire request attempts: **2913 / 12,000**; decoded network-response bytes: **645368484**; raw wire bytes received: **192986227**; decoded cached-body bytes examined: **35337146**; decoded response bytes examined total: **680705630**; cache hits: **153**; retries: **0**.
Identified User-Agent: `SecSeed w556087587@gmail.com`; request pacing cap: 4/second; first 403 stop enabled. The 12,000 cap and the per-run ledger are specific to this stage-three run.

The sample contains 60 unique missing-period companies (15 per main classification class), up to two periods each, and 60 controls from sorted-key index shards 4–7. Every ticker in the prior stage-two control sample was excluded.
Financial Statement Data Sets ZIPs were not downloaded. No answer-set value was used during extraction. No subtraction of annual EPS minus Q1–Q3 was performed.

## Final acceptance

Stage-three acceptance: **FAIL**. A failed bar is reported as observed; sample membership, extraction rules, and control thresholds were not changed after results.
