"""clean_ohlcv_dataframe: weekend placeholder bars are dropped (Yahoo served a Sunday bar on 2026-09-20)."""
import os
import sys
import types
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def import_main():
    if "main" in sys.modules:
        return sys.modules["main"]
    sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
    import main  # noqa: WPS433

    return main


def frame(dates):
    n = len(dates)
    return pd.DataFrame(
        {"Open": [10.0] * n, "High": [11.0] * n, "Low": [9.0] * n, "Close": [10.5] * n, "Volume": [1000.0] * n},
        index=pd.to_datetime(dates),
    )


class CleanOhlcvWeekendTest(unittest.TestCase):
    def test_weekend_bars_are_dropped_and_weekdays_kept(self):
        main = import_main()
        # 2026-09-18 Fri, 09-19 Sat, 09-20 Sun, 09-21 Mon
        cleaned = main.clean_ohlcv_dataframe(frame(["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"]))
        self.assertEqual([d.strftime("%Y-%m-%d") for d in cleaned.index], ["2026-09-18", "2026-09-21"])

    def test_timezone_aware_index_keeps_its_local_dates(self):
        main = import_main()
        data = frame(["2026-09-18", "2026-09-20", "2026-09-21"])
        data.index = data.index.tz_localize("Asia/Taipei")
        cleaned = main.clean_ohlcv_dataframe(data)
        self.assertEqual([d.strftime("%Y-%m-%d") for d in cleaned.index], ["2026-09-18", "2026-09-21"])

    def test_only_weekend_bars_gives_an_empty_frame(self):
        main = import_main()
        self.assertTrue(main.clean_ohlcv_dataframe(frame(["2026-09-19", "2026-09-20"])).empty)


if __name__ == "__main__":
    unittest.main()
