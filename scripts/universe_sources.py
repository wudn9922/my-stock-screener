"""
全市場 universe（scripts/build_universe_full.py）的資料來源解析器：純函式，不連網路。

台股名單與公司資料
- 上市：證交所 OpenAPI t187ap03_L（上市公司基本資料）；上櫃：櫃買中心 OpenAPI mopsfin_t187ap03_O（上櫃公司基本資料）。
  只留 4 碼、非 0 開頭的公司代號（公司代號＝普通股代號）；排除臺灣存託憑證（產業別 91、名稱含 -DR、91xx）。
  ETF／ETN（0 開頭）、特別股（2881A 等 5 碼）、權證（6 碼）與興櫃（不在這兩份名單）自然不會進來。
- 產業別：官方產業別代碼 → 中文名稱（TW_INDUSTRY_NAMES，見下方出處）；欄位已是中文時原樣使用。
- 已發行普通股數：t187ap03_L「已發行普通股數或TDR原股發行股數」、mopsfin_t187ap03_O IssueShares 等欄位；
  沒有時以「實收資本額 ÷ 普通股每股面額 − 特別股股數」推算；都取不到時為 None（市值為 null）。

台股每日行情（增量）
- 證交所 OpenAPI exchangeReport/STOCK_DAY_ALL（全部上市證券當日行情，含 Date、Change）
- 櫃買中心 OpenAPI tpex_mainboard_daily_close_quotes（全部上櫃證券當日行情）
  價格是官方原始成交價，與 Yahoo（auto_adjust=False 的 Close：只做分割調整、不含股利還原）在最新一天相同。
  沒有成交（收盤 -- 或空白）的股票給一根「平盤、量 0」的 K，與 Yahoo 對台股無成交日的寫法一致。

美股名單與公司資料
- NasdaqTrader nasdaqlisted.txt / otherlisted.txt：ETF、Test Issue 旗標直接排除；權證、權利、單位、特別股、
  債券／票據、基金、SPAC、when-issued 等依代號後綴與證券名稱排除（規則與 atlas/scripts/symbol-directory.ts 相同）。
- Nasdaq stock screener API：sector、industry、marketCap（英文分類原樣保留）。

Yahoo Finance
- v8 chart（日 K 歷史，close 為分割調整後收盤，與 data/*.csv 同口徑）、v7 quote（批次當日行情）。
"""
import math
import re
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


# =========================================================================
# 共用
# =========================================================================
def number(value):
    """'1,255.00' / '+3.00' / '$227.52' / 12 → float；'--'、空白、NaN → None。"""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        value = float(value)
        return value if math.isfinite(value) else None

    text = str(value).strip().replace(",", "").replace("$", "").replace("＋", "+")

    if text.startswith("X") or text.startswith("x"):
        # 證交所「X」表示除權息等不比價，數值仍是對參考價的漲跌
        text = text[1:]

    if not text or text in {"-", "--", "---", "N/A", "NA", "null", "None"}:
        return None

    try:
        value = float(text)
    except ValueError:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)

        if not match:
            return None

        value = float(match.group(0))

    return value if math.isfinite(value) else None


def roc_date(value):
    """'1151007'（民國）/ '20261007' / '2026-10-07' / '115/10/07' → date；無法辨識回傳 None。"""
    text = re.sub(r"[^0-9]", "", str(value or ""))

    try:
        if len(text) == 7:
            return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))

        if len(text) == 8:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))

        if len(text) == 6 and "/" in str(value):
            return date(int(text[:2]) + 1911, int(text[2:4]), int(text[4:6]))

    except ValueError:
        return None

    return None


def first_value(record, *keys):
    for key in keys:
        value = record.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


def first_matching(record, pattern):
    """第一個欄位名稱符合 pattern 且有值的欄位值（官方欄位名稱偶有改版時的容錯）。"""
    regex = re.compile(pattern)

    for key, value in record.items():
        if regex.search(str(key)) and value is not None and str(value).strip():
            return str(value).strip()

    return ""


class SourceFormatError(ValueError):
    """來源回應格式不符；訊息會寫進 Actions 日誌，需說清楚哪裡不符。"""


