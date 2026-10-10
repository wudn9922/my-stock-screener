"""F5 市場寬度（scripts/build_breadth.py）的離線測試：合成 CSV，不連網路、不 import yfinance。"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_breadth as bb  # noqa: E402


def weekdays_ending(last, count):
    """`count` 個平日，最後一個是 last（YYYY-MM-DD），由舊到新排序。"""
    day = date.fromisoformat(last)
    out = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return list(reversed(out))


def make_frame(closes, last="2026-10-09", volumes=None):
    """與 data/*.csv 同欄位的 DataFrame（index 為 Date）。"""
    dates = pd.DatetimeIndex(weekdays_ending(last, len(closes)))
    return pd.DataFrame(
        {
            "Close": [float(c) for c in closes],
            "Volume": list(volumes) if volumes is not None else [1000] * len(closes),
        },
        index=dates,
    )


def write_csv(directory, name, closes, last="2026-10-09", volumes=None):
    """寫出與 main.py 相同格式的日 K CSV（Date,Open,High,Low,Close,Volume）。"""
    dates = weekdays_ending(last, len(closes))
    volumes = list(volumes) if volumes is not None else [1000] * len(closes)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as file:
        file.write("Date,Open,High,Low,Close,Volume\n")
        for day, close, volume in zip(dates, closes, volumes):
            file.write(f"{day.isoformat()},{close},{close},{close},{close},{volume}\n")
    return path


def naive_market(frames, last_n=120):
    """逐日、逐檔重算的參考實作（與向量化版本獨立）。回傳 (asOf, dates, {n: [pct...]})。"""
    last_dates = [frame.index.max() for frame in frames.values()]
    counts = {}
    for day in last_dates:
        counts[day] = counts.get(day, 0) + 1
    as_of = max(counts.items(), key=lambda item: (item[1], item[0]))[0]

    all_dates = sorted({day for frame in frames.values() for day in frame.index if day <= as_of})
    kept = []
    for day in all_dates:
        present = sum(
            1
            for frame in frames.values()
            if day in frame.index and frame.loc[day, "Volume"] > 0
        )
        if present / len(frames) >= 0.5:
            kept.append(day)
    kept = kept[-last_n:]

    pcts = {20: [], 60: []}
    for day in kept:
        for period in (20, 60):
            above = total = 0
            for frame in frames.values():
                if day not in frame.index or frame.loc[day, "Volume"] <= 0:
                    continue
                bars = frame.loc[:day, "Close"]
                if len(bars) < period:
                    continue
                total += 1
                if bars.iloc[-1] > bars.iloc[-period:].mean():
                    above += 1
            pcts[period].append(round(above / total * 100, 1) if total else None)
    return as_of, [d.strftime("%Y-%m-%d") for d in kept], pcts


class SelectSymbolsTest(unittest.TestCase):
    def test_tw_keeps_four_digit_listed_and_otc_codes(self):
        names = ["2330.TW.csv", "6488.TWO.csv", "1101.TW.csv", "9105.TW.csv"]
        self.assertEqual(bb.select_tw_symbols(names), ["1101.TW", "2330.TW", "6488.TWO", "9105.TW"])

    def test_tw_excludes_zero_prefixed_etfs_indices_and_other_files(self):
        names = [
            "0050.TW.csv",
            "00632R.TW.csv",
            "^TWII.csv",
            "^TWOII.csv",
            "AAPL.csv",
            "23301.TW.csv",
            "2330.TW.csv.bak",
            "2330.TW.csv.tmp",
        ]
        self.assertEqual(bb.select_tw_symbols(names), [])

    def test_us_fallback_is_every_non_tw_non_index_csv(self):
        names = ["AAPL.csv", "BRK-B.csv", "0050.TW.csv", "2330.TW.csv", "^TWII.csv", "6488.TWO.csv", "notes.txt"]
        symbols, label = bb.select_us_symbols(names, None)
        self.assertEqual(symbols, ["AAPL", "BRK-B"])
        self.assertEqual(label, "S&P 500＋自選美股")

    def test_us_intersects_with_sp500_when_available(self):
        names = ["AAPL.csv", "MSFT.csv", "ZZZZ.csv", "0050.TW.csv"]
        symbols, label = bb.select_us_symbols(names, {"AAPL", "MSFT", "NVDA"})
        self.assertEqual(symbols, ["AAPL", "MSFT"])
        self.assertEqual(label, "S&P 500 成分股")


class LoadDailyBarsTest(unittest.TestCase):
    def test_reads_pipeline_csv_sorted_and_drops_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "2330.TW.csv")
            with open(path, "w", encoding="utf-8") as file:
                file.write("Date,Open,High,Low,Close,Volume\n")
                file.write("2026-10-08,1,1,1,101,500\n")
                file.write("2026-10-07,1,1,1,100,400\n")
                file.write("2026-10-07,1,1,1,100.5,450\n")  # 同一天重複：保留最後一筆
                file.write("not-a-date,1,1,1,103,600\n")  # 壞日期：丟棄
                file.write("2026-10-09,1,1,1,,700\n")  # 沒有收盤價：丟棄
            bars = bb.load_daily_bars(path)
        self.assertIsNotNone(bars)
        self.assertEqual([d.strftime("%Y-%m-%d") for d in bars.index], ["2026-10-07", "2026-10-08"])
        self.assertEqual(bars["Close"].tolist(), [100.5, 101.0])
        self.assertEqual(bars["Volume"].tolist(), [450, 500])

    def test_missing_columns_or_unreadable_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "2330.TW.csv")
            with open(path, "w", encoding="utf-8") as file:
                file.write("Date,Open\n2026-10-08,1\n")
            self.assertIsNone(bb.load_daily_bars(path))
            self.assertIsNone(bb.load_daily_bars(os.path.join(tmp, "missing.csv")))


class SnapshotTest(unittest.TestCase):
    def test_strict_comparison_and_pct_rounding(self):
        frames = {
            "A.TW": make_frame([100] * 19 + [101]),  # SMA20 = 100.05，收盤 101 > 均線
            "B.TW": make_frame([100] * 20),  # 收盤 == 均線：不算站上（嚴格大於）
            "C.TW": make_frame([100] * 19 + [99]),  # 收盤 99 < 均線
        }
        market = bb.build_market(frames, "test")
        self.assertEqual(market["asOf"], "2026-10-09")
        all_segment = market["segments"]["all"]
        self.assertEqual(all_segment["count"], 3)
        self.assertEqual(all_segment["ma20"], {"above": 1, "total": 3, "pct": 33.3})
        # 只有 20 根K，不足 60 根：MA60 沒有任何可計算的股票
        self.assertEqual(all_segment["ma60"], {"above": 0, "total": 0, "pct": None})

    def test_short_history_counts_as_present_but_not_in_that_ma_total(self):
        frames = {
            "LONG.TW": make_frame([100] * 59 + [110]),  # 60 根：MA20、MA60 都可算，且站上
            "SHORT.TW": make_frame([100] * 19),  # 19 根：只出現在 count，不進任何 MA 分母
        }
        segment = bb.build_market(frames, "test")["segments"]["all"]
        self.assertEqual(segment["count"], 2)
        self.assertEqual(segment["ma20"], {"above": 1, "total": 1, "pct": 100.0})
        self.assertEqual(segment["ma60"], {"above": 1, "total": 1, "pct": 100.0})

    def test_zero_volume_and_stale_symbols_are_left_out(self):
        frames = {
            "A.TW": make_frame([100] * 20),
            "B.TW": make_frame([100] * 20, volumes=[1000] * 19 + [0]),  # 當日量為 0
            "C.TW": make_frame([100] * 20),
            "D.TW": make_frame([100] * 20, last="2026-10-06"),  # 最後一根早於 asOf
        }
        market = bb.build_market(frames, "test")
        self.assertEqual(market["asOf"], "2026-10-09")
        self.assertEqual(market["segments"]["all"]["count"], 2)
        self.assertEqual(market["segments"]["all"]["ma20"]["total"], 2)

    def test_as_of_is_the_most_common_last_date_not_the_latest(self):
        frames = {
            "A.TW": make_frame([100] * 20),
            "B.TW": make_frame([100] * 20),
            "C.TW": make_frame([100] * 20),
            "D.TW": make_frame([100] * 20, last="2026-10-12"),  # 異常的未來一根
        }
        market = bb.build_market(frames, "test")
        self.assertEqual(market["asOf"], "2026-10-09")
        self.assertEqual(market["segments"]["all"]["count"], 4)

    def test_all_short_history_gives_null_pct(self):
        frames = {"A.TW": make_frame([100] * 5), "B.TW": make_frame([100] * 5)}
        segment = bb.build_market(frames, "test")["segments"]["all"]
        self.assertEqual(segment["count"], 2)
        self.assertEqual(segment["ma20"], {"above": 0, "total": 0, "pct": None})

    def test_listing_segments_split_twse_and_tpex(self):
        frames = {
            "1101.TW": make_frame([100] * 19 + [101]),  # 上市，站上 MA20
            "2330.TW": make_frame([100] * 20),  # 上市，未站上
            "6488.TWO": make_frame([100] * 19 + [101]),  # 上櫃，站上 MA20
        }
        segment_of = {"1101.TW": "twse", "2330.TW": "twse", "6488.TWO": "tpex"}
        market = bb.build_market(frames, "test", segment_of)
        self.assertEqual(market["segments"]["all"]["ma20"], {"above": 2, "total": 3, "pct": 66.7})
        self.assertEqual(market["segments"]["twse"]["count"], 2)
        self.assertEqual(market["segments"]["twse"]["ma20"], {"above": 1, "total": 2, "pct": 50.0})
        self.assertEqual(market["segments"]["tpex"]["ma20"], {"above": 1, "total": 1, "pct": 100.0})

    def test_empty_otc_segment_is_zero_not_missing(self):
        frames = {"1101.TW": make_frame([100] * 20)}
        market = bb.build_market(frames, "test", {"1101.TW": "twse"})
        self.assertEqual(market["segments"]["tpex"]["count"], 0)
        self.assertIsNone(market["segments"]["tpex"]["ma20"]["pct"])

    def test_empty_frames_produce_no_market(self):
        payload = bb.build_breadth_payload({"tw": {"frames": {}, "universe": "x"}}, "2026-10-10T00:00:00+00:00")
        self.assertEqual(payload["markets"], {})
        self.assertEqual(payload["version"], 1)


class HistoryTest(unittest.TestCase):
    def test_keeps_only_dates_with_at_least_half_the_universe(self):
        frames = {
            "A.TW": make_frame([100] * 10, last="2026-10-09"),
            "B.TW": make_frame([100] * 10, last="2026-10-09"),
            "C.TW": make_frame([100] * 10, last="2026-10-09"),
            "D.TW": make_frame([100] * 10, last="2026-10-09"),
        }
        # 10-07 只剩 A 一檔有資料（1/4 < 50%）；10-08 有 A、B 兩檔（2/4 = 50%，保留）
        bad_day = pd.Timestamp("2026-10-07")
        good_day = pd.Timestamp("2026-10-08")
        for symbol in ("B.TW", "C.TW", "D.TW"):
            frames[symbol] = frames[symbol].drop(index=bad_day)
        for symbol in ("C.TW", "D.TW"):
            frames[symbol] = frames[symbol].drop(index=good_day)
        history = bb.build_market(frames, "test")["history"]
        self.assertNotIn("2026-10-07", history["dates"])
        self.assertIn("2026-10-08", history["dates"])
        self.assertEqual(len(history["ma20Pct"]), len(history["dates"]))
        self.assertEqual(len(history["ma60Pct"]), len(history["dates"]))

    def test_capped_at_120_dates_and_ends_on_as_of(self):
        frames = {f"S{s}.TW": make_frame([100 + s + b * 0.01 for b in range(130)]) for s in range(3)}
        history = bb.build_market(frames, "test")["history"]
        self.assertEqual(len(history["dates"]), 120)
        self.assertEqual(history["dates"][-1], "2026-10-09")
        self.assertEqual(history["dates"], sorted(history["dates"]))

    def test_matches_a_naive_recalculation_day_by_day(self):
        frames = {}
        for k in range(4):
            closes = [100 + 10 * math.sin(i * 0.37 + k) + i * 0.1 for i in range(140)]
            volumes = [0 if (i % 17 == 0 and k == 1) else 1000 + i for i in range(140)]
            frames[f"S{k}.TW"] = make_frame(closes, volumes=volumes)
        # 第 5 檔較晚上市：只有最後 50 根
        frames["LATE.TW"] = make_frame([100 + i * 0.2 for i in range(50)])
        as_of, dates, pcts = naive_market(frames)
        market = bb.build_market(frames, "test")
        self.assertEqual(market["asOf"], as_of.strftime("%Y-%m-%d"))
        self.assertEqual(market["history"]["dates"], dates)
        self.assertEqual(market["history"]["ma20Pct"], pcts[20])
        self.assertEqual(market["history"]["ma60Pct"], pcts[60])
        snapshot = market["segments"]["all"]
        self.assertEqual(market["history"]["ma20Pct"][-1], snapshot["ma20"]["pct"])
        self.assertEqual(market["history"]["ma60Pct"][-1], snapshot["ma60"]["pct"])


class MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = os.path.join(self.tmp, "data")
        self.out = os.path.join(self.tmp, "report", "breadth.json")
        os.makedirs(self.data)
        closes_up = [100] * 19 + [101]
        closes_flat = [100] * 20
        write_csv(self.data, "2330.TW.csv", closes_flat)
        write_csv(self.data, "1101.TW.csv", closes_up)
        write_csv(self.data, "6488.TWO.csv", closes_up)
        write_csv(self.data, "0050.TW.csv", closes_up)  # ETF：不納入
        write_csv(self.data, "^TWII.csv", closes_up)  # 指數：不納入
        write_csv(self.data, "AAPL.csv", closes_up)
        write_csv(self.data, "MSFT.csv", closes_flat)
        write_csv(self.data, "ZZZZ.csv", closes_up)  # 不在 S&P 500 名單內

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, us_tickers_fn=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return bb.main(["--data-dir", self.data, "--out", self.out], us_tickers_fn=us_tickers_fn)

    def load_out(self):
        with open(self.out, encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def sp500():
        return [f"SYM{i:04d}" for i in range(450)] + ["AAPL", "MSFT"]

    def test_writes_tw_and_us_with_contract_fields(self):
        self.assertEqual(self.run_main(self.sp500), 0)
        payload = self.load_out()
        self.assertEqual(payload["version"], 1)
        datetime.fromisoformat(payload["generatedAt"])
        tw = payload["markets"]["tw"]
        self.assertEqual(tw["universe"], "台股上市櫃普通股（4碼、非0開頭）")
        self.assertEqual(tw["asOf"], "2026-10-09")
        self.assertEqual(tw["segments"]["all"]["count"], 3)
        self.assertEqual(tw["segments"]["twse"]["count"], 2)
        self.assertEqual(tw["segments"]["tpex"]["count"], 1)
        us = payload["markets"]["us"]
        self.assertEqual(us["universe"], "S&P 500 成分股")
        self.assertEqual(us["segments"]["all"]["count"], 2)
        self.assertEqual(us["segments"]["all"]["ma20"], {"above": 1, "total": 2, "pct": 50.0})
        self.assertEqual(set(tw["history"]), {"dates", "ma20Pct", "ma60Pct"})

    def test_us_uses_fallback_label_when_sp500_lookup_raises(self):
        def broken():
            raise RuntimeError("wikipedia unavailable")

        self.assertEqual(self.run_main(broken), 0)
        us = self.load_out()["markets"]["us"]
        self.assertEqual(us["universe"], "S&P 500＋自選美股")
        self.assertEqual(us["segments"]["all"]["count"], 3)

    def test_us_uses_fallback_when_lookup_returns_the_three_name_default(self):
        self.assertEqual(self.run_main(lambda: ["AAPL", "MSFT", "NVDA"]), 0)
        self.assertEqual(self.load_out()["markets"]["us"]["universe"], "S&P 500＋自選美股")

    def test_no_provider_means_fallback_without_network(self):
        self.assertEqual(self.run_main(None), 0)
        self.assertEqual(self.load_out()["markets"]["us"]["universe"], "S&P 500＋自選美股")

    def test_no_data_exits_1_and_keeps_the_existing_file(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        with open(self.out, "w", encoding="utf-8") as file:
            file.write('{"old":true}')
        with contextlib.redirect_stdout(io.StringIO()):
            code = bb.main(["--data-dir", empty, "--out", self.out], us_tickers_fn=None)
        self.assertEqual(code, 1)
        with open(self.out, encoding="utf-8") as file:
            self.assertEqual(file.read(), '{"old":true}')


if __name__ == "__main__":
    unittest.main()
