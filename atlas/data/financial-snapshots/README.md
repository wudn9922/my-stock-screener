# Local SEC financial snapshot bootstrap

Captured byte-identical local copies at `2026-10-08T10:41:17Z` from `public/financial-data/<symbol>.json` for the 16 issuers listed below. These are normalized historical SEC companyfacts packs from the last locally successful collection. They are not live data and were not produced by the latest GitHub Actions run.

## Collection and freshness status

- The latest reported GitHub Actions run was `37764054644` and the workflow completed with overall `SUCCESS`, but SEC requests returned HTTP 403 for all 20 attempted symbols and that run produced no new SEC packs. The files here are the prior local bootstrap, not new output from that run.
- Original `fetchedAt` and `generatedAt` values are preserved exactly. The oldest pack was generated at `2026-10-08 09:19:15Z` and was about 1h 22m old at capture; every pack was inside the collector's 24-hour freshness window then. The app marks packs stale once `generatedAt` is at least 24 hours old.
- A successful local refresh fetched real SEC companyfacts and normalized the supported facts. These files retain every value, null, accession, form, unit, and provenance field unchanged. No timestamps were reset, no pack was re-normalized, and no manifest or unsupported/Taiwan data is included.

## Validation and source provenance

- All 16 packs passed the project `financialSnapshotSchema` (version 1) validation.
- The symbol, CIK, issuer identity, record identities, dates, and metric provenance passed schema checks. Each top-level `source` exactly matches `https://data.sec.gov/api/xbrl/companyfacts/CIK<CIK>.json`; every metric `secUrl` matches that source, and filing URLs point to `https://www.sec.gov/Archives/edgar/data/...`.
- The refresh collector resolves symbols through the official SEC ticker map and validates the returned companyfacts CIK against the ticker-map CIK before it writes a pack. This bootstrap copy performs no new live SEC lookup; the 403 status above is recorded rather than represented as a current refresh.
- `XOM` is the current official mapping to CIK `0002115436` (`ExxonMobil Holdings Corp`), not the historical Exxon Mobil Corporation CIK `0000034088`. This pack has two quarterly records and no annual records; only 10-Q is present.
- SHA-256 below is computed from each copied file and matches its original `public/financial-data` bytes.

## Issuer records

