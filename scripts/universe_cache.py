"""
全市場 universe 的滾動日 K 運算快取（Actions cache；永久副本另存 kline-archive 分支）。
純函式與檔案讀寫，不連網路。

目錄（--cache-dir，預設 .cache/universe-full）：
- bars/<SYMBOL>.csv：Date,Open,High,Low,Close,Volume，與 data/*.csv 同格式同口徑（Yahoo 分割調整後、未含股利還原的
  收盤；成交量為股數）。report_metrics.read_price_csv / build_universe、build_breadth.py、build_breakouts.py
  都能直接把這個目錄當 data_dir 讀。每檔保留最近 KEEP_BARS 根。
- state.json：每檔的狀態（完整歷史抓取日、最後驗證日、最後一根 K、近 20 根成交量、連續失敗次數…）。
- lists.json：最後一次成功取得的名單與公司資料（來源失敗時的退路）。

增量規則
- 完整歷史（Yahoo chart 2y）抓過一次才算「完整」，才會輸出；之後每天：
  台股用證交所／櫃買的全市場當日行情、美股用 Yahoo 批次報價，各一根 K 直接接在快取後面（不需逐檔請求）。
- 接 K 前檢查：快取最後一根必須是前一個交易日（否則是缺口 → 逐檔補抓近期）；當日的參考價／前收盤
  與快取最後收盤差超過 PRICE_TOLERANCE（除權、減資、分割）→ 整檔重抓，並在之後 SUSPECT_DAYS 天每天再驗證一次
  （Yahoo 有時晚一兩天才調整歷史）。
- 逐檔補抓近期時比對重疊區間的收盤，不符也整檔重抓。另外每天輪流用近期資料驗證最久沒驗證的股票。
"""
import json
import math
import os
import tempfile
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd
from kline_integrity import accept_frame, usable_frame

KEEP_BARS = 320
# 重疊區間收盤相差超過此比例視為歷史被調整（分割、除權、減資）
PRICE_TOLERANCE = 0.005
COMPARE_BARS = 10
# 價格調整後的幾天內每天再用近期資料驗證（Yahoo 偶爾晚一兩天才調整歷史）
SUSPECT_DAYS = 5
# 每檔多久用近期資料驗證一次
VERIFY_DAYS = 30
# 台股漲跌幅限制 10%；沒有官方漲跌欄位時，超過此幅度視為可能有除權／減資
TW_LIMIT_MOVE = 0.105
# 收盤後多久才接受當天的 K（之前的視為盤中未完成）
SESSION_DONE = {"TW": dtime(14, 45), "US": dtime(16, 30)}
MARKET_TIMEZONES = {"TW": "Asia/Taipei", "US": "America/New_York"}
STATE_VERSION = 1
PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
# Shared with report_metrics: calculations retain the legacy zero fallback, while
# cache CSVs preserve unknown source volumes as empty cells for complete-window means.
UNKNOWN_VOLUME_DATES = "atlas_unknown_volume_dates"


