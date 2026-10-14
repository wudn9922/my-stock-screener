"""台股籌碼（scripts/build_tw_chips.py）的離線測試：只用 tests/fixtures/chips 的假資料，不連網路。"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_tw_chips as chips  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "chips")


def read_bytes(name):
    with open(os.path.join(FIXTURES, name), "rb") as file:
        return file.read()


def read_text(path):
    with open(path, encoding="utf-8") as file:
        return file.read()


def read_raw(path):
    with open(path, "rb") as file:
        return file.read()


def read_json(name):
    return json.loads(read_bytes(name).decode("utf-8"))


class ToNumberAndDateTests(unittest.TestCase):
    def test_to_number_handles_commas_percent_and_blanks(self):
        self.assertEqual(chips.to_number("1,234,567"), 1234567.0)
        self.assertEqual(chips.to_number(" 73.27% "), 73.27)
        self.assertEqual(chips.to_number("１２"), 12.0)  # 全形數字
        self.assertEqual(chips.to_number(5), 5.0)
        for blank in ("--", "-", "", "  ", "N/A", "X", None, "abc", float("nan"), float("inf"), True):
            self.assertIsNone(chips.to_number(blank), repr(blank))

    def test_parse_date_formats(self):
        for text in ("20251014", "2025-10-14", "2025/10/14", "1141014", "114/10/14", "114年10月14日", "114年10月14日 外資及陸資投資持股統計"):
            self.assertEqual(chips.parse_date(text), "2025-10-14", text)
        for bad in (None, "", "20251340", "abc", "99/01/01x", "00000000"):
            self.assertIsNone(chips.parse_date(bad), repr(bad))

    def test_decode_handles_bom_and_big5(self):
        self.assertEqual(chips.decode_payload("﻿abc"), "abc")
        self.assertEqual(chips.decode_payload("﻿資料".encode("utf-8")), "資料")
        self.assertEqual(chips.decode_payload("證券代號".encode("big5hkscs")), "證券代號")


class ParseQfiiTests(unittest.TestCase):
    def test_twse_fixture(self):
        result = chips.parse_qfii(read_bytes("twse_qfii_ok.json"))
        self.assertEqual(result.status, "ok", result.message)
        self.assertEqual(result.date, "2025-10-14")
        self.assertEqual(result.rows["2330"], 73.27)
        self.assertEqual(result.rows["1101"], 14.29)
        self.assertNotIn("9999", result.rows)  # `--` 沒有資料
        self.assertEqual(result.rows["0050"], 0.0)  # 0.00% 是真的 0，不是缺值

    def test_picks_holding_ratio_not_investable_ratio(self):
        # 「外資及陸資尚可投資比率」(=100-持股) 不能被誤抓
        result = chips.parse_qfii(read_bytes("twse_qfii_ok.json"))
        self.assertNotEqual(result.rows["2330"], 26.73)

    def test_stat_not_ok_is_nodata_or_error(self):
        nodata = chips.parse_qfii(read_bytes("twse_qfii_nodata.json"))
        self.assertEqual(nodata.status, "nodata")
        limited = chips.parse_qfii(json.dumps({"stat": "查詢過於頻繁，請稍後再試"}))
        self.assertEqual(limited.status, "error")  # 被限流不是「沒有資料」，不能當成假日略過
        weird = chips.parse_qfii(json.dumps({"stat": "Internal error 500"}))
        self.assertEqual(weird.status, "error")
        self.assertIn("500", weird.message)

    def test_empty_html_and_garbage_are_errors(self):
        for body in (b"", b"   ", b"<!doctype html><title>blocked</title>", b"not json", b"[]", b"{}", b'{"stat":"OK"}'):
            result = chips.parse_qfii(body)
            self.assertIn(result.status, ("error", "nodata"), body)
            self.assertNotEqual(result.status, "ok")
        self.assertEqual(chips.parse_qfii(b"").status, "error")
        self.assertEqual(chips.parse_qfii(b"<html></html>").status, "error")
        self.assertEqual(chips.parse_qfii(b'{"stat":"OK","fields":["a"],"data":[]}').status, "nodata")

    def test_bom_prefixed_json(self):
        body = b"\xef\xbb\xbf" + read_bytes("twse_qfii_ok.json")
        self.assertEqual(chips.parse_qfii(body).status, "ok")

    def test_missing_ratio_column_falls_back_to_shares(self):
        data = read_json("twse_qfii_ok.json")
        index = data["fields"].index("全體外資及陸資持股比率")
        data["fields"][index] = "某個改名後的欄位"
        result = chips.parse_qfii(data)
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.rows["2330"], 73.27, places=1)
        self.assertTrue(any("改用 持有股數/發行股數" in note for note in result.notes))

    def test_missing_everything_reports_actual_field_names(self):
        data = read_json("twse_qfii_ok.json")
        data["fields"] = ["代號", "名稱", "別的欄位"]
        result = chips.parse_qfii(data)
        self.assertEqual(result.status, "error")
        self.assertIn("別的欄位", result.message)

    def test_missing_code_column_reports_actual_field_names(self):
        data = read_json("twse_qfii_ok.json")
        data["fields"][0] = "Foo"
        result = chips.parse_qfii(data)
        self.assertEqual(result.status, "error")
        self.assertIn("Foo", result.message)
        self.assertIn("證券代號", result.message)

    def test_out_of_range_rows_dropped_but_mostly_bad_day_rejected(self):
        data = read_json("twse_qfii_ok.json")
        many = [list(data["data"][0]) for _ in range(1)]
        template = data["data"][1]
        rows = []
        for i in range(200):
            row = list(template)
            row[0] = f"{1000 + i}"
            rows.append(row)
        rows[0][7] = "150.00%"  # 一列異常 < 2%：只丟那一列
        data["data"] = rows
        result = chips.parse_qfii(data)
        self.assertEqual(result.status, "ok")
        self.assertNotIn("1000", result.rows)
        self.assertEqual(len(result.rows), 199)
        for row in rows[:20]:
            row[7] = "150.00%"  # 超過 2%：整天不採用
        self.assertEqual(chips.parse_qfii(data).status, "error")
        del many

    def test_tpex_legacy_positional_layout(self):
        result = chips.parse_qfii(read_bytes("tpex_qfii_legacy.json"), "櫃買")
        self.assertEqual(result.status, "ok", result.message)
        self.assertEqual(result.date, "2025-10-14")
        self.assertEqual(result.rows["6488"], 22.99)
        self.assertEqual(result.rows["5483"], 10.0)
        self.assertTrue(any("位置推測" in note for note in result.notes))

    def test_tpex_positional_mismatch_is_rejected(self):
        data = read_json("tpex_qfii_legacy.json")
        for row in data["aaData"]:
            row[6] = "55.55"  # 與 持有/發行 不吻合
        result = chips.parse_qfii(data)
        self.assertEqual(result.status, "error")
        self.assertIn("位置推測驗算失敗", result.message)

    def test_tpex_tables_with_fields_and_nodata(self):
        result = chips.parse_qfii(read_bytes("tpex_qfii_tables.json"))
        self.assertEqual(result.status, "ok", result.message)
        self.assertEqual(result.rows["8069"], 26.09)
        self.assertEqual(chips.parse_qfii(read_bytes("tpex_qfii_nodata.json")).status, "nodata")

    def test_openapi_list_of_dicts(self):
        body = [
            {"SecuritiesCompanyCode": "6488", "Name": "環球晶", "全體外資及陸資持股比率": "22.99"},
            {"SecuritiesCompanyCode": "8069", "Name": "元太", "全體外資及陸資持股比率": "--"},
        ]
        result = chips.parse_qfii(body)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.rows, {"6488": 22.99})


class ParseTdccTests(unittest.TestCase):
    def test_fixture_total_row_is_level_17(self):
        result = chips.parse_tdcc_holders(read_bytes("tdcc_1-5.csv"))
        self.assertEqual(result.status, "ok", result.message)
        self.assertEqual(result.date, "2025-10-10")
        self.assertEqual(result.rows["1101"], 270100)  # sum(base) == level 17 人數；level 16 的 0 人不影響
        self.assertEqual(result.rows["2330"], 270100 * 20)
        self.assertIn("00679B", result.rows)

    def test_wanted_filter(self):
        result = chips.parse_tdcc_holders(read_bytes("tdcc_1-5.csv"), wanted={"1101", "6488"})
        self.assertEqual(set(result.rows), {"1101", "6488"})

    def test_level_16_never_counted_in_fallback_sum(self):
        lines = ["資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%"]
        lines += [f"20251010,2330,{level},{level * 10},1,1" for level in range(1, 17)]
        result = chips.parse_tdcc_holders("\n".join(lines))  # 沒有第 17 級
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.rows["2330"], sum(level * 10 for level in range(1, 16)))
        self.assertTrue(any("沒有第 17 級" in note for note in result.notes))

    def test_thousand_separators_and_blank_values(self):
        text = '資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20251010,1101,17,"1,234,567","9,999",100.00\n20251010,2330,17,--,1,1\n20251010,2317,17,,1,1\n'
        result = chips.parse_tdcc_holders(text)
        self.assertEqual(result.rows, {"1101": 1234567})
        self.assertTrue(any("2 列" in note for note in result.notes))

    def test_empty_missing_columns_and_big5(self):
        self.assertEqual(chips.parse_tdcc_holders(b"").status, "error")
        self.assertEqual(chips.parse_tdcc_holders("﻿\n\n").status, "error")
        bad = chips.parse_tdcc_holders("日期,代號,人數量\n20251010,2330,5\n")
        self.assertEqual(bad.status, "error")
        self.assertIn("持股分級", bad.message)
        self.assertIn("人數量", bad.message)  # 印出實際欄位
        text = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20251010,2330,17,100,1,1\n"
        self.assertEqual(chips.parse_tdcc_holders(text.encode("big5hkscs")).rows, {"2330": 100})

    def test_unparsable_date_and_mixed_dates(self):
        text = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\nxxxx,2330,17,100,1,1\n"
        self.assertEqual(chips.parse_tdcc_holders(text).status, "error")
        mixed = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20251010,2330,17,100,1,1\n20251010,2317,17,100,1,1\n20251003,1101,17,100,1,1\n"
        result = chips.parse_tdcc_holders(mixed)
        self.assertEqual(result.date, "2025-10-10")
        self.assertTrue(any("多個資料日期" in note for note in result.notes))


class HistoryTests(unittest.TestCase):
    def test_add_is_idempotent_and_trims(self):
        history = chips.History()
        history.add("2025-10-02", {"2330": 70.0})
        history.add("2025-10-01", {"2330": 69.0, "1101": 10.0})
        history.add("2025-10-02", {"2330": 70.5})  # 同一天覆寫
        self.assertEqual(history.dates, ["2025-10-01", "2025-10-02"])
        self.assertEqual(history.series("2330"), [69.0, 70.5])
        self.assertEqual(history.series("1101"), [10.0, None])
        self.assertEqual(history.series("9999"), [None, None])
        history.trim(1)
        self.assertEqual(history.dates, ["2025-10-02"])
        self.assertNotIn("1101", history.values)

    def test_round_trip_through_payload(self):
        history = chips.History()
        for index, day in enumerate(["2025-10-01", "2025-10-02", "2025-10-03"]):
            history.add(day, {"2330": 70.0 + index})
        history.add("2025-10-03", {"2317": 41.0})
        entry = chips.foreign_entry(history.dates, history.series("2330"))
        restored = chips.history_from_payload(history.dates, {"2330.TW": {"foreign": entry}}, "foreign", lambda s: True)
        self.assertEqual(restored.dates, history.dates)
        self.assertEqual(restored.series("2330"), [70.0, 71.0, 72.0])
        # 長度不符的舊資料被略過而不是拋例外
        broken = chips.history_from_payload(history.dates, {"2330.TW": {"foreign": {"series": [1]}}}, "foreign", lambda s: True)
        self.assertEqual(broken.series("2330"), [None, None, None])
        self.assertEqual(chips.history_from_payload("oops", {}, "foreign", lambda s: True).dates, [])
        self.assertEqual(chips.history_from_payload(["2025-10-02", "2025-10-01"], {}, "foreign", lambda s: True).dates, [])


class ChangeTests(unittest.TestCase):
    def test_foreign_changes(self):
        dates = ["2025-09-01", "2025-09-12", "2025-10-13", "2025-10-14"]
        entry = chips.foreign_entry(dates, [60.0, 70.0, 71.0, 71.25])
        self.assertEqual(entry["pct"], 71.25)
        self.assertEqual(entry["asOf"], "2025-10-14")
        self.assertEqual((entry["d1"], entry["d1Date"]), (0.25, "2025-10-13"))
        # 30 天前 = 2025-09-14；其前最近一日是 09-12
        self.assertEqual((entry["m1"], entry["m1Date"]), (1.25, "2025-09-12"))

    def test_foreign_month_change_needs_enough_history(self):
        entry = chips.foreign_entry(["2025-10-13", "2025-10-14"], [71.0, 71.5])
        self.assertIsNone(entry["m1"])
        self.assertEqual(entry["d1"], 0.5)
        single = chips.foreign_entry(["2025-10-14"], [71.5])
        self.assertIsNone(single["d1"])
        self.assertIsNone(chips.foreign_entry(["2025-10-14"], [None]))

    def test_foreign_skips_missing_days(self):
        entry = chips.foreign_entry(["2025-10-10", "2025-10-13", "2025-10-14"], [70.0, None, 70.5])
        self.assertEqual((entry["d1"], entry["d1Date"]), (0.5, "2025-10-10"))

    def test_holders_week_over_week(self):
        dates = ["2025-09-26", "2025-10-03", "2025-10-10"]
        entry = chips.holders_entry(dates, [1000, 1100, 1050])
        self.assertEqual((entry["count"], entry["wow"], entry["wowDate"]), (1050, -50, "2025-10-03"))
        self.assertTrue(all(isinstance(value, int) for value in entry["series"]))
        stale_gap = chips.holders_entry(["2025-08-01", "2025-10-10"], [1000, 1050])
        self.assertIsNone(stale_gap["wow"])  # 隔太久不是「較上週」
        self.assertIsNone(chips.holders_entry(dates, [None, None, None]))


class TargetsAndValidationTests(unittest.TestCase):
    def test_weekday_targets_skip_weekends_and_existing(self):
        targets = chips.weekday_targets(date(2025, 10, 14), 7, ["2025-10-13"])
        self.assertEqual([day.isoformat() for day in targets], ["2025-10-07", "2025-10-08", "2025-10-09", "2025-10-10", "2025-10-14"])

    def _payload(self):
        foreign = {"twse": chips.History(), "tpex": chips.History()}
        foreign["twse"].add("2025-10-14", {"2330": 73.0, "1101": 14.0})
        holders = chips.History()
        holders.add("2025-10-10", {"2330": 1000, "1101": 500})
        return chips.build_payload(["1101.TW", "2330.TW"], foreign, holders, {}, "2025-10-14T00:00:00+00:00")

    def test_valid_payload_passes_and_serializes_as_json(self):
        payload = self._payload()
        self.assertEqual(chips.validate_payload(payload, 2), [])
        text = chips.serialize_payload(payload)
        self.assertEqual(json.loads(text), json.loads(json.dumps(payload)))
        self.assertGreaterEqual(text.count("\n"), 3)  # 每檔一行

    def test_invalid_payloads_are_rejected(self):
        payload = self._payload()
        bad = copy.deepcopy(payload)
        bad["stocks"]["2330.TW"]["foreign"]["series"] = [101.0]
        self.assertTrue(any("0–100" in problem for problem in chips.validate_payload(bad, 2)))
        bad = copy.deepcopy(payload)
        bad["stocks"]["2330.TW"]["foreign"]["series"] = [73.0, 74.0]
        self.assertTrue(any("長度" in problem for problem in chips.validate_payload(bad, 2)))
        bad = copy.deepcopy(payload)
        bad["stocks"]["2330.TW"]["holders"]["series"] = [-5]
        self.assertTrue(any("非負整數" in problem for problem in chips.validate_payload(bad, 2)))
        bad = copy.deepcopy(payload)
        bad["stocks"]["2330.TW"]["holders"]["series"] = [1.5]
        self.assertTrue(chips.validate_payload(bad, 2))
        self.assertTrue(chips.validate_payload(payload, 100))  # 涵蓋率太低
        bad = copy.deepcopy(payload)
        bad["stocks"] = {}
        self.assertTrue(chips.validate_payload(bad, 2))
        bad = copy.deepcopy(payload)
        bad["axes"]["holders"] = ["2025-10-11", "2025-10-10"]
        self.assertTrue(chips.validate_payload(bad, 2))
        self.assertTrue(chips.validate_payload({"version": 99}, 2))


class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FetcherTests(unittest.TestCase):
    def make(self, responses, **kwargs):
        sleeps = []
        clock = [0.0]
        session = FakeSession(responses)
        fetcher = chips.Fetcher(session=session, sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)), clock=lambda: clock[0], **kwargs)
        return fetcher, session, sleeps

    def test_retries_then_succeeds_with_user_agent_and_delay(self):
        fetcher, session, sleeps = self.make([TimeoutError("slow"), FakeResponse(503), FakeResponse(200, b"ok")], delay=1.5)
        self.assertEqual(fetcher.get("https://example.test/a"), b"ok")
        self.assertEqual(len(session.calls), 3)
        self.assertIn("User-Agent", session.calls[0][1])
        self.assertTrue(all(s > 0 for s in sleeps) and sleeps)

    def test_requests_are_spaced(self):
        fetcher, session, sleeps = self.make([FakeResponse(200, b"1"), FakeResponse(200, b"2")], delay=2.0)
        fetcher.get("u1")
        fetcher.get("u2")
        self.assertEqual(sleeps, [2.0])

    def test_4xx_is_http_error_without_retry_and_exhausted_retries_is_network(self):
        fetcher, session, _ = self.make([FakeResponse(404)])
        with self.assertRaises(chips.FetchError) as caught:
            fetcher.get("u")
        self.assertEqual(caught.exception.kind, "http")
        self.assertEqual(len(session.calls), 1)
        fetcher, session, _ = self.make([FakeResponse(500)] * 3, retries=3)
        with self.assertRaises(chips.FetchError) as caught:
            fetcher.get("u")
        self.assertEqual(caught.exception.kind, "network")
        self.assertEqual(len(session.calls), 3)


class Args:
    def __init__(self, root, **overrides):
        self.data_dir = os.path.join(root, "data")
        self.financials_dir = os.path.join(root, "fin")
        self.out = os.path.join(root, "out", "tw-chips.json")
        self.lookback_days = 3
        self.delay = 0
        self.timeout = 5
        self.retries = 1
        self.tpex_url = None
        self.dry_run = False
        self.__dict__.update(overrides)


def make_site(root, symbols=("1101.TW", "2330.TW", "2317.TW", "1216.TW", "6488.TWO", "8069.TWO")):
    os.makedirs(os.path.join(root, "data"))
    os.makedirs(os.path.join(root, "fin"))
    for symbol in symbols:
        with open(os.path.join(root, "data", f"{symbol}.csv"), "w") as file:
            file.write("Date,Open,High,Low,Close,Volume\n")
    # 不收：ETF（0 開頭）、指數、6 碼
    for name in ("0050.TW.csv", "^TWII.csv", "AAPL.csv", "00679B.TWO.csv"):
        with open(os.path.join(root, "data", name), "w") as file:
            file.write("x")
    with open(os.path.join(root, "fin", "2454.TW.json"), "w") as file:  # financials 內有、data 內沒有的也收
        file.write("{}")


def fixture_fetch(day_iso="2025-10-14", twse_ok=True, tpex_ok=True, tdcc_ok=True, mutate=None, calls=None):
    """依網址回傳 fixture 的假抓取函式；只有 day_iso 有資料，其餘日期回「無資料」。"""
    ymd = day_iso.replace("-", "")
    roc = f"{int(day_iso[:4]) - 1911}/{day_iso[5:7]}/{day_iso[8:]}"

    def fetch(url):
        if calls is not None:
            calls.append(url)
        if "MI_QFIIS" in url:
            if not twse_ok:
                raise chips.FetchError("HTTP 503（模擬）", kind="network")
            if f"date={ymd}" not in url:
                return read_bytes("twse_qfii_nodata.json")
            data = read_json("twse_qfii_ok.json")
            data["date"] = ymd
            if mutate:
                mutate("twse", data)
            return json.dumps(data, ensure_ascii=False).encode()
        if "qfii_result" in url:
            if not tpex_ok:
                raise chips.FetchError("HTTP 404（模擬）", kind="http")
            if f"d={roc}" not in url:
                return read_bytes("tpex_qfii_nodata.json")
            data = read_json("tpex_qfii_legacy.json")
            data["reportDate"] = roc
            return json.dumps(data, ensure_ascii=False).encode()
        if "www/zh-tw/insti/qfii" in url:
            raise chips.FetchError("HTTP 404（模擬）", kind="http")
        if "opendata.tdcc.com.tw" in url:
            if not tdcc_ok:
                raise chips.FetchError("HTTP 503（模擬）", kind="network")
            text = read_bytes("tdcc_1-5.csv").decode("utf-8")
            if mutate:
                holder = {"text": text}
                mutate("tdcc", holder)
                text = holder["text"]
            return text.encode("utf-8")
        raise AssertionError(f"unexpected url {url}")

    return fetch


NOW = datetime(2025, 10, 14, 12, 0, tzinfo=timezone.utc)  # 台北 20:00


class RunTests(unittest.TestCase):
    def run_main(self, root, fetch, now=NOW, **overrides):
        buffer = io.StringIO()
        summary = os.path.join(root, "summary.md")
        with contextlib.redirect_stdout(buffer):
            code = chips.run(Args(root, **overrides), fetch=fetch, now=now, env={"GITHUB_STEP_SUMMARY": summary})
        summary_text = read_text(summary) if os.path.exists(summary) else ""
        return code, buffer.getvalue(), summary_text

    def load(self, root):
        with open(os.path.join(root, "out", "tw-chips.json"), encoding="utf-8") as file:
            return json.load(file)

    def test_universe_rules(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            universe = chips.load_universe(os.path.join(root, "data"), os.path.join(root, "fin"))
            self.assertEqual(universe, ["1101.TW", "1216.TW", "2317.TW", "2330.TW", "2454.TW", "6488.TWO", "8069.TWO"])

    def test_full_run_writes_expected_structure(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root, symbols=("1101.TW", "2330.TW", "2317.TW", "1216.TW", "6488.TWO", "8069.TWO"))
            os.remove(os.path.join(root, "fin", "2454.TW.json"))
            code, out, summary = self.run_main(root, fixture_fetch())
            self.assertEqual(code, 0, out)
            payload = self.load(root)
            self.assertEqual(payload["version"], 1)
            self.assertEqual({key: payload["sources"][key]["status"] for key in ("twse", "tpex", "tdcc")}, {"twse": "ok", "tpex": "ok", "tdcc": "ok"})
            self.assertEqual(payload["sources"]["twse"]["asOf"], "2025-10-14")
            self.assertEqual(payload["sources"]["tdcc"]["asOf"], "2025-10-10")
            self.assertEqual(payload["axes"]["foreign"]["twse"], ["2025-10-14"])
            tsmc = payload["stocks"]["2330.TW"]
            self.assertEqual(tsmc["foreign"]["pct"], 73.27)
            self.assertEqual(tsmc["foreign"]["series"], [73.27])
            self.assertEqual(tsmc["holders"]["count"], 270100 * 20)
            self.assertEqual(payload["stocks"]["6488.TWO"]["foreign"]["pct"], 22.99)
            self.assertNotIn("holders", payload["stocks"]["2317.TW"])  # TDCC fixture 沒有這檔
            self.assertNotIn("0050.TW", payload["stocks"])
            self.assertIn("| 證交所 | ok | 2025-10-14 |", summary)
            self.assertNotIn("::warning::", out)

    def test_unchanged_second_run_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            self.run_main(root, fixture_fetch())
            path = os.path.join(root, "out", "tw-chips.json")
            before = read_raw(path)
            later = datetime(2025, 10, 14, 13, 0, tzinfo=timezone.utc)
            code, out, summary = self.run_main(root, fixture_fetch(), now=later)
            self.assertEqual(code, 0)
            self.assertEqual(read_raw(path), before)
            self.assertIn("內容沒有變動", out)

    def test_accumulates_new_day_and_week_without_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            self.run_main(root, fixture_fetch("2025-10-13"), now=datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc))
            first = self.load(root)
            self.assertEqual(first["axes"]["foreign"]["twse"], ["2025-10-13"])

            def bump(kind, data):
                if kind == "twse":
                    for row in data["data"]:
                        if row[0] == "2330":
                            row[7] = "73.50%"
                elif kind == "tdcc":
                    data["text"] = data["text"].replace("20251010", "20251017")

            self.run_main(root, fixture_fetch("2025-10-14", mutate=bump), now=datetime(2025, 10, 20, 2, 0, tzinfo=timezone.utc), lookback_days=7)
            second = self.load(root)
            self.assertEqual(second["axes"]["foreign"]["twse"], ["2025-10-13", "2025-10-14"])
            tsmc = second["stocks"]["2330.TW"]
            self.assertEqual(tsmc["foreign"]["series"], [73.27, 73.5])
            self.assertEqual(tsmc["foreign"]["d1"], 0.23)
            self.assertEqual(second["axes"]["holders"], ["2025-10-10", "2025-10-17"])
            self.assertEqual(tsmc["holders"]["wow"], 0)
            # 同一週再跑一次：不新增
            self.run_main(root, fixture_fetch("2025-10-14", mutate=bump), now=datetime(2025, 10, 20, 3, 0, tzinfo=timezone.utc), lookback_days=7)
            self.assertEqual(self.load(root)["axes"]["holders"], ["2025-10-10", "2025-10-17"])

    def test_tpex_failure_does_not_block_twse_and_tdcc(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            calls = []
            code, out, summary = self.run_main(root, fixture_fetch(tpex_ok=False, calls=calls))
            self.assertEqual(code, 0)
            payload = self.load(root)
            self.assertEqual(payload["sources"]["twse"]["status"], "ok")
            self.assertEqual(payload["sources"]["tpex"]["status"], "unavailable")
            self.assertEqual(payload["sources"]["tdcc"]["status"], "ok")
            self.assertNotIn("foreign", payload["stocks"]["6488.TWO"])
            self.assertIn("holders", payload["stocks"]["6488.TWO"])
            self.assertIn("foreign", payload["stocks"]["2330.TW"])
            self.assertIn("::warning::", out)
            self.assertIn("| 櫃買中心 | unavailable |", summary)

    def test_failing_source_keeps_previous_fields(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            self.run_main(root, fixture_fetch())
            later = datetime(2025, 10, 15, 12, 0, tzinfo=timezone.utc)
            code, out, summary = self.run_main(root, fixture_fetch(twse_ok=False, tdcc_ok=False), now=later)
            self.assertEqual(code, 0)
            payload = self.load(root)
            self.assertEqual(payload["sources"]["twse"]["status"], "stale")
            self.assertEqual(payload["sources"]["twse"]["asOf"], "2025-10-14")
            self.assertEqual(payload["sources"]["tdcc"]["status"], "stale")
            self.assertEqual(payload["stocks"]["2330.TW"]["foreign"]["pct"], 73.27)
            self.assertEqual(payload["stocks"]["2330.TW"]["holders"]["count"], 270100 * 20)
            self.assertEqual(payload["sources"]["tpex"]["status"], "ok")
            self.assertIn("::warning::", out)

    def test_invalid_output_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            self.run_main(root, fixture_fetch())
            path = os.path.join(root, "out", "tw-chips.json")
            before = read_raw(path)

            def poison(kind, data):
                if kind == "tdcc":
                    data["text"] = "資料日期,證券代號,持股分級,人數\n20251017,2330,17,5\n"  # 涵蓋率不足

            code, out, _ = self.run_main(root, fixture_fetch(mutate=poison), now=datetime(2025, 10, 20, 2, 0, tzinfo=timezone.utc), lookback_days=7)
            self.assertEqual(code, 0)
            payload = self.load(root)
            self.assertEqual(payload["sources"]["tdcc"]["status"], "stale")
            self.assertEqual(payload["stocks"]["2330.TW"]["holders"]["count"], 270100 * 20)
            self.assertEqual(payload["stocks"], json.loads(before)["stocks"])  # 失敗的來源保留舊欄位，狀態改標 stale

    def test_everything_failing_without_old_file_writes_nothing_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            code, out, summary = self.run_main(root, fixture_fetch(twse_ok=False, tpex_ok=False, tdcc_ok=False))
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(os.path.join(root, "out", "tw-chips.json")))
            self.assertIn("輸出驗證未通過", out)
            self.assertIn("skipped", summary)

    def test_corrupt_old_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            os.makedirs(os.path.join(root, "out"))
            with open(os.path.join(root, "out", "tw-chips.json"), "w") as file:
                file.write("{not json")
            code, out, _ = self.run_main(root, fixture_fetch())
            self.assertEqual(code, 0)
            self.assertEqual(self.load(root)["version"], 1)
            self.assertIn("::warning::舊檔無法讀取", out)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            make_site(root)
            self.run_main(root, fixture_fetch(), dry_run=True)
            self.assertFalse(os.path.exists(os.path.join(root, "out", "tw-chips.json")))


if __name__ == "__main__":
    unittest.main()
