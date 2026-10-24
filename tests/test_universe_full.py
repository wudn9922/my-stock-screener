"""
全市場 universe（scripts/build_universe_full.py、universe_sources.py、universe_cache.py）與 main.py 共存邏輯的離線測試。
不連網路：官方／Nasdaq 格式用 tests/fixtures/universe 與 atlas 既有 fixture，Yahoo 以假網路模擬。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_universe_full as bu  # noqa: E402
import report_metrics  # noqa: E402
import universe_cache as uc  # noqa: E402
import universe_sources as us  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "universe")
ATLAS_FIXTURES = os.path.join(ROOT, "atlas", "tests", "fixtures")


def fixture(name, base=FIXTURES):
    with open(os.path.join(base, name), encoding="utf-8") as file:
        return json.load(file) if name.endswith(".json") else file.read()


def weekdays(end, count):
    day = date.fromisoformat(end)
    days = []

    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)

    return list(reversed(days))


def frame(closes, end="2026-10-13", volumes=None):
    days = weekdays(end, len(closes))
    volumes = volumes if volumes is not None else [1000.0] * len(closes)
    return uc.frame_from_rows([
        (day, close, close * 1.01, close * 0.99, close, volume)
        for day, close, volume in zip(days, closes, volumes)
    ])


# =========================================================================
# 來源解析
# =========================================================================
class TaiwanSourcesTest(unittest.TestCase):
    def test_listed_companies(self):
        unknown = set()
        items = us.parse_tw_companies(fixture("t187ap03_L.json"), "TWSE", unknown)
        self.assertEqual(sorted(items), ["1101.TW", "2330.TW", "2881.TW", "4763.TW", "6666.TW"])
        self.assertEqual(items["2330.TW"], {
            "name": "台積電", "exchange": "TWSE", "industry": "半導體業", "shares": 25932524521,
        })
        self.assertEqual(items["1101.TW"]["industry"], "水泥工業")
        self.assertEqual(items["4763.TW"]["industry"], "綠能環保")
        # 沒有已發行股數：實收資本額 ÷ 面額 − 特別股
        self.assertEqual(items["2881.TW"]["shares"], 15_400_000_000 - 1_000_000_000)
        # 未知產業代碼留空並回報；股數 '--' → None
        self.assertIsNone(items["6666.TW"]["industry"])
        self.assertIsNone(items["6666.TW"]["shares"])
        self.assertEqual(unknown, {"77"})

    def test_otc_companies(self):
        items = us.parse_tw_companies(fixture("mopsfin_t187ap03_O.json"), "TPEx")
        self.assertEqual(sorted(items), ["5347.TWO", "6488.TWO", "8069.TWO"])
        self.assertEqual(items["6488.TWO"]["shares"], 478092629)
        self.assertEqual(items["6488.TWO"]["industry"], "半導體業")
        self.assertEqual(items["8069.TWO"]["shares"], 1140472005)  # 資本額 ÷ 10
        self.assertEqual(items["5347.TWO"]["industry"], "半導體業")  # 已是中文

    def test_industry_names(self):
        self.assertEqual(us.tw_industry_name("24"), "半導體業")
        self.assertEqual(us.tw_industry_name("024"), "半導體業")
        self.assertEqual(us.tw_industry_name(5), "電機機械")
        self.assertEqual(us.tw_industry_name("光電業"), "光電業")
        self.assertIsNone(us.tw_industry_name(""))
        self.assertEqual(len(us.TW_INDUSTRY_NAMES), len(set(us.TW_INDUSTRY_NAMES.values())))

    def test_bad_company_payload_explains_itself(self):
        with self.assertRaises(us.SourceFormatError) as context:
            us.parse_tw_companies({"message": "busy"}, "TWSE")
        self.assertIn("t187ap03_L", str(context.exception))
        with self.assertRaises(us.SourceFormatError) as context:
            us.parse_tw_companies([{"foo": "1"}], "TPEx")
        self.assertIn("公司代號", str(context.exception))

    def test_twse_day_all(self):
        snapshot = us.parse_twse_day_all(fixture("STOCK_DAY_ALL.json"))
        self.assertEqual(snapshot["date"], "2026-10-13")
        self.assertEqual(sorted(snapshot["bars"]), ["1101.TW", "2330.TW", "6666.TW", "9103.TW"])
        tsmc = snapshot["bars"]["2330.TW"]
        self.assertEqual((tsmc["open"], tsmc["high"], tsmc["low"], tsmc["close"]), (1250.0, 1260.0, 1245.0, 1255.0))
        self.assertEqual(tsmc["volume"], 30000000.0)
        self.assertEqual(tsmc["reference"], 1260.0)
        self.assertEqual(snapshot["bars"]["1101.TW"]["reference"], 27.05 - 0.70)
        self.assertIsNone(snapshot["bars"]["6666.TW"]["close"])  # 無成交

    def test_twse_rwd_json_variant(self):
        payload = {
            "stat": "OK", "date": "20261013",
            "fields": ["證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數"],
            "data": [["2330", "台積電", "30,000,000", "0", "1,250.00", "1,260.00", "1,245.00", "1,255.00", "-5.00", "1"]],
        }
        snapshot = us.parse_twse_day_all(payload)
        self.assertEqual(snapshot["date"], "2026-10-13")
        self.assertEqual(snapshot["bars"]["2330.TW"]["close"], 1255.0)

    def test_tpex_quotes(self):
        snapshot = us.parse_tpex_daily_quotes(fixture("tpex_mainboard_daily_close_quotes.json"))
        self.assertEqual(snapshot["date"], "2026-10-13")
        self.assertEqual(sorted(snapshot["bars"]), ["6488.TWO", "8069.TWO"])
        self.assertEqual(snapshot["bars"]["6488.TWO"]["volume"], 2345000.0)
        self.assertEqual(snapshot["bars"]["8069.TWO"]["reference"], 235.0)  # X-4.50：除權息參考價

    def test_bad_quote_payloads(self):
        with self.assertRaises(us.SourceFormatError):
            us.parse_twse_day_all([{"Code": "2330", "ClosingPrice": "1"}])  # 沒有日期
        with self.assertRaises(us.SourceFormatError) as context:
            us.parse_tpex_daily_quotes([{"代碼": "6488"}])
        self.assertIn("SecuritiesCompanyCode", str(context.exception))

    def test_companies_from_quotes_fallback(self):
        snapshot = us.parse_twse_day_all(fixture("STOCK_DAY_ALL.json"))
        items = us.tw_companies_from_quotes(snapshot, "TWSE")
        self.assertEqual(sorted(items), ["1101.TW", "2330.TW", "6666.TW"])
        self.assertIsNone(items["2330.TW"]["industry"])

    def test_numbers_and_dates(self):
        self.assertEqual(us.number("1,255.00"), 1255.0)
        self.assertEqual(us.number("$227.52"), 227.52)
        self.assertEqual(us.number("X0.00"), 0.0)
        self.assertIsNone(us.number("--"))
        self.assertIsNone(us.number(float("nan")))
        self.assertEqual(us.roc_date("1151007"), date(2026, 10, 7))
        self.assertEqual(us.roc_date("20261007"), date(2026, 10, 7))
        self.assertEqual(us.roc_date("115/10/07"), date(2026, 10, 7))
        self.assertIsNone(us.roc_date("bad"))


class UsSourcesTest(unittest.TestCase):
    def test_nasdaqtrader_matches_symbol_directory_rules(self):
        """與 atlas/tests/symbol-directory.test.ts 同一份 fixture、同樣的期望結果。"""
        excluded = {}
        items = us.parse_us_listed(
            fixture("nasdaqlisted.txt", os.path.join(ATLAS_FIXTURES, "directory")),
            fixture("otherlisted.txt", os.path.join(ATLAS_FIXTURES, "directory")),
            excluded,
        )
        self.assertEqual(
            [(symbol, item["name"], item["exchange"]) for symbol, item in items.items()],
            [
                ("AAPL", "Apple Inc.", "NASDAQ"),
                ("ASML", "ASML Holding N.V.", "NASDAQ"),
                ("GOOG", "Alphabet Inc.", "NASDAQ"),
                ("GOOGL", "Alphabet Inc.", "NASDAQ"),
                ("NVDA", "NVIDIA Corporation", "NASDAQ"),
                ("A", "Agilent Technologies, Inc.", "NYSE"),
                ("BRK-B", "Berkshire Hathaway Inc.", "NYSE"),
                ("EPD", "Enterprise Products Partners L.P.", "NYSE"),
                ("FRT", "Federal Realty Investment Trust", "NYSE"),
                ("IMO", "Imperial Oil Limited", "NYSE American"),
                ("NVO", "Novo Nordisk A/S", "NYSE"),
                ("TSM", "Taiwan Semiconductor Manufacturing Company Ltd.", "NYSE"),
                ("XYZ", "Block, Inc.", "NYSE"),
            ],
        )
        self.assertEqual(excluded["test"], 2)
        self.assertEqual(excluded["etf"], 2)
        for reason in ("warrant", "preferred", "fixed-income", "right", "spac", "fund"):
            self.assertIn(reason, excluded)

    def test_symbol_normalization(self):
        self.assertEqual(us.normalize_us_symbol("BRK.B"), "BRK-B")
        self.assertEqual(us.normalize_us_symbol("BRK/B"), "BRK-B")
        self.assertEqual(us.normalize_us_symbol("nvda"), "NVDA")
        for raw in ("BRK$B", "ABR$D", "AAM.U", "AAM.WS", "AAM.WS.A", "XYZ.R", "AAM=", "ABR^D", "FOO.WI"):
            self.assertIsNone(us.normalize_us_symbol(raw), raw)

    def test_exclusion_reasons(self):
        self.assertIsNone(us.us_exclusion_reason("Energy Transfer LP Common Units representing limited partner interests"))
        self.assertIsNone(us.us_exclusion_reason("Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares"))
        self.assertEqual(us.us_exclusion_reason("Foo Corp. Units, each consisting of one share"), "unit")
        self.assertEqual(us.us_exclusion_reason("Foo Corp - Rights"), "right")
        self.assertEqual(us.us_exclusion_reason("Bar Inc. 8.00% Senior Notes due 2030"), "fixed-income")
        self.assertEqual(us.us_exclusion_reason("Bar Inc. Senior Notes due 2030"), "debt")
        self.assertEqual(us.us_exclusion_reason("Eaton Vance Municipal Income Trust"), "fund")
        self.assertEqual(us.us_exclusion_reason("Foo Corp Warrant"), "warrant")
        self.assertEqual(us.us_exclusion_reason("Foo Corp Common Stock When Issued"), "when-issued")
        self.assertEqual(us.us_exclusion_reason("Some Capital Acquisition Corporation Class A Ordinary Shares"), "spac")

    def test_bad_nasdaqtrader_header(self):
        with self.assertRaises(us.SourceFormatError) as context:
            us.parse_us_listed("Ticker|Name\nAAPL|Apple\n", "")
        self.assertIn("Symbol", str(context.exception))

    def test_screener(self):
        rows = us.parse_nasdaq_screener(fixture("nasdaq_screener.json"))
        self.assertEqual(sorted(rows), ["AAPL", "BRK-B", "NEWCO"])  # ABR^D 特別股被代號規則排除
        self.assertEqual(rows["AAPL"]["sector"], "Technology")
        self.assertEqual(rows["AAPL"]["industry"], "Computer Manufacturing")
        self.assertEqual(rows["AAPL"]["marketCap"], 3.75e12)
        self.assertEqual(us.implied_shares(rows["AAPL"]["marketCap"], rows["AAPL"]["lastSale"]), 15_000_000_000)
        self.assertIsNone(rows["NEWCO"]["marketCap"])
        self.assertIsNone(rows["NEWCO"]["sector"])
        self.assertIsNone(us.implied_shares(None, 10))
        with self.assertRaises(us.SourceFormatError):
            us.parse_nasdaq_screener({"data": None, "status": {"rCode": 400}})

    def test_yahoo_chart_fixture(self):
        rows, splits, meta = us.parse_yahoo_chart(fixture("NFLX.json", os.path.join(ATLAS_FIXTURES, "market")), "NFLX")
        self.assertEqual(meta["exchangeTimezoneName"], "America/New_York")
        self.assertEqual(len(rows), len({row[0] for row in rows}))
        self.assertEqual(rows, sorted(rows))
        self.assertTrue(all(row[2] >= row[4] >= row[3] for row in rows))
        self.assertEqual(splits[0][1:], (10.0, 1.0))
        # 台股：時間戳 01:00 UTC → 台北當天
        rows, _, _ = us.parse_yahoo_chart(
            fixture("6488_TWO_1d.json", os.path.join(ATLAS_FIXTURES, "market", "taiwan")), "6488.TWO"
        )
        self.assertEqual(rows[-1][0], "2026-10-08")
        self.assertEqual(rows[-1][4], 1130.0)

    def test_yahoo_chart_errors(self):
        with self.assertRaises(us.SourceFormatError) as context:
            us.parse_yahoo_chart({"chart": {"result": None, "error": {"description": "No data found, symbol may be delisted"}}}, "ZZZ")
        self.assertIn("delisted", str(context.exception))
        self.assertEqual(us.parse_yahoo_chart({"chart": {"result": [], "error": None}})[0], [])

    def test_yahoo_quotes(self):
        stamp = int(datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc).timestamp())
        payload = {"quoteResponse": {"result": [
            {"symbol": "AAPL", "regularMarketTime": stamp, "regularMarketOpen": 248, "regularMarketDayHigh": 251,
             "regularMarketDayLow": 247, "regularMarketPrice": 250, "regularMarketVolume": 5e7,
             "regularMarketPreviousClose": 248.8, "quoteType": "EQUITY", "marketState": "POST",
             "sharesOutstanding": 1.5e10, "exchangeTimezoneName": "America/New_York"},
            {"symbol": "HALT", "regularMarketTime": stamp, "regularMarketPrice": 3.0},
        ], "error": None}}
        bars = us.parse_yahoo_quotes(payload, report_metrics.market_of)
        # Preserve malformed source observations until the acceptance boundary can
        # refetch/log them; the parser must not hide them or invent flat prices.
        self.assertEqual(list(bars), ["AAPL", "HALT"])
        self.assertIsNone(bars['HALT']['open'])
        self.assertEqual(bars["AAPL"]["date"], "2026-10-13")
        self.assertEqual(bars["AAPL"]["previousClose"], 248.8)
        self.assertEqual(bars["AAPL"]["shares"], 1.5e10)


# =========================================================================
# 快取
# =========================================================================
class CacheTest(unittest.TestCase):
    def test_write_and_read_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "X.csv")
            data = uc.frame_from_rows([
                ("2026-10-12", 22.350000381469727, 23.0, 22.0, 22.700000762939453, 1234.6),
                ("2026-10-09", 22.0, 22.5, 21.5, 22.25, 0.0),
            ])
            uc.write_bars(path, data)
            with open(path, encoding="utf-8") as file:
                text = file.read()
            self.assertEqual(
                text,
                "Date,Open,High,Low,Close,Volume\n2026-10-09,22.0,22.5,21.5,22.25,0\n2026-10-12,22.35,23.0,22.0,22.7,1235\n",
            )
            back = report_metrics.read_price_csv(path)  # 與 data/*.csv 同一個讀取器
            self.assertEqual(list(back["Close"]), [22.25, 22.7])

    def test_unknown_source_volume_survives_chart_cache_and_metric_round_trip(self):
        days = weekdays("2026-10-13", 20)
        stamps = [int(datetime.fromisoformat(day + "T16:00:00+00:00").timestamp()) for day in days]
        for missing in (None, float("nan"), float("inf")):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                payload = {"chart": {"result": [{
                    "meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": stamps,
                    "indicators": {"quote": [{"open": [10] * 20, "high": [11] * 20,
                        "low": [9] * 20, "close": [10] * 20, "volume": [1000] * 19 + [missing]}]},
                }], "error": None}}
                rows, _, _ = us.parse_yahoo_chart(payload, "TEST")
                self.assertIsNone(rows[-1][-1])
                source = uc.frame_from_rows(rows)
                # Existing consumers still see the historical zero fallback.
                self.assertEqual(source["Volume"].iloc[-1], 0)
                path = os.path.join(tmp, "TEST.csv")
                uc.write_bars(path, uc.clean(source))
                with open(path, encoding="utf-8") as file:
                    self.assertTrue(file.read().splitlines()[-1].endswith(","))
                cached = uc.read_bars(path)
                self.assertEqual(cached["Volume"].iloc[-1], 0)
                for loaded in (cached, report_metrics.read_price_csv(path)):
                    record = report_metrics.build_stock_record("TEST", loaded, {}, "US")
                    self.assertIsNone(record["metrics"]["volumeAvg20"])
                # A second write cannot convert the unknown cell into a real zero.
                uc.write_bars(path, cached)
                self.assertIsNone(report_metrics.build_stock_record(
                    "TEST", report_metrics.read_price_csv(path), {}, "US")["metrics"]["volumeAvg20"])

    def test_real_zero_source_volume_remains_known_after_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "TEST.csv")
            uc.write_bars(path, frame([10] * 20, volumes=[1000] * 19 + [0]))
            with open(path, encoding="utf-8") as file:
                self.assertTrue(file.read().splitlines()[-1].endswith(",0"))
            record = report_metrics.build_stock_record("TEST", uc.read_bars(path), {}, "US")
            self.assertEqual(record["metrics"]["volumeAvg20"], 950)

    def test_volume_lineage_survives_incremental_full_merge_and_source_repair(self):
        old = frame([10] * 20, end="2026-10-12", volumes=[1000] * 19 + [None])
        next_day = frame([10], end="2026-10-13")
        appended = uc.merge_incremental(old, next_day)
        self.assertIsNone(report_metrics.compute_stock_metrics(appended)["volumeAvg20"])
        # New source data can confirm that the former unknown observation was truly zero.
        confirmed = frame([10], end="2026-10-12", volumes=[0])
        repaired = uc.merge_incremental(old, confirmed)
        self.assertEqual(report_metrics.compute_stock_metrics(repaired)["volumeAvg20"], 950)
        # Full history replacement retains the newer cached unknown session, and its lineage.
        recent = frame([10] * 21, end="2026-10-13", volumes=[1000] * 20 + [None])
        complete = uc.merge_full(recent, frame([10] * 20, end="2026-10-12"))
        self.assertIsNone(report_metrics.compute_stock_metrics(complete)["volumeAvg20"])
        # A replacement containing unknowns cannot inherit the old frame's known-volume status.
        replaced = uc.merge_full(frame([10] * 20), old)
        self.assertIsNone(report_metrics.compute_stock_metrics(replaced)["volumeAvg20"])

    def test_daily_quote_unknown_volume_is_not_a_known_zero_when_appended(self):
        stamp = int(datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc).timestamp())
        payload = {"quoteResponse": {"result": [{"symbol": "TEST", "regularMarketTime": stamp,
            "regularMarketOpen": 10, "regularMarketDayHigh": 11, "regularMarketDayLow": 9,
            "regularMarketPrice": 10, "regularMarketVolume": None}], "error": None}}
        bar = us.parse_yahoo_quotes(payload, report_metrics.market_of)["TEST"]
        self.assertIsNone(bar["volume"])
        job = object.__new__(bu.Run)
        job.pending = {"TEST": bar}
        job.agreement = []
        job.calendars = {"US": uc.TradingCalendar(["2026-10-12", "2026-10-13"])}
        # Same-day source comparison must remain valid even without a quote volume.
        job.apply_pending("TEST", frame([10] * 20))
        self.assertIsNone(job.agreement[-1][2])
        combined = uc.append_row(frame([10] * 19, end="2026-10-12"), uc.snapshot_bar(bar, 10))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "TEST.csv")
            uc.write_bars(path, combined)
            record = report_metrics.build_stock_record("TEST", report_metrics.read_price_csv(path), {}, "US")
            self.assertIsNone(record["metrics"]["volumeAvg20"])

    def test_keep_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = uc.write_bars(os.path.join(tmp, "X.csv"), frame([10.0] * 400))
            self.assertEqual(len(written), uc.KEEP_BARS)

    def test_merge_incremental_and_full(self):
        old = frame([10, 11, 12, 13], end="2026-10-12")
        new = frame([12.5, 13, 14], end="2026-10-13")  # 10-09, 10-12, 10-13
        merged = uc.merge_incremental(old, new)
        self.assertEqual(list(merged["Close"]), [10, 11, 12.5, 13, 14])
        # 整檔重抓：新歷史取代舊的，只留比新資料更新的 K
        old = frame([20, 20, 20, 20, 21], end="2026-10-13")
        new = frame([10, 10, 10], end="2026-10-12")
        merged = uc.merge_full(old, new)
        self.assertEqual(list(merged["Close"]), [10, 10, 10, 21])

    def test_overlap_detects_split(self):
        old = frame([100, 102, 104, 106], end="2026-10-12")
        same = frame([104, 106.2, 107], end="2026-10-13")
        check = uc.compare_overlap(old, same)
        self.assertTrue(check["ok"])
        self.assertEqual(check["compared"], 2)
        split = frame([52, 53, 53.5], end="2026-10-13")  # 1:2 分割後 Yahoo 調整了歷史
        check = uc.compare_overlap(old, split)
        self.assertFalse(check["ok"])
        self.assertAlmostEqual(check["worst"], 0.5, places=3)
        self.assertIsNone(uc.compare_overlap(old, frame([1, 2], end="2027-01-05")))

    def test_calendar(self):
        calendar = uc.TradingCalendar(["2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"])  # 10-09 休市
        self.assertEqual(calendar.previous("2026-10-12"), "2026-10-08")
        self.assertEqual(calendar.previous("2026-10-14"), "2026-10-13")
        self.assertEqual(calendar.previous("2026-10-19"), "2026-10-16")  # 超出範圍：平日推算
        self.assertEqual(calendar.nth_last("2026-10-13", 3), "2026-10-08")
        self.assertEqual(uc.TradingCalendar().previous("2026-10-12"), "2026-10-09")

    def test_session_done(self):
        evening = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)  # 台北 15:00、紐約 03:00
        self.assertTrue(uc.session_done("2026-10-13", "TW", evening))
        self.assertFalse(uc.session_done("2026-10-13", "TW", datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc)))
        self.assertTrue(uc.session_done("2026-10-12", "US", evening))
        self.assertFalse(uc.session_done("2026-10-13", "US", datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)))
        self.assertFalse(uc.session_done("2026-10-14", "US", evening))
        data = frame([1, 2, 3], end="2026-10-13")
        self.assertEqual(len(uc.drop_unfinished(data, "US", datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc))), 2)

    def test_snapshot_actions(self):
        calendar = uc.TradingCalendar(["2026-10-09", "2026-10-12", "2026-10-13"])
        entry = {"full": "x", "last": "2026-10-12", "close": 100.0}
        bar = {"date": "2026-10-13", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 5, "reference": 100.0}
        self.assertEqual(uc.snapshot_action(entry, bar, calendar, "TW"), ("append", ("2026-10-13", 101, 103, 100, 102, 5.0)))
        # 除權：參考價 90 ≠ 快取收盤 100
        self.assertEqual(uc.snapshot_action(entry, dict(bar, reference=90.0), calendar, "TW")[0], "adjusted")
        # 沒有官方漲跌：超過漲跌幅限制也視為調整
        self.assertEqual(uc.snapshot_action(entry, dict(bar, reference=None, close=80), calendar, "TW")[0], "adjusted")
        # 美股看前收盤
        us_bar = dict(bar, reference=None, previousClose=50.0)
        self.assertEqual(uc.snapshot_action(entry, us_bar, calendar, "US")[0], "adjusted")
        self.assertEqual(uc.snapshot_action(entry, dict(us_bar, previousClose=100.2), calendar, "US")[0], "append")
        # 缺口、同一天、較舊
        self.assertEqual(uc.snapshot_action(dict(entry, last="2026-10-09"), bar, calendar, "TW")[0], "gap")
        self.assertEqual(uc.snapshot_action(dict(entry, last="2026-10-13"), bar, calendar, "TW")[0], "same")
        self.assertEqual(uc.snapshot_action(dict(entry, last="2026-10-14"), bar, calendar, "TW")[0], "older")
        # 無成交：平盤量 0
        action, row = uc.snapshot_action(entry, dict(bar, close=None, reference=None), calendar, "TW")
        self.assertEqual((action, row), ("append", ("2026-10-13", 100.0, 100.0, 100.0, 100.0, 0.0)))

    def test_eligibility(self):
        complete = {"full": "2026-10-01", "last": "2026-10-13", "bars": 250, "vol20": 5000}
        self.assertIsNone(uc.eligibility(complete, "2026-10-07"))
        self.assertEqual(uc.eligibility({"last": "2026-10-13"}, "2026-10-07"), "missing")
        self.assertEqual(uc.eligibility(dict(complete, last="2026-10-06"), "2026-10-07"), "stale")
        self.assertEqual(uc.eligibility(dict(complete, vol20=0), "2026-10-07"), "noVolume")
        self.assertIsNone(uc.eligibility(dict(complete, vol20=0, bars=10), "2026-10-07"))  # 新上市太短，不判斷

    def test_failure_backoff(self):
        state = {"version": 1, "symbols": {}}
        uc.record_failure(state, "X", "2026-10-13", "HTTP 404")
        self.assertFalse(uc.can_retry(state["symbols"]["X"], "2026-10-13"))
        self.assertTrue(uc.can_retry(state["symbols"]["X"], "2026-10-14"))
        uc.record_failure(state, "X", "2026-10-14", "HTTP 404")
        uc.record_failure(state, "X", "2026-10-16", "HTTP 404")
        self.assertEqual(state["symbols"]["X"]["retryAfter"], "2026-10-20")
        uc.record_frame(state, "X", frame([1, 2]), "2026-10-20", full=True)
        self.assertNotIn("fails", state["symbols"]["X"])
        self.assertTrue(uc.is_complete(state["symbols"]["X"]))


# =========================================================================
# 端到端（假網路）
# =========================================================================
TW_SYMBOLS = ["1101.TW", "1102.TW", "2330.TW", "2454.TW", "6488.TWO", "8069.TWO"]
US_SYMBOLS = ["AAPL", "BRK-B", "MSFT", "NVDA", "HALT"]
NOW = datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc)  # 台北 09:00、紐約 10-13 21:00


def chart_payload(data, market):
    hour = 1 if market == "TW" else 13
    zone = "Asia/Taipei" if market == "TW" else "America/New_York"
    stamps = [
        int(datetime.fromisoformat(f"{day:%Y-%m-%d}T{hour:02d}:30:00+00:00").timestamp()) for day in data.index
    ]
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": zone, "gmtoffset": 28800 if market == "TW" else -14400},
        "timestamp": stamps,
        "indicators": {"quote": [{
            "open": list(data["Open"]), "high": list(data["High"]), "low": list(data["Low"]),
            "close": list(data["Close"]), "volume": list(data["Volume"]),
        }]},
    }], "error": None}}


class FakeNet:
    """Yahoo / 證交所 / 櫃買 / NasdaqTrader / Nasdaq 的假網路；world 是每檔「真實」的日 K（Yahoo 看到的樣子）。"""

    def __init__(self, world, today_bars=None, fail=()):
        self.world = world
        self.today_bars = today_bars or {}
        self.fail = set(fail)
        self.calls = []
        self.clock = 0.0

    def sleep(self, seconds):
        self.clock += seconds

    def _check(self, key):
        self.calls.append(key)
        if key in self.fail:
            raise bu.HttpError(503, key)

    def get_json(self, url, params=None, headers=None, timeout=30):
        if url == bu.TWSE_COMPANIES_URL:
            self._check("t187ap03_L")
            return [{"公司代號": s[:4], "公司簡稱": f"N{s[:4]}", "產業別": "24", "已發行普通股數或TDR原股發行股數": "1000000"}
                    for s in TW_SYMBOLS if s.endswith(".TW")]
        if url == bu.TPEX_COMPANIES_URL:
            self._check("t187ap03_O")
            return [{"SecuritiesCompanyCode": s[:4], "CompanyAbbreviation": f"O{s[:4]}", "SecuritiesIndustryCode": "28"}
                    for s in TW_SYMBOLS if s.endswith(".TWO")]
        if url in (bu.TWSE_DAY_ALL_URL, bu.TPEX_DAILY_URL):
            listed = url == bu.TWSE_DAY_ALL_URL
            self._check("twse" if listed else "tpex")
            out = []
            for symbol, bar in self.today_bars.items():
                if symbol.endswith(".TW") != listed or not symbol.endswith((".TW", ".TWO")):
                    continue
                roc = f"{int(bar['date'][:4]) - 1911}{bar['date'][5:7]}{bar['date'][8:]}"
                change = bar["close"] - bar["reference"]
                if listed:
                    out.append({"Date": roc, "Code": symbol[:4], "Name": "x", "TradeVolume": str(bar["volume"]),
                                "OpeningPrice": str(bar["open"]), "HighestPrice": str(bar["high"]),
                                "LowestPrice": str(bar["low"]), "ClosingPrice": str(bar["close"]), "Change": f"{change:.2f}"})
                else:
                    out.append({"Date": roc, "SecuritiesCompanyCode": symbol[:4], "CompanyName": "x",
                                "TradingShares": str(bar["volume"]), "Open": str(bar["open"]), "High": str(bar["high"]),
                                "Low": str(bar["low"]), "Close": str(bar["close"]), "Change": f"{change:.2f}"})
            return out
        if url == bu.NASDAQ_SCREENER_URL:
            self._check("screener")
            return {"data": {"rows": [
                {"symbol": s.replace("-", "/"), "name": f"{s} Inc. Common Stock", "lastsale": "$10.00",
                 "marketCap": "1000000", "sector": "Technology", "industry": "Software"} for s in US_SYMBOLS
            ]}}
        raise AssertionError(url)

    def get_text(self, url, params=None, headers=None, timeout=30):
        if url == bu.NASDAQ_LISTED_URL:
            self._check("nasdaqlisted")
            rows = [f"{s}|{s} Inc. - Common Stock|Q|N|N|100|N|N" for s in US_SYMBOLS if s not in ("BRK-B",)]
            return "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n" + \
                "\n".join(rows + ["QQQ|Invesco QQQ Trust|G|N|N|100|Y|N", "ZXZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N",
                                  "File Creation Time: 1013202617:01|||||||"])
        if url == bu.OTHER_LISTED_URL:
            self._check("otherlisted")
            return ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
                    "BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|100|N|BRK/B\n"
                    "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n")
        raise AssertionError(url)

    def connect_yahoo(self):
        self._check("crumb")

    def chart(self, symbol, range_):
        self._check(f"chart:{symbol}:{range_}")
        self.clock += 1.0
        data = self.world.get(symbol)
        if data is None:
            raise bu.HttpError(404, symbol)
        data = data.tail(500 if range_ == bu.FULL_RANGE else 63)
        return chart_payload(data, report_metrics.market_of(symbol))

    def quote(self, symbols):
        self._check("quote")
        result = []
        for symbol in symbols:
            bar = self.today_bars.get(symbol)
            if bar is None or report_metrics.market_of(symbol) != "US":
                continue
            stamp = int(datetime.fromisoformat(f"{bar['date']}T20:00:00+00:00").timestamp())
            result.append({"symbol": symbol, "regularMarketTime": stamp, "regularMarketOpen": bar["open"],
                           "regularMarketDayHigh": bar["high"], "regularMarketDayLow": bar["low"],
                           "regularMarketPrice": bar["close"], "regularMarketVolume": bar["volume"],
                           "regularMarketPreviousClose": bar["reference"], "quoteType": "EQUITY",
                           "exchangeTimezoneName": "America/New_York"})
        return {"quoteResponse": {"result": result, "error": None}}


def world(end="2026-10-13", bars=300):
    data = {}
    for index, symbol in enumerate(TW_SYMBOLS + US_SYMBOLS + ["^TWII", "SPY"]):
        closes = [50 * (1 + 0.0005 * (index + 1)) ** step for step in range(bars)]
        data[symbol] = frame(closes, end=end, volumes=[1e6 + index] * bars)
    # HALT：最後一根 K 是 9 月底（停牌）
    data["HALT"] = data["HALT"][data["HALT"].index <= "2026-09-30"]
    return data


def next_bar(data, day, close=None, reference=None):
    last = float(data["Close"].iloc[-1])
    close = close if close is not None else last * 1.01
    return {"date": day, "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
            "volume": 2e6, "reference": reference if reference is not None else last}


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self.tmp.name, "cache")
        self.report = os.path.join(self.tmp.name, "report")
        os.makedirs(self.report)
        self.saved = (dict(bu.MIN_LIST), bu.REQUEST_DELAY, bu.QUOTE_DELAY, bu.RATE_LIMIT_WAITS)
        bu.MIN_LIST.update({"TWSE": 2, "TPEx": 1, "US": 3})
        bu.REQUEST_DELAY = 0.0
        bu.QUOTE_DELAY = 0.0
        bu.RATE_LIMIT_WAITS = (1, 1, 1)
        # main.py 的流動性名單已經在檔案裡
        liquid = report_metrics.build_universe_payload({}, report_date="2026-10-13")
        liquid["markets"]["US"]["rows"] = [["AAPL", "Apple"] + [None] * 20]
        liquid["markets"]["US"]["asOf"] = "2026-10-13"
        report_metrics.write_universe_json(liquid, self.report)

    def tearDown(self):
        bu.MIN_LIST.clear()
        bu.MIN_LIST.update(self.saved[0])
        bu.REQUEST_DELAY, bu.QUOTE_DELAY, bu.RATE_LIMIT_WAITS = self.saved[1:]
        self.tmp.cleanup()

    def run_job(self, net, budget=10_000, now=NOW, markets="TW,US"):
        args = bu.parse_args(["--cache-dir", self.cache, "--report-dir", self.report, "--budget", str(budget),
                              "--markets", markets])
        logs = []
        code, job = bu.run(args, net=net, now=now, clock=lambda: net.clock, sleep=net.sleep, log=logs.append)
        return code, job, logs

    def universe(self):
        with open(os.path.join(self.report, "universe.json"), encoding="utf-8") as file:
            return json.load(file)

    def test_cold_start_then_daily_increment(self):
        data = world()
        net = FakeNet(data)
        # 第一次：時間預算只夠抓幾檔 → 未達 90% 覆蓋，不寫入，保留 main.py 的名單
        code, job, logs = self.run_job(net, budget=5)
        self.assertEqual(code, 0)
        self.assertTrue(any("冷啟動中" in warning for warning in job.warnings))
        self.assertTrue(any("時間預算用完" in warning for warning in job.warnings))
        document = self.universe()
        self.assertNotIn("sector", document["fields"])
        self.assertEqual(document["markets"]["US"]["rows"][0][:2], ["AAPL", "Apple"])
        done_first = sum(uc.is_complete(entry) for entry in job.state["symbols"].values())
        self.assertGreater(done_first, 0)

        # 第二次：接著補完（已完成的不重抓）
        net.calls.clear()
        code, job, logs = self.run_job(net)
        self.assertEqual(code, 0, job.problems)
        full_fetches = [call for call in net.calls if call.startswith("chart:") and call.endswith(":2y")]
        self.assertEqual(len(full_fetches), len(TW_SYMBOLS) + len(US_SYMBOLS) - done_first + 2)  # +2 基準
        document = self.universe()
        self.assertEqual(document["fields"][-3:], ["sector", "industry", "marketCap"])
        tw, us_market = document["markets"]["TW"], document["markets"]["US"]
        self.assertEqual(tw["scope"], "full")
        self.assertEqual(tw["asOf"], "2026-10-13")
        self.assertEqual(tw["universe"], "台股上市＋上櫃普通股（上市 4、上櫃 2 檔）")
        self.assertEqual([row[0] for row in tw["rows"]], sorted(TW_SYMBOLS))
        row = dict(zip(document["fields"], tw["rows"][0]))
        self.assertEqual(row["name"], "N1101")
        self.assertEqual(row["industry"], "半導體業")
        self.assertIsNone(row["sector"])
        self.assertEqual(row["marketCap"], round(1_000_000 * row["close"]))
        self.assertIsNotNone(row["rs63"])
        self.assertEqual(tw["rows"][-1][document["fields"].index("industry")], "電子零組件業")
        self.assertIsNone(tw["rows"][-1][document["fields"].index("marketCap")])  # 上櫃沒有股數
        # 美股：HALT 停牌被排除並計數；ETF、測試代號不在名單
        self.assertEqual([row[0] for row in us_market["rows"]], ["AAPL", "BRK-B", "MSFT", "NVDA"])
        self.assertEqual(us_market["counts"]["stale"], 1)
        self.assertIn("近 5 個交易日無 K 線", us_market["note"])
        self.assertEqual(us_market["universe"], "美股 NYSE／NASDAQ／NYSE American 等交易所普通股與 ADR（5 檔）")
        row = dict(zip(document["fields"], us_market["rows"][0]))
        self.assertEqual((row["sector"], row["industry"]), ("Technology", "Software"))
        self.assertEqual(row["marketCap"], round(100_000 * row["close"]))  # Nasdaq 市值 ÷ 價格 × 我們的收盤
        # 前端解析器也接受（同一份欄位，RS 百分位在擴大後的名單內）
        ranks = [dict(zip(document["fields"], r))["rsRank"] for r in us_market["rows"]]
        self.assertTrue(all(rank is None for rank in ranks))  # 只有 4 檔 < RS_RANK_MIN_COUNT

        # 第三天：只用官方行情與批次報價接 K，不逐檔請求
        net.calls.clear()
        net.today_bars = {
            symbol: next_bar(data[symbol], "2026-10-14") for symbol in TW_SYMBOLS + US_SYMBOLS if symbol != "HALT"
        }
        later = datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc)
        code, job, logs = self.run_job(net, now=later)
        self.assertEqual(code, 0, job.problems)
        symbol_charts = [call for call in net.calls if call.startswith("chart:") and "^" not in call and "SPY" not in call]
        self.assertEqual(symbol_charts, ["chart:HALT:3mo"])  # 只有停牌、沒有當日行情的那檔
        self.assertEqual(job.stats["TW.appended"], len(TW_SYMBOLS))
        self.assertEqual(job.stats["US.appended"], 4)
        document = self.universe()
        self.assertEqual(document["markets"]["TW"]["asOf"], "2026-10-14")
        cached = uc.read_bars(uc.bars_path(self.cache, "2330.TW"))
        self.assertEqual(cached.index[-1].strftime("%Y-%m-%d"), "2026-10-14")
        self.assertEqual(len(cached), 301)

    def complete_cache(self, data):
        net = FakeNet(data)
        code, job, _ = self.run_job(net)
        self.assertEqual(code, 0, job.problems)
        net.calls.clear()
        return net

    def test_adjustment_triggers_full_refetch(self):
        data = world()
        net = self.complete_cache(data)
        # 2330 除權（參考價 −20%）；Yahoo 隔天才調整歷史
        last = float(data["2330.TW"]["Close"].iloc[-1])
        net.today_bars = {"2330.TW": next_bar(data["2330.TW"], "2026-10-14", close=last * 0.8, reference=last * 0.8)}
        adjusted = data["2330.TW"].copy()
        adjusted[["Open", "High", "Low", "Close"]] *= 0.8
        net.world["2330.TW"] = adjusted
        code, job, logs = self.run_job(net, now=datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc), markets="TW")
        self.assertEqual(code, 0, job.problems)
        self.assertIn("chart:2330.TW:2y", net.calls)
        self.assertTrue(any("2330.TW" in line and "整檔重抓" in line for line in logs))
        cached = uc.read_bars(uc.bars_path(self.cache, "2330.TW"))
        self.assertAlmostEqual(float(cached["Close"].iloc[-2]), round(last * 0.8, 4), places=3)
        self.assertEqual(cached.index[-1].strftime("%Y-%m-%d"), "2026-10-14")  # 官方當日 K 接在重抓的歷史後
        self.assertTrue(uc.suspect(job.state["symbols"]["2330.TW"], "2026-10-16"))

    def test_cash_dividend_keeps_todays_bar(self):
        # 現金除息：參考價比前收盤低 3%，Yahoo 的收盤歷史不調整 → 重抓後照樣接上官方當日 K
        data = world()
        net = self.complete_cache(data)
        last = float(data["1101.TW"]["Close"].iloc[-1])
        net.today_bars = {"1101.TW": next_bar(data["1101.TW"], "2026-10-14", close=last * 0.98, reference=last * 0.97)}
        code, job, _ = self.run_job(net, now=datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc), markets="TW")
        self.assertEqual(code, 0, job.problems)
        self.assertIn("chart:1101.TW:2y", net.calls)
        cached = uc.read_bars(uc.bars_path(self.cache, "1101.TW"))
        self.assertEqual(cached.index[-1].strftime("%Y-%m-%d"), "2026-10-14")
        self.assertAlmostEqual(float(cached["Close"].iloc[-2]), last, places=3)
        self.assertTrue(uc.suspect(job.state["symbols"]["1101.TW"], "2026-10-16"))  # 之後幾天仍會再驗證

    def test_verify_detects_split_in_history(self):
        data = world()
        net = self.complete_cache(data)
        state = uc.load_state(self.cache)
        for entry in state["symbols"].values():
            entry["verified"] = "2026-01-01"
        uc.save_state(self.cache, state)
        split = data["NVDA"].copy()
        split[["Open", "High", "Low", "Close"]] /= 10
        net.world["NVDA"] = split
        code, job, logs = self.run_job(net, markets="US")
        self.assertIn("chart:NVDA:3mo", net.calls)
        self.assertIn("chart:NVDA:2y", net.calls)
        cached = uc.read_bars(uc.bars_path(self.cache, "NVDA"))
        self.assertAlmostEqual(float(cached["Close"].iloc[0]), float(split["Close"].iloc[0]), places=3)

    def test_gap_fetches_recent_history(self):
        data = world()
        net = self.complete_cache({symbol: frame_.iloc[:-2] for symbol, frame_ in data.items()})
        net.world = data
        net.today_bars = {"1101.TW": next_bar(data["1101.TW"], "2026-10-14")}
        code, job, _ = self.run_job(net, now=datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc), markets="TW")
        self.assertIn("chart:1101.TW:3mo", net.calls)
        cached = uc.read_bars(uc.bars_path(self.cache, "1101.TW"))
        self.assertEqual([day.strftime("%Y-%m-%d") for day in cached.index[-3:]], ["2026-10-12", "2026-10-13", "2026-10-14"])

    def test_zero_volume_stock_is_excluded(self):
        data = world()
        data["8069.TWO"]["Volume"] = 0.0
        self.complete_cache(data)
        document = self.universe()
        self.assertNotIn("8069.TWO", [row[0] for row in document["markets"]["TW"]["rows"]])
        self.assertEqual(document["markets"]["TW"]["counts"]["noVolume"], 1)
        self.assertIn("近 20 日零成交 1 檔", document["markets"]["TW"]["note"])

    def test_list_failure_uses_cache_then_refuses(self):
        data = world()
        net = self.complete_cache(data)
        before = self.universe()
        # 官方公司資料與 NasdaqTrader 失敗：用上次名單（警告，不是錯誤）
        net.fail = {"t187ap03_L", "t187ap03_O", "nasdaqlisted"}
        code, job, logs = self.run_job(net)
        self.assertEqual(code, 0, job.problems)
        self.assertTrue(any("t187ap03_L" in warning or "TWSE 公司基本資料" in warning for warning in job.warnings))
        self.assertEqual(len(job.listing["TW"]), len(TW_SYMBOLS))
        self.assertEqual(job.list_info["US"]["source"], "nasdaq screener")
        before = self.universe()
        # 名單太久沒更新、也沒有當日行情與 screener：該市場不更新、以錯誤結束，檔案保留原內容
        lists = uc.read_json(os.path.join(self.cache, "lists.json"))
        for key in ("TWSE", "TPEx", "US", "USProfiles"):
            lists[key]["fetchedAt"] = "2026-01-01"
        uc.write_json(os.path.join(self.cache, "lists.json"), lists)
        net.fail = {"t187ap03_L", "t187ap03_O", "nasdaqlisted", "screener", "twse", "tpex"}
        code, job, logs = self.run_job(net)
        self.assertEqual(code, 1)
        self.assertEqual(len(job.problems), 2)
        self.assertTrue(any(line.startswith("::error::") for line in logs))
        after = self.universe()
        self.assertEqual(after["markets"], before["markets"])

    def test_count_anomaly_is_not_used(self):
        bu.MIN_LIST.update({"US": 10})  # NasdaqTrader 只有 5 檔 → 異常
        net = FakeNet(world())
        code, job, logs = self.run_job(net, markets="US")
        self.assertEqual(code, 1)
        self.assertTrue(any("只有 5 檔" in warning for warning in job.warnings))
        self.assertEqual(self.universe()["markets"]["US"]["rows"][0][:2], ["AAPL", "Apple"])

    def test_one_market_failing_keeps_the_other(self):
        data = world()
        self.complete_cache(data)
        before = self.universe()
        net = FakeNet(data, fail={"nasdaqlisted", "screener"})
        lists = uc.read_json(os.path.join(self.cache, "lists.json"))
        lists["US"]["fetchedAt"] = "2026-01-01"
        uc.write_json(os.path.join(self.cache, "lists.json"), lists)
        code, job, logs = self.run_job(net)
        self.assertEqual(code, 1)
        after = self.universe()
        self.assertEqual(after["markets"]["US"], before["markets"]["US"])
        self.assertEqual(after["markets"]["TW"]["scope"], "full")

    def test_yahoo_rate_limit_stops_cleanly(self):
        data = world()

        class Limited(FakeNet):
            def chart(self, symbol, range_):
                self.calls.append(f"chart:{symbol}:{range_}")
                raise bu.RateLimited(429, symbol)

        net = Limited(data)
        code, job, logs = self.run_job(net)
        self.assertEqual(code, 0)
        self.assertTrue(any("限流" in warning for warning in job.warnings))
        self.assertTrue(os.path.exists(os.path.join(self.cache, "state.json")))

    def test_bad_symbol_does_not_stop_the_run(self):
        data = world()
        del data["MSFT"]
        net = FakeNet(data)
        code, job, logs = self.run_job(net)
        self.assertEqual(code, 0, job.problems)
        self.assertEqual(job.state["symbols"]["MSFT"]["fails"], 1)
        document = self.universe()
        # 4/5 = 80% < 90%：美股不寫入（保留 main.py 的名單）
        self.assertTrue(any("US 冷啟動中：完整歷史 4/5" in warning for warning in job.warnings))
        self.assertNotEqual(document["markets"]["US"].get("scope"), "full")
        self.assertEqual(document["markets"]["TW"]["scope"], "full")

    def test_same_day_check_is_logged(self):
        # 冷啟動時官方當日行情與 Yahoo 日 K 同一天：記錄收盤／成交量是否一致（Actions 日誌的口徑檢查）
        data = world()
        today = {symbol: {"date": "2026-10-13", "open": float(frame_["Open"].iloc[-1]),
                          "high": float(frame_["High"].iloc[-1]), "low": float(frame_["Low"].iloc[-1]),
                          "close": float(frame_["Close"].iloc[-1]), "volume": float(frame_["Volume"].iloc[-1]) * 2,
                          "reference": float(frame_["Close"].iloc[-2])}
                 for symbol, frame_ in data.items() if symbol in TW_SYMBOLS}
        net = FakeNet(data, today_bars=today)
        code, job, logs = self.run_job(net, markets="TW")
        self.assertEqual(code, 0, job.problems)
        self.assertEqual(bu.agreement_summary(job.agreement)["TW"],
                         {"samples": len(TW_SYMBOLS), "closeOff": 0, "volumeRatioMedian": 2.0})
        self.assertTrue(any('"sameDayCheck"' in line for line in logs))

    def test_unchanged_output_is_not_rewritten(self):
        data = world()
        self.complete_cache(data)
        path = os.path.join(self.report, "universe.json")
        stamp = os.path.getmtime(path)
        os.utime(path, (stamp - 100, stamp - 100))
        args = bu.parse_args(["--offline", "--cache-dir", self.cache, "--report-dir", self.report])
        code, job = bu.run(args, log=lambda *a: None)
        self.assertEqual(code, 0)
        self.assertEqual(os.path.getmtime(path), stamp - 100)


# =========================================================================
# main.py 與全市場檔共存
# =========================================================================
def full_market(as_of, rows=12):
    return {
        "benchmark": "SPY", "asOf": as_of, "universe": "美股…", "scope": "full",
        "rows": [[f"S{i}", f"S{i}"] + [None] * 16 + [float(i), None, None, None, as_of, None, "Tech", "Software", 1000 * i]
                 for i in range(rows)],
    }


class CoexistenceTest(unittest.TestCase):
    def existing(self, as_of="2026-10-13"):
        return {
            "version": 1, "generatedAt": "x", "reportDate": "2026-10-13",
            "fields": bu.FULL_FIELDS,
            "markets": {"US": full_market(as_of)},
        }

    def liquid(self):
        payload = report_metrics.build_universe_payload({}, report_date="2026-10-14")
        payload["markets"]["TW"]["asOf"] = "2026-10-14"
        payload["markets"]["TW"]["rows"] = [["2330.TW", "台積電"] + [None] * 20]
        payload["markets"]["US"]["asOf"] = "2026-10-14"
        payload["markets"]["US"]["rows"] = [["AAPL", "Apple"] + [None] * 20]
        return payload

    def test_main_keeps_fresh_full_market(self):
        combined, kept = report_metrics.keep_fresh_full_markets(self.liquid(), self.existing())
        self.assertEqual(kept, ["US"])
        self.assertEqual(combined["fields"], bu.FULL_FIELDS)
        self.assertEqual(combined["markets"]["US"]["scope"], "full")
        self.assertEqual(len(combined["markets"]["US"]["rows"]), 12)
        # 流動性名單的列補齊到完整欄位
        self.assertEqual(combined["markets"]["TW"]["rows"][0][:2], ["2330.TW", "台積電"])
        self.assertEqual(len(combined["markets"]["TW"]["rows"][0]), len(bu.FULL_FIELDS))
        self.assertEqual(combined["markets"]["TW"]["scope"], "liquid")
        self.assertEqual(combined["reportDate"], "2026-10-14")

    def test_main_replaces_stale_full_market(self):
        combined, kept = report_metrics.keep_fresh_full_markets(self.liquid(), self.existing("2026-10-01"))
        self.assertEqual(kept, [])
        self.assertEqual(combined["fields"], list(report_metrics.UNIVERSE_FIELDS))
        self.assertEqual(combined["markets"]["US"]["rows"][0][:2], ["AAPL", "Apple"])

    def test_main_without_existing_file(self):
        combined, kept = report_metrics.keep_fresh_full_markets(self.liquid(), None)
        self.assertEqual(kept, [])
        self.assertEqual(combined["markets"]["TW"]["rows"][0][0], "2330.TW")

    def test_remap_by_field_name(self):
        existing = self.existing()
        # 欄位順序不同（舊版檔案）也依名稱對應
        existing["fields"] = list(reversed(existing["fields"]))
        existing["markets"]["US"]["rows"] = [list(reversed(row)) for row in existing["markets"]["US"]["rows"]]
        combined, _ = report_metrics.keep_fresh_full_markets(self.liquid(), existing)
        row = dict(zip(combined["fields"], combined["markets"]["US"]["rows"][3]))
        self.assertEqual((row["symbol"], row["rs63"], row["industry"], row["marketCap"]), ("S3", 3.0, "Software", 3000))

    def test_rs_rank_uses_full_distribution(self):
        universes = {
            "US": {"asOf": "2026-10-14", "records": [{"metrics": {"rs63": 5.5}}, {"metrics": {"rs63": None}}]},
            "TW": {"asOf": "2026-10-14", "records": [{"metrics": {"rs63": 1.0, "rsRank": 50}}]},
        }
        adopted = report_metrics.adopt_full_rs_distribution(universes, self.existing())
        self.assertEqual(adopted, ["US"])
        # 全市場 rs63 = 0..11；5.5 在中間
        self.assertEqual(universes["US"]["records"][0]["metrics"]["rsRank"], 50)
        self.assertIsNone(universes["US"]["records"][1]["metrics"]["rsRank"])
        self.assertEqual(universes["US"]["rsDistribution"], [float(i) for i in range(12)])
        self.assertEqual(universes["TW"]["records"][0]["metrics"]["rsRank"], 50)
        self.assertEqual(report_metrics.adopt_full_rs_distribution(universes, self.existing("2026-09-01")), [])

    def test_main_write_report_universe_keeps_full(self):
        from test_report_metrics import import_main

        main = import_main()
        with tempfile.TemporaryDirectory() as out:
            report_metrics.write_universe_json(self.existing(), out)
            universes = {
                "TW": {"benchmark": "^TWII", "asOf": "2026-10-14", "records": []},
                "US": {"benchmark": "SPY", "asOf": "2026-10-14", "records": []},
            }
            main.write_report_universe(universes, "2026-10-14", report_dir=out)
            document = report_metrics.read_universe_json(out)
            self.assertEqual(document["markets"]["US"]["scope"], "full")
            self.assertEqual(len(document["markets"]["US"]["rows"]), 12)
            self.assertEqual(document["markets"]["TW"]["rows"], [])
            self.assertEqual(document["reportDate"], "2026-10-14")


if __name__ == "__main__":
    unittest.main()


class NetworkTransportRetryTest(unittest.TestCase):
    """A transport error (curl's "HTTP/2 stream reset", seen on TPEx) is retried once over plain HTTP/1.1."""

    class Response:
        def __init__(self, status=200, body="[]"):
            self.status_code = status
            self.text = body

        def json(self):
            return json.loads(self.text)

    class Session:
        def __init__(self, outcome):
            self.outcome = outcome
            self.calls = 0

        def get(self, url, params=None, headers=None, timeout=30):
            self.calls += 1
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    def network(self, primary, plain):
        net = bu.Network.__new__(bu.Network)
        net.session = primary
        net.plain = plain
        net.crumb = None
        return net

    def test_transport_error_falls_back_to_http11_once(self):
        primary = self.Session(RuntimeError("curl: (92) HTTP/2 stream 5 reset by server"))
        plain = self.Session(self.Response(200, '[{"a": 1}]'))
        self.assertEqual(self.network(primary, plain).get_json("https://www.tpex.org.tw/x"), [{"a": 1}])
        self.assertEqual((primary.calls, plain.calls), (1, 1))

    def test_http_errors_and_rate_limits_are_not_retried_over_http11(self):
        plain = self.Session(self.Response(200))
        with self.assertRaises(bu.RateLimited):
            self.network(self.Session(self.Response(429)), plain).get("https://query1.finance.yahoo.com/x")
        with self.assertRaises(bu.HttpError):
            self.network(self.Session(self.Response(503)), plain).get("https://www.tpex.org.tw/x")
        self.assertEqual(plain.calls, 0)