def require_records(payload, source):
    if not isinstance(payload, list):
        raise SourceFormatError(
            f"{source}：預期 JSON 陣列，實際是 {type(payload).__name__}"
            f"（前 120 字：{str(payload)[:120]!r}）"
        )

    records = [row for row in payload if isinstance(row, dict)]

    if payload and not records:
        raise SourceFormatError(f"{source}：陣列內沒有物件（第一筆：{str(payload[0])[:120]!r}）")

    return records


# =========================================================================
# 台股：產業別
# =========================================================================
# 證交所／櫃買中心「產業別」代碼（公開資訊觀測站 t187ap03_L / mopsfin_t187ap03_O 的產業別欄位使用同一套代碼）。
# 出處：臺灣證券交易所上市公司產業類別、證券櫃檯買賣中心上櫃公司產業類別（2023-07 新增 35–38 類、
# 16 類更名為「觀光餐旅」）。07「化學生技醫療」、13「電子工業」為已拆分的舊類別，只為容錯保留。
TW_INDUSTRY_NAMES = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "07": "化學生技醫療",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "13": "電子工業",
    "14": "建材營造業",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險業",
    "18": "貿易百貨業",
    "19": "綜合",
    "20": "其他業",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "34": "電子商務",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
    "80": "管理股票",
    "91": "存託憑證",
}
TDR_INDUSTRY_CODE = "91"


def tw_industry_name(value, unknown=None):
    """產業別代碼（'24'、'024'、24）→ 中文名稱；已是中文時原樣回傳；未知代碼記入 unknown（set）並回傳 None。"""
    text = str(value if value is not None else "").strip()

    if not text:
        return None

    if re.fullmatch(r"\d{1,3}", text):
        code = text[-2:].zfill(2)
        name = TW_INDUSTRY_NAMES.get(code)

        if name is None and unknown is not None:
            unknown.add(code)

        return name

    return text[:40]


# =========================================================================
# 台股：上市／上櫃公司基本資料
# =========================================================================
TW_CODE = re.compile(r"^[1-9][0-9]{3}$")


def _shares_from_record(record):
    """已發行普通股數；沒有明確欄位時以實收資本額 ÷ 每股面額 − 特別股推算；都沒有回傳 None。"""
    explicit = number(
        first_matching(record, r"已發行普通股數")
        or first_value(
            record,
            "IssueShares", "IssuedShares", "IssuedCommonShares", "NumberOfSharesIssued",
            "CommonStockSharesIssued", "SharesIssued",
        )
    )

    if explicit is not None and explicit > 0:
        return int(round(explicit))

    capital = number(
        first_matching(record, r"實收資本額")
        or first_value(record, "Paidin.Capital.NTDollars", "PaidInCapital", "PaidinCapital", "Capital")
    )
    par = number(
        first_matching(record, r"普通股每股面額")
        or first_value(record, "ParValueOfCommonStock", "ParValue")
    )

    if capital is None or capital <= 0 or par is None or par <= 0:
        return None

    preferred = number(
        first_matching(record, r"^特別股")
        or first_value(record, "PreferredStock.shares", "PreferredStock", "PreferredShares")
    ) or 0
    shares = capital / par - max(preferred, 0)

    return int(round(shares)) if shares > 0 else None


def parse_tw_companies(payload, exchange, unknown_industries=None):
    """
    t187ap03_L（exchange='TWSE'）或 mopsfin_t187ap03_O（'TPEx'）→ {symbol: {name, exchange, industry, shares}}。
    TDR、非 4 碼代號一律排除。
    """
    source = "t187ap03_L" if exchange == "TWSE" else "mopsfin_t187ap03_O"
    records = require_records(payload, source)
    suffix = "TW" if exchange == "TWSE" else "TWO"
    items = {}

    if records and not any(
        first_value(record, "公司代號", "SecuritiesCompanyCode", "CompanyCode", "Code") for record in records
    ):
        raise SourceFormatError(
            f"{source}：找不到公司代號欄位（公司代號／SecuritiesCompanyCode），第一筆欄位：{sorted(records[0])[:12]}"
        )

    for record in records:
        code = first_value(record, "公司代號", "SecuritiesCompanyCode", "CompanyCode", "Code")
        name = first_value(record, "公司簡稱", "CompanyAbbreviation", "公司名稱", "CompanyName", "Name")
        industry_raw = first_value(record, "產業別", "SecuritiesIndustryCode", "IndustryCode", "Industry")

        if not TW_CODE.match(code) or not name:
            continue

        if (
            str(industry_raw).strip().lstrip("0") == TDR_INDUSTRY_CODE
            or "存託憑證" in industry_raw
            or re.search(r"-DR$", name.upper())
            or code.startswith("91")
        ):
            continue

        items[f"{code}.{suffix}"] = {
            "name": name,
            "exchange": exchange,
            "industry": tw_industry_name(industry_raw, unknown_industries),
            "shares": _shares_from_record(record),
        }

    return items


