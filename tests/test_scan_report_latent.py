"""每日報告掃描清單：台股 tw_all（latent_rules）的條件與排序，以及美股清單維持原行為。"""
import contextlib
import inspect
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
import main  # noqa: E402
import report_metrics  # noqa: E402

END = "2026-10-20"
FLAT = [100.0] * 30
# 最後 5 根小幅墊高／走低：收盤都在 MA20 的 −2%～+1% 內；MA20 對 5 根前上升（+0.15%）／下降（−0.15%）
RISING = [100.0] * 25 + [100.2, 100.4, 100.6, 100.8, 101.0]
FALLING = [100.0] * 25 + [99.8, 99.6, 99.4, 99.2, 99.0]


def frame(closes, volume=2_000_000, end=END):
    index = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": float(volume)}, index=index
    )


def scan(frames, **options):
    with patch.object(main, "get_tw_stock_name", side_effect=lambda ticker: ticker), \
         patch.object(main, "build_stock_data", return_value={}), \
         contextlib.redirect_stdout(io.StringIO()):
        return main._market_scan_report(list(frames), frames, **options)


def tickers_of(matches):
    return [item["ticker"] for item in matches]


class FixtureSanityTest(unittest.TestCase):
    def test_fixtures_are_inside_the_band_with_the_expected_slope(self):
        for closes, sign in ((RISING, 1), (FALLING, -1), (FLAT, 0)):
            ma20 = sum(closes[-20:]) / 20
            self.assertTrue(ma20 * 0.98 <= closes[-1] <= ma20 * 1.01)
            slope = report_metrics.ma20_slope5(closes)
            self.assertEqual((slope > 0) - (slope < 0), sign)


class LatentRulesTest(unittest.TestCase):
    def frames(self):
        return {
            "UP.TW": frame(RISING),
            "DOWN.TW": frame(FALLING),
            "FLAT.TW": frame(FLAT),
            "LOWVOL.TW": frame(RISING, volume=999_999),
            "EDGE.TW": frame(RISING, volume=1_000_000),
            "ABOVE.TWO": frame([100.0] * 25 + [100.5, 101.0, 101.5, 102.0, 103.0]),  # 收盤高於 MA20 +1%
            "SHORT.TW": frame(RISING[-19:]),
        }

    def test_requires_rising_ma20_and_volume_floor(self):
        matches = scan(self.frames(), report_min_volume=main.TW_MIN_VOLUME, latent_rules=True)
        self.assertEqual(set(tickers_of(matches)), {"UP.TW", "EDGE.TW"})

    def test_without_latent_rules_the_slope_is_not_required(self):
        matches = scan(self.frames(), report_min_volume=main.TW_MIN_VOLUME)
        self.assertEqual(set(tickers_of(matches)), {"UP.TW", "DOWN.TW", "FLAT.TW", "EDGE.TW"})

    def test_slope_uses_the_ma20_slope5_definition(self):
        # 正好持平（MA20 與 5 根前相同）不算上彎
        flat = frame(FLAT)
        self.assertEqual(report_metrics.ma20_slope5(flat["Close"].to_numpy()), 0)
        self.assertEqual(scan({"FLAT.TW": flat}, latent_rules=True), [])
        # 與 compute_stock_metrics 的 ma20Slope5 同一個函式
        metrics = report_metrics.compute_stock_metrics(frame(RISING))
        self.assertAlmostEqual(round(report_metrics.ma20_slope5(frame(RISING)["Close"].to_numpy()), 2), metrics["ma20Slope5"])

    def test_sorted_by_rs_rank_descending_then_volume(self):
        frames = {
            "A.TW": frame(RISING, volume=5_000_000),
            "B.TW": frame(RISING, volume=4_000_000),
            "C.TW": frame(RISING, volume=3_000_000),
            "D.TW": frame(RISING, volume=9_000_000),
            "E.TW": frame(RISING, volume=2_000_000),
            "F.TW": frame(RISING, volume=1_000_000),
        }
        ranks = {"A.TW": 40, "B.TW": 90, "C.TW": 90, "D.TW": None, "E.TW": 99}
        rank_of = Mock(side_effect=lambda ticker: ranks.get(ticker))
        matches = scan(frames, report_min_volume=main.TW_MIN_VOLUME, latent_rules=True, rs_rank_of=rank_of)
        # E(99) → B／C(90，同百分位依成交量) → A(40) → 沒有百分位的 D、F（依成交量）
        self.assertEqual(tickers_of(matches), ["E.TW", "B.TW", "C.TW", "A.TW", "D.TW", "F.TW"])
        self.assertEqual(sorted(call.args[0] for call in rank_of.call_args_list), sorted(frames))

    def test_a_percentile_of_one_still_beats_a_missing_percentile(self):
        frames = {"HIGHVOL.TW": frame(RISING, volume=9_000_000), "RANK1.TW": frame(RISING, volume=1_000_000)}
        rank_of = lambda ticker: 1 if ticker == "RANK1.TW" else None  # noqa: E731
        matches = scan(frames, latent_rules=True, rs_rank_of=rank_of)
        self.assertEqual(tickers_of(matches), ["RANK1.TW", "HIGHVOL.TW"])

    def test_missing_rank_function_falls_back_to_volume_order(self):
        frames = {
            "SMALL.TW": frame(RISING, volume=1_500_000),
            "BIG.TW": frame(RISING, volume=8_000_000),
            "MID.TW": frame(RISING, volume=3_000_000),
        }
        expected = ["BIG.TW", "MID.TW", "SMALL.TW"]
        self.assertEqual(tickers_of(scan(frames, latent_rules=True)), expected)
        self.assertEqual(tickers_of(scan(frames, latent_rules=True, rs_rank_of=lambda ticker: None)), expected)

    def test_failing_or_invalid_rank_values_count_as_missing(self):
        frames = {
            "OK.TW": frame(RISING, volume=1_000_000),
            "BOOM.TW": frame(RISING, volume=7_000_000),
            "NAN.TW": frame(RISING, volume=6_000_000),
            "TEXT.TW": frame(RISING, volume=5_000_000),
            "BOOL.TW": frame(RISING, volume=4_000_000),
        }

        def rank_of(ticker):
            if ticker == "BOOM.TW":
                raise RuntimeError("lookup failed")
            return {"OK.TW": 10, "NAN.TW": float("nan"), "TEXT.TW": "n/a", "BOOL.TW": True}[ticker]

        matches = scan(frames, latent_rules=True, rs_rank_of=rank_of)
        self.assertEqual(tickers_of(matches), ["OK.TW", "BOOM.TW", "NAN.TW", "TEXT.TW", "BOOL.TW"])

    def test_item_shape_is_unchanged(self):
        item = scan({"UP.TW": frame(RISING)}, latent_rules=True, rs_rank_of=lambda ticker: 80)[0]
        self.assertEqual(set(item), {"ticker", "name", "volume", "ma_list", "chart_data"})
        self.assertEqual((item["volume"], item["ma_list"]), (2_000_000, [20]))


