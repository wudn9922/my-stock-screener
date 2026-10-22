#!/usr/bin/env python3
"""
台股籌碼（只含兩項）：外資及陸資持股比率、集保戶股權分散表總股東人數 → docs/report/tw-chips.json。

使用者明確只要這兩項，其他籌碼（三大法人買賣超、融資券、大戶持股級距…）都不做。

== 資料來源與「未能驗證」清單 ==
容器連不到下列任何官方網站，所以以下端點與欄位格式都是依記憶寫成「欄位名稱導向、容錯」的解析器，
**尚未用真實回應驗證**。第一次在 GitHub Actions 跑時，請對照日誌：欄位找不到時，解析器會把實際看到的
欄位名稱印出來（`::warning::` 與 step summary），照著調整 FIELD_* 常數或端點即可。

1. 上市外資持股（TWSE，未能驗證）
   https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=YYYYMMDD&selectType=ALLBUT0999&response=json
   預期：{"stat":"OK","date":"YYYYMMDD","fields":[證券代號, 證券名稱, 國際證券辨識號碼, 發行股數,
   外資及陸資尚可投資股數, 全體外資及陸資持有股數, 外資及陸資尚可投資比率, 全體外資及陸資持股比率, ...],"data":[[...]]}
   `stat` 不是 OK（假日 / 未出表：「很抱歉，沒有符合條件的資料」）視為「該日無資料」，不是錯誤。
   取「全體外資及陸資持股比率」欄（標頭含「持股比率」且不含「尚可」「上限」）；找不到時退而用 持有股數 / 發行股數。
2. 上櫃外資持股（TPEx，未能驗證，最不確定的一項）
   依序嘗試的端點（可用環境變數 TPEX_QFII_URL 或 --tpex-url 加入自己的範本，範本可用
   {roc}=114/10/14、{ymd}=20251014、{slash}=2025/10/14、{iso}=2025-10-14）：
     a. https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json&d={roc}
        舊版格式：{"reportDate":"114/10/14","aaData":[[代號,名稱,發行股數,尚可投資股數,持有股數,尚可投資比率,持股比率,...]]}
        （沒有欄位名稱，改用「位置」並以 持有/發行 交叉驗算，驗算不過就判定格式不符）
     b. https://www.tpex.org.tw/www/zh-tw/insti/qfii?type=Daily&date={slash}&id=&response=json
        新版格式的純猜測：{"stat":"ok","date":"20251014","tables":[{"fields":[...],"data":[...]}]}
   全部失敗時：上櫃個股的 foreign 欄位不輸出、sources.tpex.status 標 unavailable（有舊資料則保留舊資料並標 stale），
   **不影響上市與股東人數**。
3. 總股東人數（集保結算所開放資料，未能驗證）
   https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
   CSV（可能帶 BOM）：資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
   持股分級 17 為合計列，其「人數」即總股東人數（第 16 級為差異數調整，不計入）。只提供最新一週；歷史靠每次
   執行把新的一週加進既有 tw-chips.json 累積（同一週重跑不會重複）。若整份檔案完全沒有第 17 級，才退回
   加總第 1~15 級並印出警告。

== 輸出格式（version 1）==
{
  "version": 1,
  "generatedAt": "2026-10-14T11:40:00+00:00",     # 內容有變動時才更新；內容沒變就不改寫檔案
  "sources": {                                     # 各來源的最新狀態
    "twse": {"label":"證交所","asOf":"2026-10-14","status":"ok|stale|unavailable","message":null|"說明"},
    "tpex": {"label":"櫃買中心", ...},
    "tdcc": {"label":"集保結算所", ...}            # asOf = 最新一週的資料日期
  },
  "axes": {                                        # 共用日期軸（舊→新，個股序列與之等長，缺值為 null）
    "foreign": {"twse":["2026-04-20",...], "tpex":[...]},   # 最多約 120 個交易日
    "holders": ["2025-10-17",...]                           # 最多 52 週
  },
  "stocks": {                                      # 只含有任一項資料的個股，key 為 data/ 檔名的代號
    "2330.TW": {
      "foreign": {"pct":72.51,"asOf":"2026-10-14",
                  "d1":-0.03,"d1Date":"2026-10-13",   # 較前一個有資料的交易日（百分點）
                  "m1":0.2,"m1Date":"2026-09-12",     # 較「30 天前（含）以前最近一日」（百分點；歷史不夠長為 null）
                  "series":[72.4,...]},               # 對齊 axes.foreign.<twse|tpex>
      "holders": {"count":1234567,"asOf":"2026-10-10",
                  "wow":-1234,"wowDate":"2026-10-03", # 較上一週（人）
                  "series":[...]}                     # 對齊 axes.holders
    }
  }
}
檔案以「每檔一行」輸出（合法 JSON，git diff 較好讀）。

== 範圍 ==
data/ 的 {4碼, 非 0 開頭}.TW.csv / .TWO.csv 與 docs/report/financials/ 的同名 .json 聯集（普通股；0 開頭的 ETF 不收）。

== 行為保證 ==
- 抓取層：timeout、重試（指數退避）、請求間隔、User-Agent；單一來源失敗保留舊檔該來源的欄位，其餘照更新。
- 輸出前驗證（涵蓋率、比率 0–100、人數為非負整數、序列長度）；不合格就不覆蓋舊檔。
- 單一來源失敗不讓 workflow 變紅：以 `::warning::` 提示並寫入 step summary；程式本身的 bug 才會非零結束。

用法：python scripts/build_tw_chips.py [--lookback-days 10] [--dry-run]
首次建檔想一次補約 120 個交易日的外資歷史：--lookback-days 200（約 140 個工作日 × 2 市場，請求間隔下需 10 分鐘以上）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import tempfile
import time
from bisect import insort
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

CHIPS_JSON_VERSION = 1
FOREIGN_HISTORY_DAYS = 120
HOLDER_HISTORY_WEEKS = 53  # 52-week delta needs the baseline plus 52 observations
# 單日或單次下載解析出的股票數，至少要涵蓋名單中該市場股票的這個比例，否則視為格式或內容有問題
MIN_COVERAGE = 0.5
# 最終輸出至少要有名單的這個比例的個股有資料
MIN_STOCK_RATIO = 0.3
# 一天內被剔除的異常列（比率超出 0–100 等）超過這個比例就整天不採用
MAX_DROPPED_RATIO = 0.02
LOOKBACK_DAYS_DEFAULT = 10
M1_DAYS = 30
M1_TOLERANCE_DAYS = 10
WOW_MAX_GAP_DAYS = 21
CONSECUTIVE_FAILURE_LIMIT = 2

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_FINANCIALS_DIR = os.path.join(REPO_ROOT, "docs", "report", "financials")
DEFAULT_OUT = os.path.join(REPO_ROOT, "docs", "report", "tw-chips.json")

TAIPEI = timezone(timedelta(hours=8))
USER_AGENT = "Mozilla/5.0 (compatible; my-stock-screener-chips/1.0; +https://github.com/wudn9922/my-stock-screener)"

TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date={ymd}&selectType=ALLBUT0999&response=json"
TPEX_URLS = (
    "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json&d={roc}",
    "https://www.tpex.org.tw/www/zh-tw/insti/qfii?type=Daily&date={slash}&id=&response=json",
)
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"

SOURCE_LABELS = {"twse": "證交所", "tpex": "櫃買中心", "tdcc": "集保結算所"}

UNIVERSE_PATTERN = re.compile(r"^([1-9]\d{3})\.(TWO?)\.(?:csv|json)$")
SYMBOL_PATTERN = re.compile(r"^([1-9]\d{3})\.(TWO?)$")

# --- 欄位名稱（標頭正規化後以「包含」比對；改欄位名稱只需動這裡）---
FIELD_CODE = (("證券代號",), ("股票代號",), ("代號",), ("securitiescompanycode",), ("stockno",), ("code",))
FIELD_FOREIGN_PCT = (("全體", "持股比"), ("外資", "持股比"), ("持股比",))
FIELD_PCT_EXCLUDE = ("尚可", "上限")
FIELD_ISSUED = (("發行股數",), ("已發行股數",))
FIELD_HELD = (("全體", "持有股數"), ("外資", "持有股數"), ("持有股數",))
HELD_EXCLUDE = ("尚可",)
# 沒有欄位名稱時的位置推測：(代號, 發行股數, 持有股數, 持股比率)，依序以交叉驗算挑出可用者
POSITIONAL_LAYOUTS = ((0, 2, 4, 6), (0, 3, 5, 7))
TDCC_DATE = (("資料日期",), ("日期",), ("date",))
TDCC_CODE = (("證券代號",), ("代號",), ("code",))
TDCC_LEVEL = (("持股分級",), ("分級",), ("level",))
TDCC_PEOPLE = (("人數",), ("people",), ("holders",))
TDCC_TOTAL_LEVEL = 17

NODATA_PATTERN = re.compile(r"沒有符合|無符合|無資料|查無|尚無|尚未|查詢日期大於可查詢最大日期|no data", re.IGNORECASE)
BLANK_VALUES = {"", "-", "--", "---", "—", "－", "n/a", "na", "x", "null", "none", "nan", "不適用", "無"}


# ---------------------------------------------------------------- 純函式：解析 ----------------------------------------------------------------


@dataclass
class ParseResult:
    status: str  # "ok" | "nodata" | "error"
    date: str | None = None
    rows: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    message: str = ""


def to_number(value):
    """寬鬆轉數字：千分位逗號、%、全形空白；`--`、空字串、N/A 等回傳 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = re.sub(r"<[^>]*>", "", str(value)).replace("　", " ").strip()
    text = text.replace(",", "").replace("，", "").replace("%", "").replace("％", "").strip()
    if text.lower() in BLANK_VALUES:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def decode_payload(data) -> str:
    """bytes / str → str；去 BOM；UTF-8 失敗時退回 Big5（舊版 CSV）。"""
    if isinstance(data, str):
        return data.lstrip("﻿")
    for encoding in ("utf-8-sig", "big5hkscs", "cp950"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_date(value) -> str | None:
    """YYYYMMDD、YYYY-MM-DD、YYYY/MM/DD、民國 1141014、114/10/14、114年10月14日 → YYYY-MM-DD；不合法回傳 None。"""
    if value is None:
        return None
    text = str(value).strip()
    candidates = []
    match = re.search(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if match:
        candidates.append((int(match.group(1)) + 1911, int(match.group(2)), int(match.group(3))))
    match = re.fullmatch(r"(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", text)
    if match:
        candidates.append((int(match.group(1)), int(match.group(2)), int(match.group(3))))
    match = re.fullmatch(r"(\d{2,3})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        candidates.append((int(match.group(1)) + 1911, int(match.group(2)), int(match.group(3))))
    match = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", text)
    if match:
        candidates.append((int(match.group(1)) + 1911, int(match.group(2)), int(match.group(3))))
    for year, month, day in candidates:
        try:
            parsed = date(year, month, day)
        except ValueError:
            continue
        if parsed.year >= 1990:
            return parsed.isoformat()
    return None


def _norm(header) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", str(header))).lower()


def _find_column(headers, alternatives, exclude=()):
    """回傳第一個符合的欄位 index；alternatives 依優先序，每一組內的關鍵字都要包含。"""
    for keywords in alternatives:
        for index, header in enumerate(headers):
            if all(keyword in header for keyword in keywords) and not any(bad in header for bad in exclude):
                return index
    return None


def _clean_code(value) -> str | None:
    text = re.sub(r"<[^>]*>", "", str(value if value is not None else "")).strip().strip('="').strip()
    text = text.replace("　", "").upper()
    return text if re.fullmatch(r"[0-9A-Z]{4,8}", text) else None


def _decode_json(data):
    if isinstance(data, (dict, list)):
        return data
    text = decode_payload(data).strip()
    if not text:
        raise ValueError("回應是空的")
    if text.startswith("<"):
        raise ValueError(f"回應是 HTML 而不是 JSON（可能被擋或網址改了）：{text[:100]!r}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失敗（{exc}）：{text[:100]!r}") from exc


def _tables_from_dict(table):
    rows = None
    for key in ("data", "aaData", "rows"):
        if isinstance(table.get(key), list):
            rows = table[key]
            break
    if rows is None:
        return []
    if rows and all(isinstance(row, dict) for row in rows):
        return _tables_from_list(rows)
    fields = None
    for key in ("fields", "columns", "header", "headers"):
        candidate = table.get(key)
        if isinstance(candidate, list) and candidate and all(isinstance(item, str) for item in candidate):
            fields = candidate
            break
    return [(fields, rows)]


def _tables_from_list(items):
    if items and all(isinstance(item, dict) for item in items):  # OpenAPI 風格：[{欄位: 值}, ...]
        fields = []
        for item in items:
            for key in item:
                if key not in fields:
                    fields.append(key)
        return [(fields, [[item.get(name) for name in fields] for item in items])]
    if items and all(isinstance(item, list) for item in items):
        return [(None, items)]
    return []


def extract_tables(obj):
    """從已知的回應形狀取出 [(欄位名稱 list 或 None, 資料列 list)]。"""
    if isinstance(obj, list):
        return _tables_from_list(obj)
    tables = []
    if isinstance(obj, dict):
        for sub in obj.get("tables") or []:
            if isinstance(sub, dict):
                tables.extend(_tables_from_dict(sub))
        tables.extend(_tables_from_dict(obj))
    return tables


def _response_date(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    for key in ("date", "Date", "reportDate", "資料日期", "title", "reportTitle"):
        parsed = parse_date(obj.get(key))
        if parsed:
            return parsed
    for sub in obj.get("tables") or []:
        if isinstance(sub, dict):
            parsed = _response_date(sub)
            if parsed:
                return parsed
    return None


def _positional_layout(rows):
    """沒有欄位名稱時，挑一個「持有 / 發行 ≈ 持股比率」的位置組合；都不過回傳 None。"""
    for layout in POSITIONAL_LAYOUTS:
        _, issued_i, held_i, pct_i = layout
        testable = agree = 0
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) <= max(layout):
                continue
            issued, held, pct = to_number(row[issued_i]), to_number(row[held_i]), to_number(row[pct_i])
            if issued and issued > 0 and held is not None and pct is not None:
                testable += 1
                if abs(held / issued * 100 - pct) <= 1.0:
                    agree += 1
        if testable and agree / testable >= 0.9:
            return layout
    return None


def _qfii_rows(fields, rows):
    """單一表格 → ({代號: 比率}, dropped, notes)；欄位找不到時 notes 內含實際欄位名稱，rows 為 None。"""
    notes = []
    if fields is not None:
        headers = [_norm(name) for name in fields]
        code_i = _find_column(headers, FIELD_CODE)
        pct_i = _find_column(headers, FIELD_FOREIGN_PCT, FIELD_PCT_EXCLUDE)
        issued_i = _find_column(headers, FIELD_ISSUED)
        held_i = _find_column(headers, FIELD_HELD, HELD_EXCLUDE)
        if code_i is None:
            return None, 0, [f"找不到「證券代號」欄位；實際欄位：{list(fields)}"]
        if pct_i is None and (issued_i is None or held_i is None):
            return None, 0, [f"找不到「全體外資及陸資持股比率」欄位（也無法用 持有股數/發行股數 推算）；實際欄位：{list(fields)}"]
        if pct_i is None:
            notes.append(f"找不到持股比率欄位，改用 持有股數/發行股數 推算；實際欄位：{list(fields)}")
    else:
        layout = _positional_layout(rows)
        if layout is None:
            sample = next((row for row in rows if isinstance(row, (list, tuple))), None)
            return None, 0, [f"沒有欄位名稱，且位置推測驗算失敗（持有/發行 與 持股比率 不吻合）；第一列範例：{sample}"]
        code_i, issued_i, held_i, pct_i = layout
        notes.append(f"沒有欄位名稱，依位置推測 代號/發行/持有/比率 = 第 {layout} 欄（未驗證）")

    out, dropped, mismatched, testable = {}, 0, 0, 0
    needed = max(index for index in (code_i, pct_i, issued_i, held_i) if index is not None)
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= needed:
            dropped += 1
            continue
        code = _clean_code(row[code_i])
        if code is None:
            continue  # 合計列、備註列等
        issued = to_number(row[issued_i]) if issued_i is not None else None
        held = to_number(row[held_i]) if held_i is not None else None
        computed = round(held / issued * 100, 2) if issued and issued > 0 and held is not None else None
        pct = to_number(row[pct_i]) if pct_i is not None else None
        if pct is None:
            pct = computed
        elif computed is not None:
            testable += 1
            if abs(computed - pct) > 1.0:
                mismatched += 1
        if pct is None:
            continue  # `--` / 空值：該檔沒有資料
        if not 0 <= pct <= 100:
            dropped += 1
            continue
        out[code] = round(pct, 2)
    if testable and mismatched / testable > 0.2:
        notes.append(f"警告：{mismatched}/{testable} 列的「持股比率」與 持有股數/發行股數 差超過 1 個百分點，欄位可能抓錯；實際欄位：{fields}")
    return out, dropped, notes


def parse_qfii(data, label: str = "外資持股") -> ParseResult:
    """TWSE / TPEx 外資及陸資持股比率回應 → ParseResult（rows: {證券代號: 持股比率 %}）。"""
    try:
        obj = _decode_json(data)
    except ValueError as exc:
        return ParseResult("error", message=f"{label}：{exc}")
    if isinstance(obj, dict) and obj.get("stat") is not None and str(obj["stat"]).strip().upper() != "OK":
        stat = str(obj["stat"]).strip()
        if NODATA_PATTERN.search(stat):
            return ParseResult("nodata", date=_response_date(obj), message=f"{label}：{stat}")
        return ParseResult("error", message=f"{label}：stat={stat!r}")
    tables = extract_tables(obj)
    if not tables:
        keys = list(obj.keys()) if isinstance(obj, dict) else type(obj).__name__
        return ParseResult("error", message=f"{label}：回應中找不到資料表（頂層欄位：{keys}）")
    notes, saw_rows = [], False
    for fields, rows in tables:
        if not rows:
            continue
        saw_rows = True
        parsed, dropped, table_notes = _qfii_rows(fields, rows)
        notes.extend(table_notes)
        if parsed is None:
            continue
        if parsed and dropped > MAX_DROPPED_RATIO * (len(parsed) + dropped):
            return ParseResult("error", notes=notes, message=f"{label}：{dropped} 列的比率超出 0–100 或欄位不足，整天不採用")
        if parsed:
            return ParseResult("ok", date=_response_date(obj), rows=parsed, notes=notes)
    if not saw_rows:
        return ParseResult("nodata", date=_response_date(obj), message=f"{label}：資料表是空的")
    return ParseResult("error", notes=notes, message=f"{label}：" + ("；".join(notes) or "找不到可用欄位"))


def parse_tdcc_distribution(data, wanted=None) -> ParseResult:
    """Official inventory denominator; levels 12–15 (>400k), 15 (>1m), never 16.

    Require complete grades and reconcile rounded published percentages (0.03pp
    per grade); malformed stocks are omitted, never represented as zero.
    """
    groups = {}
    for row in csv.DictReader(io.StringIO(decode_payload(data))):
        code = _clean_code(row.get('證券代號'))
        day = parse_date(row.get('資料日期'))
        level = to_number(row.get('持股分級'))
        if not code or not day or level is None or (wanted is not None and code not in wanted):
            continue
        group = groups.setdefault((day, code), {})
        if level in group:
            return ParseResult('error', message='TDCC duplicate grade')
        group[int(level)] = (to_number(row.get('人數')), to_number(row.get('股數')), to_number(row.get('占集保庫存數比例%')))
    if not groups:
        return ParseResult('error', message='TDCC distribution headers/data unavailable')
    latest = max(day for day, _ in groups)
    rows = {}
    for (day, code), grades in groups.items():
        if day != latest or not all(i in grades for i in [*range(1, 16), 17]):
            continue
        total_people, total_shares, _ = grades[17]
        if total_people is None or total_shares is None or total_people < 0 or total_people != int(total_people) or total_shares <= 0:
            continue
        if any(p is None or s is None or pct is None or p < 0 or s < 0 or p != int(p) or s != int(s) or not 0 <= pct <= 100
               for i, (p, s, pct) in grades.items() if i in range(1, 16)):
            continue
        if sum(grades[i][0] for i in range(1, 16)) != total_people:
            continue
        result = dict(date=day, count=int(total_people), totalShares=int(total_shares))
        good = True
        for key, levels in [('over400', range(12, 16)), ('over1000', [15])]:
            shares = sum(grades[i][1] for i in levels)
            pct = shares / total_shares * 100
            published = sum(grades[i][2] for i in levels)
            if not 0 <= pct <= 100 or abs(pct - published) > .03 * len(levels):
                good = False
            result[key] = dict(pct=round(pct, 6), people=int(sum(grades[i][0] for i in levels)), shares=int(shares), publishedPct=published)
        if good:
            rows[code] = result
    return ParseResult('ok' if rows else 'error', date=latest, rows=rows, message='' if rows else 'TDCC distribution validation failed')


def parse_tdcc_holders(data, wanted=None) -> ParseResult:
    """集保戶股權分散表 CSV → ParseResult（rows: {證券代號: 總股東人數}，date: 資料日期）。"""
    text = decode_payload(data)
    if not text.strip():
        return ParseResult("error", message="集保股權分散表：檔案是空的")
    reader = csv.reader(io.StringIO(text))
    header = next((row for row in reader if any(cell.strip() for cell in row)), None)
    if header is None:
        return ParseResult("error", message="集保股權分散表：檔案沒有標頭列")
    headers = [_norm(cell) for cell in header]
    date_i = _find_column(headers, TDCC_DATE)
    code_i = _find_column(headers, TDCC_CODE)
    level_i = _find_column(headers, TDCC_LEVEL)
    people_i = _find_column(headers, TDCC_PEOPLE)
    missing = [name for name, index in (("資料日期", date_i), ("證券代號", code_i), ("持股分級", level_i), ("人數", people_i)) if index is None]
    if missing:
        return ParseResult("error", message=f"集保股權分散表：找不到欄位 {missing}；實際欄位：{[cell.strip() for cell in header]}")
    needed = max(date_i, code_i, level_i, people_i)
    totals, partial, dates, bad_rows = {}, {}, Counter(), 0
    for row in reader:
        if len(row) <= needed:
            if any(cell.strip() for cell in row):
                bad_rows += 1
            continue
        code = _clean_code(row[code_i])
        if code is None or (wanted is not None and code not in wanted):
            continue
        level, people = to_number(row[level_i]), to_number(row[people_i])
        if level is None or people is None or people < 0:
            bad_rows += 1
            continue
        parsed_date = parse_date(row[date_i])
        if parsed_date:
            dates[parsed_date] += 1
        level = int(level)
        if level == TDCC_TOTAL_LEVEL:
            totals[code] = int(people)
        elif 1 <= level < 16:  # 第 16 級是差異數調整，不計入
            partial[code] = partial.get(code, 0) + int(people)
    notes = []
    if bad_rows:
        notes.append(f"{bad_rows} 列欄位不足或數值無法解析，已略過")
    rows = totals
    if not totals and partial:
        rows = partial
        notes.append("警告：整份檔案沒有第 17 級（合計）列，改用第 1~15 級加總；請確認格式是否改版")
    if not rows:
        return ParseResult("error", notes=notes, message=f"集保股權分散表：沒有可用資料列（標頭：{[cell.strip() for cell in header]}）")
    if not dates:
        return ParseResult("error", notes=notes, message="集保股權分散表：資料日期欄位無法解析")
    best, _ = max(dates.items(), key=lambda item: (item[1], item[0]))
    if len(dates) > 1:
        notes.append(f"警告：檔案內有多個資料日期 {dict(dates)}，採用列數最多者 {best}")
    return ParseResult("ok", date=best, rows=rows, notes=notes)


# ---------------------------------------------------------------- 純函式：歷史與輸出 ----------------------------------------------------------------


class History:
    """日期軸 + {代號: {日期: 值}}；加入同一天是覆寫（冪等），超過上限時丟掉最舊的日期。"""

    def __init__(self):
        self.dates: list[str] = []
        self.values: dict[str, dict[str, float]] = {}

    def add(self, day: str, rows: dict):
        if day not in self.dates:
            insort(self.dates, day)
        for code, value in rows.items():
            self.values.setdefault(code, {})[day] = value

    def trim(self, keep: int):
        if len(self.dates) <= keep:
            return
        dropped = set(self.dates[:-keep])
        self.dates = self.dates[-keep:]
        for code in list(self.values):
            self.values[code] = {day: value for day, value in self.values[code].items() if day not in dropped}
            if not self.values[code]:
                del self.values[code]

    def series(self, code: str):
        per_day = self.values.get(code, {})
        return [per_day.get(day) for day in self.dates]


def history_from_payload(axis, stocks, section, symbol_ok):
    """從舊的 tw-chips.json 還原 History；形狀不對的個股略過。symbol_ok(symbol) 決定哪些個股屬於這個軸。"""
    history = History()
    if not isinstance(axis, list) or not all(isinstance(day, str) for day in axis):
        return history
    history.dates = sorted(set(axis))
    if history.dates != list(axis):
        return History()
    for symbol, entry in (stocks or {}).items():
        match = SYMBOL_PATTERN.match(str(symbol))
        if not match or not symbol_ok(symbol) or not isinstance(entry, dict):
            continue
        series = (entry.get(section) or {}).get("series") if isinstance(entry.get(section), dict) else None
        if not isinstance(series, list) or len(series) != len(axis):
            continue
        for day, value in zip(axis, series):
            number = to_number(value)
            if number is not None:
                history.values.setdefault(match.group(1), {})[day] = number
    return history


def _round_change(value, digits):
    return round(value, digits) if digits else int(round(value))


def _latest_and_changes(dates, series, *, digits, m1=False, gap_limit=None):
    """最新值與變化。回傳 dict 或 None（序列全空）。"""
    present = [(day, value) for day, value in zip(dates, series) if value is not None]
    if not present:
        return None
    day, value = present[-1]
    out = {"value": value, "asOf": day, "prev": None, "month": None}
    if len(present) >= 2:
        prev_day, prev_value = present[-2]
        gap = (date.fromisoformat(day) - date.fromisoformat(prev_day)).days
        if gap_limit is None or gap <= gap_limit:
            out["prev"] = (_round_change(value - prev_value, digits), prev_day)
    if m1:
        target = date.fromisoformat(day) - timedelta(days=M1_DAYS)
        older = [(d, v) for d, v in present[:-1] if date.fromisoformat(d) <= target]
        if older and (target - date.fromisoformat(older[-1][0])).days <= M1_TOLERANCE_DAYS:
            out["month"] = (_round_change(value - older[-1][1], digits), older[-1][0])
    return out


def foreign_entry(dates, series):
    info = _latest_and_changes(dates, series, digits=2, m1=True)
    if info is None:
        return None
    return {
        "pct": info["value"],
        "asOf": info["asOf"],
        "d1": info["prev"][0] if info["prev"] else None,
        "d1Date": info["prev"][1] if info["prev"] else None,
        "m1": info["month"][0] if info["month"] else None,
        "m1Date": info["month"][1] if info["month"] else None,
        "series": series,
    }


def holders_entry(dates, series):
    info = _latest_and_changes(dates, series, digits=0, gap_limit=WOW_MAX_GAP_DAYS)
    if info is None:
        return None
    return {
        "count": int(info["value"]),
        "asOf": info["asOf"],
        "wow": info["prev"][0] if info["prev"] else None,
        "wowDate": info["prev"][1] if info["prev"] else None,
        "series": [None if value is None else int(value) for value in series],
    }


def build_payload(universe, foreign, holders, sources, generated_at):
    """foreign: {"twse": History, "tpex": History}；holders: History（以 4 碼代號為 key）。"""
    stocks = {}
    for symbol in sorted(universe):
        match = SYMBOL_PATTERN.match(symbol)
        code, suffix = match.group(1), match.group(2)
        entry = {}
        market = "twse" if suffix == "TW" else "tpex"
        found = foreign_entry(foreign[market].dates, foreign[market].series(code))
        if found:
            entry["foreign"] = found
        found = holders_entry(holders.dates, holders.series(code))
        if found:
            entry["holders"] = found
        if entry:
            stocks[symbol] = entry
    return {
        "version": CHIPS_JSON_VERSION,
        "generatedAt": generated_at,
        "sources": sources,
        "axes": {"foreign": {"twse": foreign["twse"].dates, "tpex": foreign["tpex"].dates}, "holders": holders.dates},
        "stocks": stocks,
    }


def _iso_ok(value):
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def validate_payload(payload, universe_size):
    """回傳問題清單；空 list 代表可以寫檔。"""
    problems = []
    if not isinstance(payload, dict) or payload.get("version") != CHIPS_JSON_VERSION:
        return ["version 不正確"]
    axes = payload.get("axes") or {}
    foreign_axes = axes.get("foreign") or {}
    holders_axis = axes.get("holders") or []
    for name, axis in [("axes.foreign.twse", foreign_axes.get("twse")), ("axes.foreign.tpex", foreign_axes.get("tpex")), ("axes.holders", holders_axis)]:
        if not isinstance(axis, list) or not all(_iso_ok(day) for day in axis) or axis != sorted(set(axis)):
            problems.append(f"{name} 不是遞增的 ISO 日期")
    stocks = payload.get("stocks") or {}
    if not stocks:
        problems.append("沒有任何個股資料")
    elif len(stocks) < MIN_STOCK_RATIO * universe_size:
        problems.append(f"只有 {len(stocks)} 檔有資料，少於名單 {universe_size} 檔的 {int(MIN_STOCK_RATIO * 100)}%")
    for symbol, entry in stocks.items():
        match = SYMBOL_PATTERN.match(symbol)
        if not match:
            problems.append(f"{symbol} 代號格式不對")
            continue
        foreign = entry.get("foreign")
        if foreign is not None:
            axis = foreign_axes.get("twse" if match.group(2) == "TW" else "tpex") or []
            series = foreign.get("series") or []
            if len(series) != len(axis):
                problems.append(f"{symbol} foreign.series 長度 {len(series)} ≠ 日期軸 {len(axis)}")
            if any(v is not None and not (isinstance(v, (int, float)) and 0 <= v <= 100) for v in series) or not 0 <= foreign.get("pct", -1) <= 100:
                problems.append(f"{symbol} 外資持股比率超出 0–100")
        holders = entry.get("holders")
        if holders is not None:
            series = holders.get("series") or []
            if len(series) != len(holders_axis):
                problems.append(f"{symbol} holders.series 長度 {len(series)} ≠ 日期軸 {len(holders_axis)}")
            if any(v is not None and not (isinstance(v, int) and v >= 0) for v in series) or not (isinstance(holders.get("count"), int) and holders["count"] >= 0):
                problems.append(f"{symbol} 股東人數不是非負整數")
        if len(problems) >= 10:
            problems.append("（問題太多，省略其餘）")
            break
    return problems


def serialize_payload(payload) -> str:
    """合法 JSON，但每檔一行，git diff 比較好讀。"""

    def dump(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    stock_lines = ",\n".join(f"{dump(symbol)}:{dump(entry)}" for symbol, entry in payload["stocks"].items())
    head = ",".join(f'{dump(key)}:{dump(payload[key])}' for key in ("version", "generatedAt", "sources", "axes", "distributionMethod") if key in payload)
    return "{" + head + ',"stocks":{\n' + stock_lines + "\n}}\n"


def write_text_atomic(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    file_descriptor, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_universe(data_dir, financials_dir):
    """data/ 與 financials/ 檔名的聯集（4 碼、非 0 開頭的 .TW / .TWO）。"""
    symbols = set()
    for directory in (data_dir, financials_dir):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            match = UNIVERSE_PATTERN.match(name)
            if match:
                symbols.add(f"{match.group(1)}.{match.group(2)}")
    return sorted(symbols)


def weekday_targets(today: date, lookback_days: int, existing: list[str]):
    """今天往前 lookback_days 天內、尚未在日期軸上的平日，舊→新。假日會被重試（便宜），不另外記錄。"""
    have = set(existing)
    days = []
    for offset in range(lookback_days, -1, -1):
        day = today - timedelta(days=offset)
        if day.weekday() < 5 and day.isoformat() not in have:
            days.append(day)
    return days


# ---------------------------------------------------------------- 抓取層 ----------------------------------------------------------------


class FetchError(Exception):
    """kind: "network"（逾時 / 連線 / 5xx，重試後仍失敗）或 "http"（4xx 等，端點可能不存在）。"""

    def __init__(self, message, kind="network"):
        super().__init__(message)
        self.kind = kind


class Fetcher:
    def __init__(self, session=None, timeout=(10, 60), retries=3, delay=1.5, sleep=time.sleep, clock=time.monotonic):
        if session is None:
            import requests  # 延遲匯入：解析與測試不需要 requests

            session = requests.Session()
        self.session = session
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.sleep = sleep
        self.clock = clock
        self._last = None

    def _polite_wait(self):
        if self._last is not None:
            wait = self.delay - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def get(self, url, timeout=None) -> bytes:
        last = "未知錯誤"
        for attempt in range(1, self.retries + 1):
            self._polite_wait()
            try:
                response = self.session.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/csv, */*"}, timeout=timeout or self.timeout)
            except Exception as exc:  # requests 的逾時 / 連線例外
                last = f"{type(exc).__name__}: {str(exc)[:160]}"
            else:
                status = response.status_code
                if status == 200:
                    return response.content
                last = f"HTTP {status}"
                if status != 429 and status < 500:
                    raise FetchError(f"{last}（{url}）", kind="http")
            if attempt < self.retries:
                self.sleep(2**attempt)
        raise FetchError(f"{last}（已重試 {self.retries} 次）", kind="network")


# ---------------------------------------------------------------- 各來源更新 ----------------------------------------------------------------


@dataclass
class Endpoint:
    name: str
    url: object  # callable(date) -> str
    parse: object  # callable(bytes) -> ParseResult


def _date_forms(day: date):
    return {
        "ymd": day.strftime("%Y%m%d"),
        "iso": day.isoformat(),
        "slash": day.strftime("%Y/%m/%d"),
        "roc": f"{day.year - 1911}/{day.month:02d}/{day.day:02d}",
    }


def twse_endpoints():
    return [Endpoint("TWSE MI_QFIIS", lambda day: TWSE_URL.format(**_date_forms(day)), lambda body: parse_qfii(body, "證交所外資持股"))]


def tpex_endpoints(extra_templates=()):
    templates = list(extra_templates) + list(TPEX_URLS)
    return [
        Endpoint(f"TPEx 端點{index + 1}", (lambda day, t=template: t.format(**_date_forms(day))), lambda body: parse_qfii(body, "櫃買外資持股"))
        for index, template in enumerate(templates)
    ]


@dataclass
class Outcome:
    status: str  # ok | stale | unavailable
    message: str | None = None
    added: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def update_foreign(history: History, endpoints, fetch, targets, member_codes, log=print) -> Outcome:
    """逐日抓外資持股並併入 history。無資料日（假日 / 未出表）不算失敗；連續失敗 2 天就停止，避免猛打對方。"""
    outcome = Outcome("ok")
    preferred = 0
    consecutive = 0
    for day in targets:
        result, last_error = None, "未知錯誤"
        order = [preferred] + [i for i in range(len(endpoints)) if i != preferred]
        for index in order:
            endpoint = endpoints[index]
            try:
                body = fetch(endpoint.url(day))
            except FetchError as exc:
                last_error = f"{endpoint.name}: {exc}"
                if exc.kind == "network":
                    break  # 連線層級的問題，換端點沒有幫助
                continue
            parsed = endpoint.parse(body)
            outcome.notes.extend(f"{endpoint.name}: {note}" for note in parsed.notes)
            if parsed.status in ("ok", "nodata"):
                result, preferred = parsed, index
                break
            last_error = f"{endpoint.name}: {parsed.message}"
        if result is not None and result.status == "ok":
            rows = {code: value for code, value in result.rows.items() if code in member_codes}
            if len(rows) < MIN_COVERAGE * len(member_codes):
                last_error = f"{endpoints[preferred].name}: 只解析到 {len(rows)}/{len(member_codes)} 檔名單內股票，低於 {int(MIN_COVERAGE * 100)}%（可能欄位或格式不符）"
                result = None
        if result is None:
            consecutive += 1
            outcome.errors.append(f"{day.isoformat()} {last_error}")
            log(f"  ✗ {day.isoformat()} {last_error}")
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                outcome.errors.append(f"連續 {CONSECUTIVE_FAILURE_LIMIT} 天失敗，停止這個來源的其餘日期")
                break
            continue
        consecutive = 0
        if result.status == "nodata":
            log(f"  - {day.isoformat()} 無資料（假日或尚未出表）")
            continue
        actual = result.date or day.isoformat()
        if actual != day.isoformat():
            outcome.notes.append(f"請求 {day.isoformat()} 但回應日期是 {actual}，以回應日期為準")
        history.add(actual, rows)
        outcome.added.append(actual)
        log(f"  ✓ {actual} {len(rows)}/{len(member_codes)} 檔")
    history.trim(FOREIGN_HISTORY_DAYS)
    if outcome.errors and not outcome.added:
        outcome.status = "stale" if history.dates else "unavailable"
        outcome.message = "本次更新失敗，沿用舊資料" if history.dates else "資料來源暫時無法取得"
    elif not outcome.added and not history.dates:
        outcome.status = "unavailable"
        outcome.message = "查詢區間內沒有交易日資料"
    elif outcome.errors:
        outcome.message = "部分日期抓取失敗"
    return outcome


def update_holders(history: History, fetch, wanted_codes, url=TDCC_URL, log=print, distributions=None) -> Outcome:
    outcome = Outcome("ok")

    def fail(message):
        outcome.status = "stale" if history.dates else "unavailable"
        outcome.message = "本次更新失敗，沿用舊資料" if history.dates else "資料來源暫時無法取得"
        outcome.errors.append(message)
        log(f"  ✗ {message}")
        return outcome

    try:
        body = fetch(url)
    except FetchError as exc:
        return fail(f"集保: {exc}")
    parsed = parse_tdcc_holders(body, wanted=wanted_codes)
    if distributions is not None:
        large = parse_tdcc_distribution(body, wanted=wanted_codes)
        if large.status == 'ok':
            for code, row in large.rows.items():
                existing = {r['date']: r for r in distributions.get(code, [])}
                existing[row['date']] = row
                distributions[code] = [existing[d] for d in sorted(existing)][-HOLDER_HISTORY_WEEKS:]
        else:
            outcome.notes.append('大戶分級資料未取得完整有效分級，保留已累積歷史；總股東人數獨立更新')
    outcome.notes.extend(parsed.notes)
    if parsed.status != "ok":
        return fail(parsed.message)
    if len(parsed.rows) < MIN_COVERAGE * len(wanted_codes):
        return fail(f"集保: 只解析到 {len(parsed.rows)}/{len(wanted_codes)} 檔名單內股票，低於 {int(MIN_COVERAGE * 100)}%（可能欄位或格式不符）")
    if history.dates and parsed.date < history.dates[-1]:
        outcome.notes.append(f"檔案資料日期 {parsed.date} 早於已累積的 {history.dates[-1]}，不採用")
        return outcome
    new_week = parsed.date not in history.dates
    history.add(parsed.date, {code: int(value) for code, value in parsed.rows.items()})
    history.trim(HOLDER_HISTORY_WEEKS)
    if new_week:
        outcome.added.append(parsed.date)
    log(f"  ✓ 資料日期 {parsed.date} {len(parsed.rows)}/{len(wanted_codes)} 檔{'（新的一週）' if new_week else '（已有，覆寫）'}")
    return outcome


# ---------------------------------------------------------------- 主流程 ----------------------------------------------------------------


def _load_previous(path):
    """舊檔不存在或壞掉都回傳 None（壞掉時回傳 (None, 原因)）。"""
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as file:
            previous = json.load(file)
    except (OSError, ValueError) as exc:
        return None, f"舊檔無法讀取（{exc}），視為不存在"
    if not isinstance(previous, dict) or previous.get("version") != CHIPS_JSON_VERSION:
        return None, "舊檔版本不符，視為不存在"
    return previous, None


class Reporter:
    """收集警告；GitHub Actions 的 ::warning:: 與 step summary。"""

    def __init__(self, env=None):
        self.env = os.environ if env is None else env
        self.warnings: list[str] = []
        self.rows: list[tuple] = []

    def warn(self, message):
        self.warnings.append(message)
        print("::warning::" + message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))

    def row(self, source, status, as_of, detail):
        self.rows.append((source, status, as_of or "—", detail))

    def write_summary(self, extra=()):
        path = self.env.get("GITHUB_STEP_SUMMARY")
        if not path:
            return
        lines = ["## 台股籌碼（外資持股比率 / 總股東人數）", "", "| 來源 | 狀態 | 資料日 | 說明 |", "|---|---|---|---|"]
        for source, status, as_of, detail in self.rows:
            lines.append(f"| {source} | {status} | {as_of} | {str(detail).replace('|', '/')} |")
        if self.warnings:
            lines += ["", "### 警告"] + [f"- {message.splitlines()[0][:400]}" for message in self.warnings]
        lines += list(extra)
        try:
            with open(path, "a", encoding="utf-8") as file:
                file.write("\n".join(lines) + "\n")
        except OSError as exc:
            print(f"無法寫入 step summary：{exc}")


def run(args, fetch=None, now=None, env=None) -> int:
    reporter = Reporter(env)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TAIPEI).date()
    universe = load_universe(args.data_dir, args.financials_dir)
    if not universe:
        reporter.warn(f"{args.data_dir} 與 {args.financials_dir} 找不到任何 4 碼台股，不更新")
        reporter.write_summary()
        return 0
    members = {
        "twse": {SYMBOL_PATTERN.match(s).group(1) for s in universe if s.endswith(".TW")},
        "tpex": {SYMBOL_PATTERN.match(s).group(1) for s in universe if s.endswith(".TWO")},
    }
    print(f"名單：{len(universe)} 檔（上市 {len(members['twse'])}、上櫃 {len(members['tpex'])}）")

    previous, problem = _load_previous(args.out)
    if problem:
        reporter.warn(problem)
    old_stocks = (previous or {}).get("stocks") or {}
    old_axes = (previous or {}).get("axes") or {}
    old_foreign_axes = old_axes.get("foreign") if isinstance(old_axes.get("foreign"), dict) else {}
    old_sources = (previous or {}).get("sources") if isinstance((previous or {}).get("sources"), dict) else {}
    foreign = {
        "twse": history_from_payload(old_foreign_axes.get("twse"), old_stocks, "foreign", lambda s: s.endswith(".TW")),
        "tpex": history_from_payload(old_foreign_axes.get("tpex"), old_stocks, "foreign", lambda s: s.endswith(".TWO")),
    }
    holders = history_from_payload(old_axes.get("holders"), old_stocks, "holders", lambda s: True)
    distributions = {s.split('.')[0]: e['largeHolders'] for s, e in old_stocks.items() if isinstance(e.get('largeHolders'), list)}

    if fetch is None:
        fetcher = Fetcher(timeout=(10, args.timeout), retries=args.retries, delay=args.delay)
        fetch = fetcher.get
        tdcc_fetch = lambda url: fetcher.get(url, timeout=(10, max(args.timeout, 180)))  # noqa: E731 - 檔案很大
    else:
        tdcc_fetch = fetch

    extra_templates = [t for t in (args.tpex_url or []) + ([os.environ["TPEX_QFII_URL"]] if os.environ.get("TPEX_QFII_URL") else []) if t.strip()]
    outcomes = {}
    unexpected_errors = []
    plans = [("twse", twse_endpoints()), ("tpex", tpex_endpoints(extra_templates))]
    for key, endpoints in plans:
        print(f"[{SOURCE_LABELS[key]}] 外資持股")
        targets = weekday_targets(today, args.lookback_days, foreign[key].dates)
        try:
            if not members[key]:
                outcomes[key] = Outcome("ok", "名單內沒有這個市場的股票")
            else:
                outcomes[key] = update_foreign(foreign[key], endpoints, fetch, targets, members[key])
        except Exception as exc:  # noqa: BLE001 - 單一來源的任何錯誤都不能拖垮其他來源
            unexpected_errors.append(exc)
            outcomes[key] = Outcome("stale" if foreign[key].dates else "unavailable", "本次更新發生例外", errors=[f"{type(exc).__name__}: {exc}"])
    print(f"[{SOURCE_LABELS['tdcc']}] 總股東人數")
    try:
        outcomes["tdcc"] = update_holders(holders, tdcc_fetch, members["twse"] | members["tpex"], distributions=distributions)
    except Exception as exc:  # noqa: BLE001
        unexpected_errors.append(exc)
        outcomes["tdcc"] = Outcome("stale" if holders.dates else "unavailable", "本次更新發生例外", errors=[f"{type(exc).__name__}: {exc}"])

    histories = {"twse": foreign["twse"], "tpex": foreign["tpex"], "tdcc": holders}
    sources = {}
    for key in ("twse", "tpex", "tdcc"):
        outcome, history = outcomes[key], histories[key]
        sources[key] = {
            "label": SOURCE_LABELS[key],
            "asOf": history.dates[-1] if history.dates else None,
            "status": outcome.status,
            "message": outcome.message,
        }
        for note in dict.fromkeys(outcome.notes):  # 逐日重複的訊息只留一次
            if any(word in note for word in ("警告", "找不到", "不採用", "不吻合")):
                reporter.warn(f"{SOURCE_LABELS[key]}：{note}")
            else:
                print(f"  ℹ {SOURCE_LABELS[key]}：{note}")
        for error in outcome.errors:
            reporter.warn(f"{SOURCE_LABELS[key]}：{error}")
        detail = outcome.message or ""
        if outcome.added:
            detail = (detail + "；" if detail else "") + f"新增 {len(outcome.added)} 個資料日（{outcome.added[0]}～{outcome.added[-1]}）"
        elif outcome.status == "ok":
            detail = detail or "沒有新的資料日"
        reporter.row(SOURCE_LABELS[key], outcome.status, sources[key]["asOf"], detail)

    payload = build_payload(universe, foreign, holders, sources, now.replace(microsecond=0).isoformat())
    for symbol, entry in payload['stocks'].items():
        if symbol.split('.')[0] in distributions:
            entry['largeHolders'] = distributions[symbol.split('.')[0]]
    distribution_asof = max((r[-1]['date'] for r in distributions.values() if r), default=None)
    distribution_ok = distribution_asof == sources['tdcc']['asOf'] and outcomes['tdcc'].status == 'ok'
    payload['distributionMethod'] = {'version': 1, 'sourceUrl': TDCC_URL, 'asOf': distribution_asof, 'status': 'ok' if distribution_ok else 'stale' if distributions else 'unavailable',
        'fetchedAt': now.replace(microsecond=0).isoformat() if distribution_ok else (previous or {}).get('distributionMethod', {}).get('fetchedAt'), 'publishedAt': None,
        'thresholds': '12–15: >400,000 shares; 15: >1,000,000 shares; denominator: level 17 inventory; exclude level 16',
        'history': 'Latest public download only; prior observations retained, never synthesized.'}
    problems = validate_payload(payload, len(universe))
    if problems:
        reporter.warn("輸出驗證未通過，不覆蓋舊檔：" + "；".join(problems))
        reporter.row("輸出", "skipped", None, "驗證未通過，保留舊檔")
    else:
        comparable = lambda p: {k: v for k, v in p.items() if k != "generatedAt"}  # noqa: E731
        if previous is not None and comparable(previous) == comparable(json.loads(serialize_payload(payload))):
            print("內容沒有變動，不改寫檔案")
            reporter.row("輸出", "unchanged", None, "內容沒有變動")
        elif args.dry_run:
            print(f"--dry-run：不寫檔（{len(payload['stocks'])} 檔有資料）")
            reporter.row("輸出", "dry-run", None, f"{len(payload['stocks'])} 檔有資料")
        else:
            text = serialize_payload(payload)
            write_text_atomic(args.out, text)
            print(f"✅ 輸出 {args.out}：{len(payload['stocks'])} 檔有資料，{len(text) / 1024:.0f} KiB")
            reporter.row("輸出", "written", None, f"{len(payload['stocks'])} 檔有資料，{len(text) / 1024:.0f} KiB")
    reporter.write_summary()
    return 1 if unexpected_errors else 0


def main(argv=None, fetch=None, now=None, env=None) -> int:
    parser = argparse.ArgumentParser(description="產生 docs/report/tw-chips.json（外資持股比率、總股東人數）")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--financials-dir", default=DEFAULT_FINANCIALS_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS_DEFAULT, help="往回補抓外資持股的日曆天數（預設 10；首次建檔可用 200）")
    parser.add_argument("--delay", type=float, default=1.5, help="請求間隔秒數")
    parser.add_argument("--timeout", type=float, default=30, help="單次讀取逾時秒數")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--tpex-url", action="append", help="額外的 TPEx 外資持股網址範本，可重複；可用 {roc}{ymd}{slash}{iso}")
    parser.add_argument("--dry-run", action="store_true", help="抓取並驗證，但不寫檔")
    return run(parser.parse_args(argv), fetch=fetch, now=now, env=env)


if __name__ == "__main__":
    sys.exit(main())