def tw_companies_from_quotes(snapshot, exchange):
    """
    退路：公司基本資料取不到時，由當日行情推出普通股名單（4 碼、非 0 開頭、名稱不含 -DR），沒有產業與股數。
    """
    suffix = ".TW" if exchange == "TWSE" else ".TWO"
    items = {}

    for symbol, bar in (snapshot or {}).get("bars", {}).items():
        if not symbol.endswith(suffix):
            continue

        code = symbol.split(".")[0]
        name = bar.get("name") or ""

        if not TW_CODE.match(code) or code.startswith("91") or re.search(r"-DR$", name.upper()):
            continue

        items[symbol] = {"name": name, "exchange": exchange, "industry": None, "shares": None}

    return items


# =========================================================================
# 台股：每日全市場行情
# =========================================================================
def _tw_bar(code, name, day, open_, high, low, close, volume, change, suffix):
    return {
        "symbol": f"{code}.{suffix}",
        "name": name,
        "date": day.isoformat() if day else None,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        # 參考價（＝收盤 − 漲跌）：除權息、減資、分割時與前一日收盤不同
        "reference": (close - change) if close is not None and change is not None else None,
    }


def _tw_snapshot(bars, source):
    dates = sorted({bar["date"] for bar in bars.values() if bar["date"]})

    if not dates:
        raise SourceFormatError(f"{source}：沒有可辨識的資料日期（Date 欄位）")

    # 同一份行情只有一個資料日；混雜時以最多的為準
    counts = {}

    for bar in bars.values():
        if bar["date"]:
            counts[bar["date"]] = counts.get(bar["date"], 0) + 1

    as_of = max(counts, key=lambda day: (counts[day], day))

    return {
        "source": source,
        "date": as_of,
        "bars": {symbol: bar for symbol, bar in bars.items() if bar["date"] == as_of},
    }


def parse_twse_day_all(payload, fallback_date=None):
    """
    證交所 STOCK_DAY_ALL：OpenAPI（物件陣列：Date, Code, Name, TradeVolume, OpeningPrice, HighestPrice,
    LowestPrice, ClosingPrice, Change …）或 www.twse.com.tw 的 rwd JSON（date + fields + data）。
    → {"source", "date": 'YYYY-MM-DD', "bars": {symbol: bar}}（只留 4 碼代號）。
    """
    source = "TWSE STOCK_DAY_ALL"
    rows = []

    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        fields = [str(field).strip() for field in payload.get("fields") or []]
        day = roc_date(payload.get("date")) or fallback_date

        if not fields:
            raise SourceFormatError(f"{source}：rwd JSON 缺少 fields")

        for values in payload["data"]:
            if isinstance(values, list):
                record = dict(zip(fields, values))
                record.setdefault("日期", day.isoformat() if day else "")
                rows.append(record)
    else:
        rows = require_records(payload, source)

    bars = {}

    for record in rows:
        code = first_value(record, "Code", "證券代號")

        if not TW_CODE.match(code):
            continue

        day = roc_date(first_value(record, "Date", "日期")) or fallback_date
        close = number(first_value(record, "ClosingPrice", "收盤價"))
        bars[f"{code}.TW"] = _tw_bar(
            code,
            first_value(record, "Name", "證券名稱"),
            day,
            number(first_value(record, "OpeningPrice", "開盤價")),
            number(first_value(record, "HighestPrice", "最高價")),
            number(first_value(record, "LowestPrice", "最低價")),
            close if close and close > 0 else None,
            number(first_value(record, "TradeVolume", "成交股數")) or 0.0,
            number(first_value(record, "Change", "漲跌價差")),
            "TW",
        )

    if rows and not bars:
        raise SourceFormatError(
            f"{source}：{len(rows)} 筆資料中沒有 4 碼證券代號（Code／證券代號），第一筆欄位：{sorted(rows[0])[:12]}"
        )

    return _tw_snapshot(bars, source)