# =========================================================================
# 檔案
# =========================================================================
def write_atomic(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            file.write(text)

        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    write_atomic(path, json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def bars_dir(cache_dir):
    return os.path.join(cache_dir, "bars")


def bars_path(cache_dir, symbol):
    return os.path.join(bars_dir(cache_dir), f"{symbol}.csv")


def frame_from_rows(rows):
    """[(YYYY-MM-DD, o, h, l, c, v)] → 清理過的 DataFrame（DatetimeIndex，已排序去重）。"""
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    frame = pd.DataFrame(
        [list(row[1:6]) for row in rows],
        columns=PRICE_COLUMNS,
        index=pd.to_datetime([row[0] for row in rows]),
    )

    return clean(frame)


def _volume_source_frame(frame):
    """Recover unknown source cells before acceptance, concatenation or serialization."""
    if frame is None or frame.empty:
        return frame
    unknown = set(frame.attrs.get(UNKNOWN_VOLUME_DATES, ()))
    if not unknown:
        return frame
    frame = frame.copy()
    mask = [pd.Timestamp(day).tz_localize(None).isoformat() in unknown for day in frame.index]
    frame.loc[mask, "Volume"] = math.nan
    frame.attrs.pop(UNKNOWN_VOLUME_DATES, None)
    return frame


def clean(frame):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    frame = _volume_source_frame(frame)[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([math.inf, -math.inf], math.nan).dropna(subset=["Open", "High", "Low", "Close"])
    frame = frame[frame["Close"] > 0].copy()
    frame["_volume_unknown"] = frame["Volume"].isna() | (frame["Volume"] < 0)
    frame["Volume"] = frame["Volume"].fillna(0).clip(lower=0)
    frame.index = pd.to_datetime(frame.index).normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame.index.name = "Date"
    unknown = tuple(pd.Timestamp(day).tz_localize(None).isoformat() for day in frame.index[frame["_volume_unknown"]])
    frame = frame.drop(columns=["_volume_unknown"])
    frame.attrs[UNKNOWN_VOLUME_DATES] = unknown

    return frame


def read_bars(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=PRICE_COLUMNS)

    try:
        return usable_frame(clean(pd.read_csv(path, index_col=0, parse_dates=True)), os.path.basename(path).removesuffix(".csv"))
    except Exception as exc:  # noqa: BLE001 - a broken file is refetched
        print(f"⚠️ {os.path.basename(path)} 快取讀取失敗：{type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=PRICE_COLUMNS)


def _price(value):
    if 0 < float(value) < 0.00005:
        return format(float(value), '.15g')
    text = f"{round(float(value), 4):.4f}".rstrip("0").rstrip(".")
    return text if "." in text else text + ".0"


def write_bars(path, frame, keep=KEEP_BARS, refetch=None, now=None):
    """CSV 原子寫入：價格 4 位、已知成交量整數、未知成交量空欄；回傳相容的計算 frame。"""
    symbol = os.path.basename(path).removesuffix(".csv")
    frame = accept_frame(_volume_source_frame(frame), symbol, refetch=refetch, now=now,
                         source_url="https://query1.finance.yahoo.com/v8/finance/chart/" + symbol)
    frame = clean(frame).tail(keep)
    lines = ["Date,Open,High,Low,Close,Volume"]
    unknown = set(frame.attrs.get(UNKNOWN_VOLUME_DATES, ()))

    for day, row in zip(frame.index, frame.itertuples(index=False)):
        volume = "" if pd.Timestamp(day).tz_localize(None).isoformat() in unknown else str(int(round(row.Volume)))
        lines.append(
            f"{day:%Y-%m-%d},{_price(row.Open)},{_price(row.High)},{_price(row.Low)},"
            f"{_price(row.Close)},{volume}"
        )

    write_atomic(path, "\n".join(lines) + "\n")
    return frame


def summarize(frame):
    """state 用的摘要：first / last / bars / vol20（近 20 根成交量合計）/ close。"""
    if frame is None or frame.empty:
        return {"first": None, "last": None, "bars": 0, "vol20": 0, "close": None}

    return {
        "first": frame.index[0].strftime("%Y-%m-%d"),
        "last": frame.index[-1].strftime("%Y-%m-%d"),
        "bars": int(len(frame)),
        "vol20": int(round(float(frame["Volume"].tail(20).sum()))),
        "close": round(float(frame["Close"].iloc[-1]), 4),
    }


# =========================================================================
# 狀態
# =========================================================================
def load_state(cache_dir):
    state = read_json(os.path.join(cache_dir, "state.json"), {}) or {}

    if state.get("version") != STATE_VERSION or not isinstance(state.get("symbols"), dict):
        state = {"version": STATE_VERSION, "symbols": {}}

    return state


def save_state(cache_dir, state):
    write_json(os.path.join(cache_dir, "state.json"), state)


def entry_of(state, symbol):
    return state["symbols"].setdefault(symbol, {})


def is_complete(entry):
    return bool(entry and entry.get("full") and entry.get("last"))


def record_frame(state, symbol, frame, today, full=False, verified=False):
    """CSV 寫入後更新 state（摘要、抓取／驗證日、清除失敗計數）。"""
    entry = entry_of(state, symbol)
    entry.update(summarize(frame))

    if full:
        entry["full"] = today

    if full or verified:
        entry["verified"] = today
        entry.pop("fails", None)
        entry.pop("retryAfter", None)

    return entry


def record_failure(state, symbol, today, reason):
    """單檔請求失敗：連續失敗次數 n → 之後 min(2^(n-1), 14) 天內不再重試。"""
    entry = entry_of(state, symbol)
    fails = int(entry.get("fails") or 0) + 1
    entry["fails"] = fails
    entry["failReason"] = str(reason)[:120]
    wait = min(2 ** (fails - 1), 14)
    entry["retryAfter"] = (date.fromisoformat(today) + timedelta(days=wait)).isoformat()
    return entry


def can_retry(entry, today):
    retry = (entry or {}).get("retryAfter")
    return not retry or retry <= today


# =========================================================================
# 交易日曆
# =========================================================================
class TradingCalendar:
    """由基準指數（^TWII / SPY）日 K 的日期組成；沒有基準時退回平日（週一到週五）。"""

    def __init__(self, dates=None):
        self.dates = sorted({pd.Timestamp(day).strftime("%Y-%m-%d") for day in (dates or [])})

    def add(self, day):
        if day and day not in self.dates:
            self.dates = sorted(self.dates + [day])

    def previous(self, day):
        """day 之前最近的交易日（YYYY-MM-DD）；超出日曆範圍時用平日推算（但不早於日曆內最後一個交易日）。"""
        earlier = [value for value in self.dates if value < day]

        if earlier and day <= self.dates[-1]:
            return earlier[-1]

        current = date.fromisoformat(day) - timedelta(days=1)

        while current.weekday() >= 5:
            current -= timedelta(days=1)

        current = current.isoformat()

        if earlier and current <= earlier[-1]:
            return earlier[-1]

        return current

    def nth_last(self, as_of, count):
        """as_of（含）往回第 count 個交易日。"""
        day = as_of

        for _ in range(count - 1):
            day = self.previous(day)

        return day


# =========================================================================
# 合併與調整偵測
# =========================================================================
def compare_overlap(old, new, tolerance=PRICE_TOLERANCE, count=COMPARE_BARS):
    """
    重疊日期（最近 count 個）的收盤比對。回傳 None（沒有重疊）或
    {"ok": bool, "worst": 最大相對差, "date": 該日, "compared": 筆數}。
    """
    if old is None or new is None or old.empty or new.empty:
        return None

    common = old.index.intersection(new.index)

    if not len(common):
        return None

    common = common[-count:]
    ratios = (new.loc[common, "Close"] / old.loc[common, "Close"] - 1).abs()
    worst_date = ratios.idxmax()
    worst = float(ratios.max())

    return {
        "ok": worst <= tolerance,
        "worst": round(worst, 6),
        "date": worst_date.strftime("%Y-%m-%d"),
        "compared": int(len(common)),
    }


def merge_incremental(old, new, keep=KEEP_BARS):
    """近期資料併入快取：同一天以新資料為準；新資料沒有的日期保留快取。"""
    if old is None or old.empty:
        return clean(new).tail(keep)

    if new is None or new.empty:
        return clean(old).tail(keep)

    combined = pd.concat([_volume_source_frame(old[~old.index.isin(new.index)]), _volume_source_frame(new)])
    combined.attrs.pop(UNKNOWN_VOLUME_DATES, None)
    return clean(combined).tail(keep)


def merge_full(old, new, keep=KEEP_BARS):
    """整檔重抓：以新歷史取代，只保留快取中比新資料更新的 K（例如官方行情已有、Yahoo 還沒有的當日）。"""
    if new is None or new.empty:
        return clean(old).tail(keep)

    if old is None or old.empty:
        return clean(new).tail(keep)

    newer = old[old.index > new.index[-1]]
    combined = pd.concat([_volume_source_frame(new), _volume_source_frame(newer)])
    combined.attrs.pop(UNKNOWN_VOLUME_DATES, None)
    return clean(combined).tail(keep)


def session_done(day, market, now):
    """day（YYYY-MM-DD）的交易時段是否已結束（now 為 aware datetime）。"""
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo(MARKET_TIMEZONES[market]))
    except Exception:  # noqa: BLE001 - without tz data, fall back to a fixed offset
        local = now + timedelta(hours=8 if market == "TW" else -4)

    today = local.date().isoformat()

    if day < today:
        return True

    if day > today:
        return False

    return local.time() >= SESSION_DONE[market]


def drop_unfinished(frame, market, now):
    """去掉盤中（收盤前）的當日 K 與未來日期的 K。"""
    if frame is None or frame.empty:
        return frame

    keep = [session_done(day.strftime("%Y-%m-%d"), market, now) for day in frame.index]
    return frame[keep]


def snapshot_bar(bar, last_close):
    """當日行情 → (YYYY-MM-DD, o, h, l, c, v)；明確零成交量保留 0，未知量保留缺值。"""
    close = bar.get("close")

    if close is None:
        if last_close is None:
            return None
        return (bar["date"], last_close, last_close, last_close, last_close, bar.get("volume"))

    open_ = bar.get("open") or close
    high = bar.get("high") if bar.get("high") is not None else close
    low = bar.get("low") if bar.get("low") is not None else close
    return (bar["date"], open_, high, low, close, bar.get("volume"))


def snapshot_action(entry, bar, calendar, market, tolerance=PRICE_TOLERANCE, check_reference=True):
    """
    完整快取（entry 有 last / close）遇到當日行情 bar 時該做什麼：
    - ("append", row)：接上一根 K
    - ("same", None)：快取已有這一天
    - ("older", None)：行情比快取舊
    - ("gap", detail)：快取最後一根不是前一個交易日（逐檔補抓近期）
    - ("adjusted", detail)：參考價／前收盤與快取收盤不符（整檔重抓）
    check_reference=False：剛整檔重抓完，不再比對參考價（現金除息時 Yahoo 的收盤歷史本來就不會調整；
    若是 Yahoo 晚一步調整，之後 SUSPECT_DAYS 天的近期驗證會抓到）。
    """
    day = bar.get("date")
    last = entry.get("last")
    last_close = entry.get("close")

    if not day or not last:
        return ("gap", "沒有快取")

    if day == last:
        return ("same", None)

    if day < last:
        return ("older", None)

    previous = calendar.previous(day)

    if last < previous:
        return ("gap", f"快取最後 {last}，前一交易日 {previous}")

    reference = bar.get("reference") if market == "TW" else bar.get("previousClose")
    close = bar.get("close")

    if not check_reference:
        pass
    elif last_close and reference:
        difference = abs(reference / last_close - 1)

        if difference > tolerance:
            return ("adjusted", f"參考價 {reference:g} ≠ 快取收盤 {last_close:g}（{difference:.2%}）")

    elif market == "TW" and last_close and close and abs(close / last_close - 1) > TW_LIMIT_MOVE:
        return ("adjusted", f"漲跌 {close / last_close - 1:.1%} 超過漲跌幅限制")

    row = snapshot_bar(bar, last_close)
    return ("append", row) if row else ("gap", "沒有價格")


def append_row(frame, row, keep=KEEP_BARS):
    addition = frame_from_rows([row])
    return merge_incremental(frame, addition, keep)


# =========================================================================
# 可輸出與不可交易
# =========================================================================
def eligibility(entry, cutoff, zero_volume_bars=20):
    """
    快取狀態 → None（可輸出）或排除原因：
    - "missing"：還沒有完整歷史（冷啟動中或 Yahoo 抓不到）
    - "stale"：最後一根 K 早於 cutoff（近 5 個交易日沒有 K：停牌、下市、暫停交易）
    - "noVolume"：近 20 根 K 全部零成交（長期無法成交）
    """
    if not is_complete(entry):
        return "missing"

    if entry["last"] < cutoff:
        return "stale"

    if int(entry.get("bars") or 0) >= zero_volume_bars and int(entry.get("vol20") or 0) <= 0:
        return "noVolume"

    return None


def verify_due(entry, today, days=VERIFY_DAYS):
    verified = (entry or {}).get("verified")

    if not verified:
        return True

    return (date.fromisoformat(today) - date.fromisoformat(verified)).days >= days


def suspect(entry, today):
    until = (entry or {}).get("suspectUntil")
    return bool(until and until >= today)


def mark_suspect(entry, today, days=SUSPECT_DAYS):
    entry["suspectUntil"] = (date.fromisoformat(today) + timedelta(days=days)).isoformat()


def iso_now(now=None):
    return (now or datetime.utcnow()).replace(microsecond=0).isoformat()