| Symbol | SEC issuer name | CIK | Fetched at (UTC) | Generated at (UTC) | Quarterly / annual records | Record heading forms | Official companyfacts source | Bytes | SHA-256 |
|---|---|---:|---|---|---:|---|---|---:|---|
| AAPL | Apple Inc. | `0000320193` | 2026-10-08 09:19:14Z (`1791451154`) | 2026-10-08 09:19:15Z (`1791451155`) | 73 / 19 | 10-K, 10-K/A, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json) | 914970 | `7ee9dcb7bd7627d628052164c30f534105e869b392db7924619cf57f5ae35586` |
| MSFT | MICROSOFT CORPORATION | `0000789019` | 2026-10-08 09:19:16Z (`1791451156`) | 2026-10-08 09:19:17Z (`1791451157`) | 76 / 19 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json) | 800240 | `cebea9389fbd60525ad2089351a0539d53b44c4f3729d2289a78fe32555c07ed` |
| NVDA | NVIDIA CORP | `0001045810` | 2026-10-08 09:19:17Z (`1791451157`) | 2026-10-08 09:19:17Z (`1791451157`) | 77 / 19 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json) | 841420 | `0ea52aa3bfe3567ef89b58931d0b8bbf528cac79509906c92d09d6cbe395f5f9` |
| TSLA | Tesla, Inc. | `0001318605` | 2026-10-08 09:19:18Z (`1791451158`) | 2026-10-08 09:19:18Z (`1791451158`) | 66 / 17 | 10-K, 10-K/A, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json) | 794972 | `ecd4ce755de62dda249553befc5f5a16fa85512a950f6a2ed7255ef6d6702884` |
| AMD | ADVANCED MICRO DEVICES INC | `0000002488` | 2026-10-08 09:19:18Z (`1791451158`) | 2026-10-08 09:19:18Z (`1791451158`) | 69 / 18 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0000002488.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0000002488.json) | 772759 | `46aa3a9473720bd8486184e61655fe93ae9d7228db0f9391f6dbbf7eec535509` |
| META | Meta Platforms, Inc. | `0001326801` | 2026-10-08 09:19:19Z (`1791451159`) | 2026-10-08 09:19:19Z (`1791451159`) | 61 / 16 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001326801.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001326801.json) | 783702 | `cac625aa620f07467008ee934a06781b513c76e08e1828dbd855dd84b3545f47` |
| GOOGL | Alphabet Inc. | `0001652044` | 2026-10-08 09:19:19Z (`1791451159`) | 2026-10-08 09:19:19Z (`1791451159`) | 48 / 13 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json) | 600213 | `a9c9077b7f40879e3597978767a8481df983441b4a7ce54b2b43e709f749232e` |
| AMZN | AMAZON COM INC | `0001018724` | 2026-10-08 09:19:20Z (`1791451160`) | 2026-10-08 09:19:20Z (`1791451160`) | 73 / 19 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001018724.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001018724.json) | 800993 | `4457a426aeb1fe9efee0225a18038b63d060bdf6b5ecfcf82cba56cf65efe00c` |
| SMCI | Super Micro Computer, Inc. | `0001375365` | 2026-10-08 09:19:21Z (`1791451161`) | 2026-10-08 09:19:21Z (`1791451161`) | 64 / 17 | 10-K, 10-K/A, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001375365.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001375365.json) | 809509 | `f9feea2a2ff2b25b05a817576385785d5bd4d34c7e64114d58ff96faae6ec831` |
| NFLX | NETFLIX INC | `0001065280` | 2026-10-08 09:19:22Z (`1791451162`) | 2026-10-08 09:19:22Z (`1791451162`) | 73 / 19 | 10-K, 10-K/A, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001065280.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001065280.json) | 809383 | `c44704b650ba9748a808e241e4cbe57c55eb22c4c219e2872039a530cb6ab7a7` |
| JPM | JPMORGAN CHASE & CO | `0000019617` | 2026-10-08 09:19:22Z (`1791451162`) | 2026-10-08 09:19:22Z (`1791451162`) | 73 / 19 | 10-K, 10-Q, 10-Q/A | [https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json) | 427042 | `76723d28c31901694afc878398e0869ea6ac3be2adc151f46456d6d71ccd3ecd` |
| WMT | WALMART INC. | `0000104169` | 2026-10-08 09:19:22Z (`1791451162`) | 2026-10-08 09:19:22Z (`1791451162`) | 77 / 20 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0000104169.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0000104169.json) | 927555 | `a64f170459fde83c5d72aac6d791f71bdbd4f6a6503eb127d841c227cfd8223a` |
| XOM | ExxonMobil Holdings Corp | `0002115436` | 2026-10-08 09:19:23Z (`1791451163`) | 2026-10-08 09:19:23Z (`1791451163`) | 2 / 0 | 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0002115436.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0002115436.json) | 7485 | `18669a218cd3f1a057f1446ff33a2f375ba03f5efdd38e727adb25abefc615fe` |
| AVGO | Broadcom Inc. | `0001730168` | 2026-10-08 09:19:24Z (`1791451164`) | 2026-10-08 09:19:24Z (`1791451164`) | 39 / 10 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001730168.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001730168.json) | 476381 | `338407c8612d081240ed96daa332b686b822e9132af3b484a826810dad2bc058` |
| PLTR | Palantir Technologies Inc. | `0001321655` | 2026-10-08 09:19:24Z (`1791451164`) | 2026-10-08 09:19:24Z (`1791451164`) | 28 / 8 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0001321655.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0001321655.json) | 358352 | `e7b34a7af5e2d45c02906f3058ffb525a9d9db08b4d772cbdc8eaac672fe58ce` |
| MU | Micron Technology, Inc. | `0000723125` | 2026-10-08 09:19:25Z (`1791451165`) | 2026-10-08 09:19:25Z (`1791451165`) | 68 / 17 | 10-K, 10-Q | [https://data.sec.gov/api/xbrl/companyfacts/CIK0000723125.json](https://data.sec.gov/api/xbrl/companyfacts/CIK0000723125.json) | 792977 | `64045392019dc62511044e2e2bfc0ce3801c80c1743b1458a4052fbc81c0e5d1` |

“Record heading forms” lists the representative filing form used for normalized records. Metric audit inputs retain their own filing forms, including 10-Q/A amendments where present.

## Future refresh

From the repository root, the collector can request this exact supported set with:

```sh
node scripts/refresh-fundamentals.mjs AAPL MSFT NVDA TSLA AMD META GOOGL AMZN SMCI NFLX JPM WMT XOM AVGO PLTR MU
```

The collector writes to `public/financial-data/`; it validates the official ticker-to-CIK identity, retains the existing pack when a request fails, and does not replace a valid pack with an error response. It skips SEC requests for packs generated within the preceding 24 hours, so a run during that window can reuse existing data. After a successful refresh, copy the validated per-symbol files byte-for-byte into this directory and regenerate this SHA-256 table. Confirm `fetchedAt` advanced before describing a pack as refreshed. If SEC continues to return 403, keep the existing timestamps and report the packs as stale when their 24-hour threshold is reached.