def parse_tpex_daily_quotes(payload, fallback_date=None):
    """櫃買 tpex_mainboard_daily_close_quotes（Date, SecuritiesCompanyCode, CompanyName, Close, Change, Open, High, Low, TradingShares …）。"""
    source = "TPEx tpex_mainboard_daily_close_quotes"
    rows = require_records(payload, source)
    bars = {}

    for record in rows:
        code = first_value(record, "SecuritiesCompanyCode", "Code", "代號")

        if not TW_CODE.match(code):
            continue

        close = number(first_value(record, "Close", "ClosingPrice", "收盤"))
        bars[f"{code}.TWO"] = _tw_bar(
            code,
            first_value(record, "CompanyName", "SecuritiesCompanyName", "Name", "名稱"),
            roc_date(first_value(record, "Date", "日期")) or fallback_date,
            number(first_value(record, "Open", "OpeningPrice", "開盤")),
            number(first_value(record, "High", "HighestPrice", "最高")),
            number(first_value(record, "Low", "LowestPrice", "最低")),
            close if close and close > 0 else None,
            number(first_value(record, "TradingShares", "TradeVolume", "TradingVolume", "成交股數")) or 0.0,
            number(first_value(record, "Change", "漲跌")),
            "TWO",
        )

    if rows and not bars:
        raise SourceFormatError(
            f"{source}：{len(rows)} 筆資料中沒有 4 碼代號（SecuritiesCompanyCode），第一筆欄位：{sorted(rows[0])[:12]}"
        )

    return _tw_snapshot(bars, source)


# =========================================================================
# 美股：NasdaqTrader 名單（規則同 atlas/scripts/symbol-directory.ts）
# =========================================================================
def parse_pipe_table(text):
    lines = [line for line in str(text or "").lstrip("﻿").splitlines() if line.strip()]

    if not lines:
        return []

    header = [cell.strip() for cell in lines[0].split("|")]
    rows = []

    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue

        cells = line.split("|")

        if len(cells) < len(header):
            continue

        rows.append({name: (cells[index] or "").strip() for index, name in enumerate(header)})

    return rows


US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,2})?$")
_US_DERIVATIVE_SUFFIX = re.compile(r"^(U|UN|WS|W|WI|WD|R|RT|CL|CV|PR|P[A-Z])$")


def normalize_us_symbol(raw):
    """BRK.B / BRK/B / BRK B → BRK-B；特別股、單位、權證、權利、when-issued 後綴 → None。"""
    symbol = str(raw or "").strip().upper()

    if not symbol or re.search(r"[$=+^#*!%~]", symbol):
        return None

    match = re.fullmatch(r"([A-Z][A-Z0-9]{0,6})(?:[./ ]([A-Z0-9]{1,3}))?", symbol)

    if not match:
        return None

    root, suffix = match.group(1), match.group(2)

    if suffix is None:
        return root

    if _US_DERIVATIVE_SUFFIX.match(suffix) or len(suffix) > 2:
        return None

    yahoo = f"{root}-{suffix}"

    return yahoo if US_SYMBOL.match(yahoo) else None


