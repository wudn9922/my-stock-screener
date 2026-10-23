# Offline public research corpus

Source: generated `c4fd63c39e542149607eac7cea4a47f74840be99`
(2026-10-23). These are frozen test records, not deployable outputs.

The institution and volume market files use the real range-addressable codec.
They retain two symbols per market plus 2330.TW and AAPL, three recent volume
features, two recent institution rows and the last negative foreign-flow row,
and two cost observations per window. The 2330.TW and AAPL browser regressions
retain 30 volume features to exercise the complete 5/20-day windows.
Index ranges, sizes and SHA-256 hashes
were rebuilt with `research_store.MarketStore.save`. Alerts retain one notice,
one disposal and the 3354.TWO regression cases; SEC retains three events and
three checked symbols plus AAPL. Chips retain the 2330.TW record with its source
axes. World source metadata is unchanged. No source values were fabricated.

`npm test` always uses this corpus. CI additionally runs the research contract
tests with `ATLAS_RESEARCH_DIR=../docs/report/research` after the existing
generated restore; that mode still validates every live shard and requires
more than 1,000 institutional symbols. Candle tests use all retained
`data/world/*.csv` histories without a restore and additionally compare the
live snapshots when this environment variable is set.

The browser research mocks share these excerpts. Their candle packets are built
from retained `data/world/*.csv`; theme drawings use the existing theme fixtures.
The separate real Pages smoke check still reads restored production inputs.
