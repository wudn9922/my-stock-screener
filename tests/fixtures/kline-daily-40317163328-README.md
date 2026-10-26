# Real daily artifact regression fixture

`kline-daily-40317163328-subset.zip` is a 12-symbol, public-market-data subset of
artifact **12647897306**, `kline-daily-40317163328`, downloaded from universe-full
run **40317163328** on 2026-10-26. Original ZIP SHA-256:
`35062aad55779c661287de51a4a961c0ece2077b17f7b899bc0747aa1c52c19a`.

Archive CSVs were copied byte-for-byte from a depth-1 checkout of the live
`kline-archive` commit `4c934296ac3bb1003d0d4a7f106d21a099cddb87`.
Payload CSVs (including the repeated baseline) were also copied byte-for-byte;
only the operation lists were reduced to these symbols. Their SHA-256 receipts
are unchanged. `provenance.json` includes the symbol list and expected comparison.
No generated/synthetic prices, credentials, or user data are included.

The subset includes Taiwan volume-only differences, Taiwan OHLC differences,
large volume differences, US small and split-size differences, a new symbol in
each market, and retained historical holes. It has 1,027 incoming rows after
identical-operation deduplication: 71 disagreements, 646 appendable rows, and
310 old missing rows which the daily importer must leave for explicit backfill.

The offline test uses the retained canonical rows and the actual baseline/final
transport shape, verifies every prior row survives unchanged, and repeats the
payload to check byte-identical conflict summaries and CSVs. The compressed fixture
remains usable after GitHub's seven-day artifact retention expires.