_EXCLUDE_NAME_PATTERNS = [
    (re.compile(r"\bwarrants?\b", re.I), "warrant"),
    (re.compile(r"\b(?:etf|etn|exchange[- ]traded)\b", re.I), "etf"),
    (re.compile(r"\bfunds?\b", re.I), "fund"),
    (re.compile(r"\bpreferred\b|\bpref(?:erence)?\b|\bpfd\b|\bperpetual\b|\bcumulative\b|\bredeemable\b", re.I), "preferred"),
    (re.compile(r"%"), "fixed-income"),
    (re.compile(r"\bnotes?\b|\bdebentures?\b|\bbonds?\b|\bdue \d{4}\b|\bsenior secured\b|\bsubordinated\b", re.I), "debt"),
    (re.compile(r"\bmunicipal\b|\bclosed[- ]end\b|\btax[- ]free\b", re.I), "fund"),
    (re.compile(r"\b(?:income|credit|dividend|yield|treasury|opportunit(?:y|ies)|municipal|bond)\b[^|]*\btrust\b", re.I), "fund"),
    (re.compile(r"\bacquisition (?:corp(?:oration)?|co|company|ltd|limited|inc)\b|\bblank check\b", re.I), "spac"),
    (re.compile(r"\bwhen[- ]issued\b|\bwhen issued\b", re.I), "when-issued"),
    (re.compile(r"\bcontingent value\b|\bescrow\b|\btest (?:stock|issue)\b", re.I), "other"),
]
_BENEFICIAL_INTEREST = re.compile(r"\bbeneficial interest\b", re.I)
_REAL_ASSET_TRUST = re.compile(r"realty|reit|royalty|propert|real estate|residential|apartment|hotel|lodging|mortgage", re.I)
_UNITS = re.compile(r"\bunits?\b", re.I)
_OPERATING_UNITS = re.compile(r"\bcommon units?\b|\blimited partnership units?\b|\bpartnership units?\b", re.I)
_RIGHTS = re.compile(r"\brights?\b", re.I)
_COMMON = re.compile(r"\bcommon (?:stock|shares)\b|\bordinary shares\b", re.I)


def us_exclusion_reason(name):
    """不列入的原因（warrant / etf / fund / preferred / debt / spac / unit / right …），營運公司股權回傳 None。"""
    name = str(name or "")

    for pattern, reason in _EXCLUDE_NAME_PATTERNS:
        if pattern.search(name):
            return reason

    if _BENEFICIAL_INTEREST.search(name) and not _REAL_ASSET_TRUST.search(name):
        return "fund"

    if _UNITS.search(name) and not _OPERATING_UNITS.search(name):
        return "unit"

    if _RIGHTS.search(name) and not _COMMON.search(name):
        return "right"

    return None


_NAME_SUFFIX = re.compile(
    r"\s*[,-]?\s*(?:new\s+)?(?:(?:class|series)\s+[a-z0-9]+\s+)?(?:common\s+(?=shares of beneficial interest|units?\b))?"
    r"(?:common stock|common shares?|ordinary shares?|common units?(?:\s+representing.*)?|limited partnership units?.*|"
    r"subordinate voting shares?|voting shares?|shares of beneficial interest|american depositary shares?.*|"
    r"american depository shares?.*|american depositary receipts?.*|sponsored adr.*|depositary shares?.*|ads|adr|shares|stock)\s*$",
    re.I,
)


def clean_us_name(raw):
    name = str(raw or "").strip()
    dash = name.find(" - ")

    if dash > 0:
        name = name[:dash]

    for _ in range(2):
        name = _NAME_SUFFIX.sub("", name).strip()

    return name.rstrip(" ,") or str(raw or "").strip()


US_OTHER_EXCHANGES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}