class UsListUnchangedTest(unittest.TestCase):
    def test_default_options_keep_the_original_filter_and_volume_sort(self):
        frames = {
            "UP": frame(RISING, volume=1),
            "DOWN": frame(FALLING, volume=3),
            "FLAT": frame(FLAT, volume=2),
            "HIGH": frame([100.0] * 25 + [100.5, 101.0, 101.5, 102.0, 103.0], volume=9),
        }
        rank_of = Mock(return_value=99)
        matches = scan(frames, rs_rank_of=rank_of)
        # 美股不要求 MA20 上彎、不看 RS 百分位：依成交量由大到小
        self.assertEqual(tickers_of(matches), ["DOWN", "FLAT", "UP"])
        rank_of.assert_not_called()
        self.assertEqual(tickers_of(scan(frames)), ["DOWN", "FLAT", "UP"])
        self.assertEqual(tickers_of(scan(frames, report_min_volume=2)), ["DOWN", "FLAT"])

    def test_signature_defaults_leave_latent_rules_off(self):
        parameters = inspect.signature(main._market_scan_report).parameters
        self.assertIs(parameters["latent_rules"].default, False)
        self.assertIsNone(parameters["rs_rank_of"].default)
        self.assertEqual(inspect.signature(main.scan_market).parameters["report_options"].kind, inspect.Parameter.VAR_KEYWORD)


class ReportRsRankOfTest(unittest.TestCase):
    def test_reads_rs_rank_from_the_metrics_lookup(self):
        lookup = {"A.TW": {"rsRank": 77}, "B.TW": {"rsRank": None}, "C.TW": None}
        rank_of = main.report_rs_rank_of(lambda ticker: lookup.get(ticker))
        self.assertEqual([rank_of(ticker) for ticker in ("A.TW", "B.TW", "C.TW", "D.TW")], [77, None, None, None])

    def test_without_a_lookup_every_rank_is_missing(self):
        rank_of = main.report_rs_rank_of(None)
        self.assertIsNone(rank_of("A.TW"))