def parse_us_listed(nasdaq_text, other_text, excluded=None):
    """
    nasdaqlisted.txt + otherlisted.txt → {symbol: {name, exchange}}。
    excluded（dict）累計排除原因：test / etf / symbol / 名稱規則的原因。
    """
    items = {}
    excluded = excluded if excluded is not None else {}

    def drop(reason):
        excluded[reason] = excluded.get(reason, 0) + 1

    nasdaq_rows = parse_pipe_table(nasdaq_text)
    other_rows = parse_pipe_table(other_text)

    if nasdaq_text and nasdaq_rows and "Symbol" not in nasdaq_rows[0]:
        raise SourceFormatError(f"nasdaqlisted.txt：缺少 Symbol 欄，表頭為 {list(nasdaq_rows[0])[:8]}")

    if other_text and other_rows and "ACT Symbol" not in other_rows[0] and "CQS Symbol" not in other_rows[0]:
        raise SourceFormatError(f"otherlisted.txt：缺少 ACT Symbol 欄，表頭為 {list(other_rows[0])[:8]}")

    for row in nasdaq_rows:
        if row.get("Test Issue") == "Y":
            drop("test")
            continue

        if row.get("ETF") == "Y" or row.get("NextShares") == "Y":
            drop("etf")
            continue

        name = row.get("Security Name", "")
        reason = us_exclusion_reason(name)

        if reason:
            drop(reason)
            continue

        symbol = normalize_us_symbol(row.get("Symbol", ""))

        if not symbol:
            drop("symbol")
            continue

        items.setdefault(symbol, {"name": clean_us_name(name), "exchange": "NASDAQ"})

    for row in other_rows:
        if row.get("Test Issue") == "Y":
            drop("test")
            continue

        if row.get("ETF") == "Y":
            drop("etf")
            continue

        exchange = US_OTHER_EXCHANGES.get(row.get("Exchange", ""))

        if not exchange:
            drop("exchange")
            continue

        name = row.get("Security Name", "")
        reason = us_exclusion_reason(name)

        if reason:
            drop(reason)
            continue

        symbol = normalize_us_symbol(row.get("ACT Symbol") or row.get("CQS Symbol") or "")

        if not symbol:
            drop("symbol")
            continue

        items.setdefault(symbol, {"name": clean_us_name(name), "exchange": exchange})

    return items


# =========================================================================
# 美股：Nasdaq stock screener（sector / industry / marketCap）
# =========================================================================
def parse_nasdaq_screener(payload):
    """
    api.nasdaq.com/api/screener/stocks?tableonly=true&download=true →
    {symbol: {name, sector, industry, marketCap, lastSale, country}}（symbol 已轉成 Yahoo 寫法）。
    """
    source = "Nasdaq screener"

    if not isinstance(payload, dict):
        raise SourceFormatError(f"{source}：預期 JSON 物件，實際是 {type(payload).__name__}")

    data = payload.get("data")

    if not isinstance(data, dict):
        status = payload.get("status")
        raise SourceFormatError(f"{source}：缺少 data（status={str(status)[:160]}）")

    rows = data.get("rows")

    if rows is None and isinstance(data.get("table"), dict):
        rows = data["table"].get("rows")

    if not isinstance(rows, list):
        raise SourceFormatError(f"{source}：data.rows 不是陣列（data 欄位：{sorted(data)[:8]}）")

    items = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = normalize_us_symbol(row.get("symbol"))

        if not symbol:
            continue

        def text(key):
            value = str(row.get(key) or "").strip()
            return value[:60] if value and value.lower() not in {"n/a", "na", "-"} else None

        market_cap = number(row.get("marketCap"))
        last_sale = number(row.get("lastsale"))
        items[symbol] = {
            "name": text("name"),
            "sector": text("sector"),
            "industry": text("industry"),
            "marketCap": market_cap if market_cap and market_cap > 0 else None,
            "lastSale": last_sale if last_sale and last_sale > 0 else None,
            "country": text("country"),
        }

    return items


def implied_shares(market_cap, last_sale):
    """Nasdaq 的市值 ÷ 其最後成交價 → 流通股數（用我們的收盤價重算市值，與 asOf 一致）。"""
    if not market_cap or not last_sale or market_cap <= 0 or last_sale <= 0:
        return None

    return int(round(market_cap / last_sale))


# =========================================================================
# Yahoo Finance
# =========================================================================
MARKET_TIMEZONES = {"TW": "Asia/Taipei", "US": "America/New_York"}


def _zone(name):
    if ZoneInfo is None:
        return None

    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone names fall back to the gmtoffset
        return None


def local_day(timestamp, timezone_name=None, gmtoffset=None):
    """Unix 秒 → 交易所當地日期。"""
    zone = _zone(timezone_name) if timezone_name else None

    if zone is not None:
        return datetime.fromtimestamp(int(timestamp), zone).date()

    offset = int(gmtoffset or 0)
    return (datetime.fromtimestamp(int(timestamp), timezone.utc) + timedelta(seconds=offset)).date()


def parse_yahoo_chart(payload, symbol=""):
    """
    v8 chart 日 K → (rows, splits, meta)。
    rows：[(YYYY-MM-DD, open, high, low, close, volume)] 依日期排序、同日取最後一筆；
    未知成交量保留 None，由快取區分缺值與真實 0 量。close 是 Yahoo 分割調整後、未含股利還原的收盤（同 data/*.csv）。
    splits：[(YYYY-MM-DD, numerator, denominator)]。無資料時 rows 為空串列。
    """
    chart = (payload or {}).get("chart") if isinstance(payload, dict) else None

    if not isinstance(chart, dict):
        raise SourceFormatError(f"Yahoo chart {symbol}：缺少 chart 物件")

    if chart.get("error"):
        error = chart["error"]
        description = error.get("description") if isinstance(error, dict) else error
        raise SourceFormatError(f"Yahoo chart {symbol}：{str(description)[:160]}")

    results = chart.get("result")

    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return [], [], {}

    result = results[0]
    meta = result.get("meta") or {}
    zone_name = meta.get("exchangeTimezoneName")
    gmtoffset = meta.get("gmtoffset")
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    columns = [quotes.get(key) or [] for key in ("open", "high", "low", "close", "volume")]
    rows = {}

    for index, stamp in enumerate(timestamps):
        try:
            values = [column[index] if index < len(column) else None for column in columns]
        except TypeError:
            continue

        open_, high, low, close, volume = (number(value) for value in values)

        day = local_day(stamp, zone_name, gmtoffset).isoformat()
        rows[day] = (day, open_, high, low, close, volume)

    splits = []

    for event in ((result.get("events") or {}).get("splits") or {}).values():
        if not isinstance(event, dict) or event.get("date") is None:
            continue

        numerator = number(event.get("numerator"))
        denominator = number(event.get("denominator"))

        if numerator and denominator:
            splits.append((local_day(event["date"], zone_name, gmtoffset).isoformat(), numerator, denominator))

    return [rows[day] for day in sorted(rows)], sorted(splits), meta


def parse_yahoo_quotes(payload, market_of):
    """
    v7 quote → {symbol: bar}；bar：{date, open, high, low, close, volume, previousClose, marketState,
    shares, marketCap, quoteType}。開高低收不完整的報價略過（沒有成交或資料未齊）。
    market_of(symbol) → 'TW' / 'US'，用來決定日期的時區。
    """
    response = (payload or {}).get("quoteResponse") if isinstance(payload, dict) else None

    if not isinstance(response, dict):
        raise SourceFormatError("Yahoo quote：缺少 quoteResponse")

    if response.get("error"):
        raise SourceFormatError(f"Yahoo quote：{str(response['error'])[:160]}")

    bars = {}

    for quote in response.get("result") or []:
        if not isinstance(quote, dict):
            continue

        symbol = str(quote.get("symbol") or "").upper()
        stamp = quote.get("regularMarketTime")

        if not symbol or stamp is None:
            continue

        if isinstance(stamp, dict):
            stamp = stamp.get("raw")

        def value(key):
            raw = quote.get(key)
            return number(raw.get("raw") if isinstance(raw, dict) else raw)

        open_, high, low, close = (
            value("regularMarketOpen"), value("regularMarketDayHigh"),
            value("regularMarketDayLow"), value("regularMarketPrice"),
        )

        zone_name = quote.get("exchangeTimezoneName") or MARKET_TIMEZONES.get(market_of(symbol))

        offset = quote.get("gmtOffSetMilliseconds")

        try:
            day = local_day(number(stamp), zone_name, offset / 1000 if isinstance(offset, (int, float)) else None)
        except (TypeError, ValueError, OverflowError):
            continue

        bars[symbol] = {
            "symbol": symbol,
            "date": day.isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": value("regularMarketVolume"),
            "previousClose": value("regularMarketPreviousClose"),
            "marketState": quote.get("marketState"),
            "shares": value("sharesOutstanding"),
            "marketCap": value("marketCap"),
            "quoteType": quote.get("quoteType"),
        }

    return bars