class WiringTest(unittest.TestCase):
    """tw_all 的 RS 百分位來源：由 main() 呼叫順序與 build_report_universe 的 lookup 實際算出。"""

    def test_main_builds_metrics_before_scanning_tw_all(self):
        source = inspect.getsource(main.main)
        universe_call = source.index("build_report_universe(")
        tw_scan = source.index("_market_scan_report(")
        self.assertLess(universe_call, tw_scan)
        self.assertEqual(source.count("build_report_universe("), 1)
        self.assertEqual(source.count("_market_scan_report("), 1)
        tw_call = source[tw_scan:source.index(")\n\n", tw_scan)]
        for needle in ("tw_tickers", "tw_frames", "TW_MIN_VOLUME", "latent_rules=True", "rs_rank_of=report_rs_rank_of("):
            self.assertIn(needle, tw_call)
        self.assertIn("metrics_lookup", tw_call)
        # 美股清單維持原呼叫（沒有任何選項），台股不再走 scan_market
        self.assertIn("us_all = scan_market(\n        us_tickers\n    )", source)
        self.assertNotIn("scan_market(\n        tw_tickers", source)
        # 下載（collect_market_frames）早於篩選；universe 與 lookup 只建一次後沿用到報告 JSON
        self.assertLess(source.index("collect_market_frames("), universe_call)
        self.assertEqual(source.count("metrics_lookup=metrics_lookup"), 1)

    def test_rs_rank_reaches_the_list_without_downloads(self):
        count = 12
        symbols = [f"T{i}.TW" for i in range(count)]
        downloads = []

        def downloader(symbol):
            downloads.append(symbol)
            return pd.DataFrame()

        def stock(i, extra=None):
            # 前 270 根依 i 不同的速度上漲，最後 30 根整理（上彎、收盤在 MA20 帶內）
            rising = [100.0 * (1 + 0.001 * (i + 1)) ** step for step in range(270)]
            base = rising[-1]
            tail = [base] * 25 + [base * ratio for ratio in (1.002, 1.004, 1.006, 1.008, 1.01)]
            return frame(rising + tail, volume=(count - i) * 1_500_000)  # 成交量與 RS 相反

        frames_by_symbol = {symbol: stock(i) for i, symbol in enumerate(symbols)}
        frames_by_symbol["DOWN.TW"] = frame([100.0] * 270 + FALLING, volume=50_000_000)  # MA20 下彎
        frames_by_symbol["LOWVOL.TW"] = frame([100.0 * 1.002 ** step for step in range(270)] + RISING, volume=10)
        frames_by_symbol["NEW.TW"] = frame(RISING, volume=40_000_000)  # 歷史不足 63 根：沒有 RS 百分位
        bench = frame([100.0] * 400)

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            report_dir = Path(directory) / "report"
            data_dir.mkdir()
            report_dir.mkdir()

            for symbol, df in frames_by_symbol.items():
                df.to_csv(data_dir / f"{symbol}.csv", index_label="Date")

            tickers = list(frames_by_symbol)

            with patch.object(main, "DATA_DIR", str(data_dir)), \
                 patch.dict(main.INDEX_HISTORY_CACHE, {"^TWII": {"df": bench, "source": "test"}}), \
                 patch.dict(main.REPORT_NON_TRADING_DATES, clear=True), \
                 patch.object(main, "get_tw_stock_name", side_effect=lambda ticker: ticker), \
                 patch.object(main, "build_stock_data", return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                frames = main._scan_analysis_frames(tickers)
                universes, lookup = main.build_report_universe(
                    tickers, [], data_dir=str(data_dir), downloader=downloader, report_dir=str(report_dir)
                )
                matches = main._market_scan_report(
                    tickers, frames, main.TW_MIN_VOLUME,
                    latent_rules=True, rs_rank_of=main.report_rs_rank_of(lookup)
                )
                fallback = main._market_scan_report(
                    tickers, frames, main.TW_MIN_VOLUME,
                    latent_rules=True, rs_rank_of=main.report_rs_rank_of(None)
                )

        self.assertEqual(downloads, ["SPY"])  # 台股基準取自 INDEX_HISTORY_CACHE，沒有額外下載
        ranks = {symbol: lookup(symbol)["rsRank"] for symbol in tickers}
        self.assertEqual(universes["TW"]["rsField"], "rsScore")
        self.assertIsNone(ranks["NEW.TW"])
        self.assertTrue(all(ranks[symbol] is not None for symbol in symbols))
        self.assertEqual([ranks[symbol] for symbol in symbols], sorted(ranks[symbol] for symbol in symbols))
        # 結果：不含下彎與低量；RS 百分位由高到低，沒有百分位的 NEW 排最後（儘管它成交量最大）
        self.assertEqual(tickers_of(matches), list(reversed(symbols)) + ["NEW.TW"])
        # 沒有百分位時整份清單依成交量（與 RS 相反）排序
        self.assertEqual(tickers_of(fallback), ["NEW.TW"] + symbols)


if __name__ == "__main__":
    unittest.main()
