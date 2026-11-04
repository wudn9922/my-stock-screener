"""美股寬度訊號規則：校準（calibrate.py）與每日（daily.py）共用，純函式、不讀檔不連網。

規則摘要（數字的來源與回測結果見 RESEARCH.md）：

抓低點訊號（每個指數各自判斷）
  條件 A：全市場站上 MA20 比例 ≤ 校準檔的第 10 百分位（ma20.p10）
  條件 B：指數收盤距「近 60 個交易日（含今天）最高收盤」的跌幅 ≥ 該指數的波段幅度 swing
  訊號日 = A 且 B 由不成立變成成立的那一天；距上一個訊號不到 COOLDOWN 個交易日的不算。
  只有 A 成立、B 還不成立 → 狀態 watch（寬度已超賣，等指數回檔到位）。

交易計畫（用來算命中率，也是網站顯示的建議）
  進場：訊號日的下一個交易日開盤價
  停損：進場價 ×(1 − swing)；停利：進場價 ×(1 + swing)；最長持有 MAX_HOLD 個交易日（含進場日），到期以收盤價出場
  盤中最低 ≤ 停損 → 停損；盤中最高 ≥ 停利 → 停利；同一天兩個都碰到 → 保守算停損；
  開盤就跳空越過 → 以開盤價成交。
  「抓到低點」= 先碰到停利。

超買：只做標示（趨勢強勢），不產生賣出訊號——回測中超買後「先跌一個波段幅度」的機率與任意一天相同。
台股：不適用（回測前後段方向相反、全期不顯著）。
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

RULES_VERSION = 1

# 每個指數的波段幅度：依波動度設定（S&P／道瓊 5%、那斯達克／羅素 7%、費半 10%）
INDICES = {
    "^GSPC": {"name": "S&P 500", "swing": 0.05},
    "^IXIC": {"name": "那斯達克", "swing": 0.07},
    "^DJI": {"name": "道瓊工業", "swing": 0.05},
    "^RUT": {"name": "羅素 2000", "swing": 0.07},
    "^SOX": {"name": "費城半導體", "swing": 0.10},
}
OVERSOLD_Q = 0.10          # 條件 A 的分位數
DRAWDOWN_LOOKBACK = 60     # 條件 B 的回看天數
COOLDOWN = 10              # 兩個訊號至少相隔的交易日
MAX_HOLD = 60              # 最長持有交易日
ZONE_QUANTILES = (0.05, 0.10, 0.50, 0.90, 0.95)


# ---------------------------------------------------------------- 分區
def zone(pct: float | None, th: dict) -> str:
    """依校準門檻回傳 extreme_oversold / oversold / neutral / overbought / extreme_overbought / none。"""
    if pct is None or not math.isfinite(pct):
        return "none"
    if pct <= th["p5"]:
        return "extreme_oversold"
    if pct <= th["p10"]:
        return "oversold"
    if pct >= th["p95"]:
        return "extreme_overbought"
    if pct >= th["p90"]:
        return "overbought"
    return "neutral"


def percentile_rank(value: float, history: pd.Series) -> float | None:
    h = history.dropna()
    if not len(h) or value is None or not math.isfinite(value):
        return None
    return float((h <= value).mean() * 100)


# ---------------------------------------------------------------- 訊號
def drawdown_from_high(close: pd.Series, lookback: int = DRAWDOWN_LOOKBACK) -> pd.Series:
    return close / close.rolling(lookback, min_periods=lookback).max() - 1


def low_signal_frame(pct20: pd.Series, close: pd.Series, p10: float, swing: float) -> pd.DataFrame:
    """對齊到 pct20 的日期。欄位：a（寬度超賣）、b（指數回檔到位）、cond、signal（去重後的訊號日）。
    close 為指數收盤；寬度日期上沒有指數收盤時沿用前一筆（最多 3 天）。"""
    c = close.reindex(pct20.index).ffill(limit=3)
    dd = drawdown_from_high(c)
    a = pct20 <= p10
    b = dd <= -swing
    cond = (a & b).fillna(False)
    first = cond & ~cond.shift(1, fill_value=False)
    sig = np.zeros(len(first), bool)
    last = -10 ** 9
    for i in np.flatnonzero(first.to_numpy()):
        if i - last > COOLDOWN:
            sig[i] = True
            last = i
    return pd.DataFrame({"pct20": pct20, "close": c, "drawdown": dd, "a": a.fillna(False),
                         "b": b.fillna(False), "cond": cond, "signal": sig}, index=pct20.index)


# ---------------------------------------------------------------- 交易
@dataclass
class Trade:
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp | None
    entry: float | None
    stop: float | None
    target: float | None
    exit_date: pd.Timestamp | None
    exit: float | None
    outcome: str            # pending（明天開盤才進場）/ open（持有中）/ target / stop / timeout
    days: int
    ret: float | None
    mae: float | None       # 持有期間盤中最深跌幅
    mfe: float | None       # 持有期間盤中最大漲幅
    expires: pd.Timestamp | None = None   # 持有中時：預計到期日（以交易日估算）


def simulate_trade(bars: pd.DataFrame, signal_date: pd.Timestamp, swing: float, max_hold: int = MAX_HOLD) -> Trade:
    """bars：指數日 K（index=日期，欄位 Open/High/Low/Close）。"""
    d = bars.index
    i = int(np.searchsorted(d.values, np.datetime64(signal_date), side="right"))
    if i >= len(d):
        return Trade(signal_date, None, None, None, None, None, None, "pending", 0, None, None, None)
    O, H, L, C = (bars[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    e = O[i] if O[i] > 0 else C[i]
    stop, target = e * (1 - swing), e * (1 + swing)
    end = min(i + max_hold, len(d))
    for j in range(i, end):
        hit_stop, hit_tgt = L[j] <= stop, H[j] >= target
        if hit_stop:
            px = O[j] if (j > i and O[j] < stop) else stop
            return _close(signal_date, d, i, j, e, stop, target, px, "stop", L, H)
        if hit_tgt:
            px = O[j] if (j > i and O[j] > target) else target
            return _close(signal_date, d, i, j, e, stop, target, px, "target", L, H)
    j = end - 1
    if i + max_hold > len(d):        # 還沒滿 max_hold：持有中
        t = _close(signal_date, d, i, j, e, stop, target, C[j], "open", L, H)
        t.expires = _add_sessions(d[j], i + max_hold - 1 - j)
        return t
    return _close(signal_date, d, i, j, e, stop, target, C[j], "timeout", L, H)


def _close(sig, d, i, j, e, stop, target, px, outcome, L, H):
    lows = np.append(L[i:j], px if outcome in ("stop", "target") else L[j])
    highs = np.append(H[i:j], px if outcome in ("stop", "target") else H[j])
    return Trade(sig, d[i], float(e), float(stop), float(target), d[j], float(px), outcome, j - i + 1,
                 float(px / e - 1), float(lows.min() / e - 1), float(highs.max() / e - 1))


def _add_sessions(day: pd.Timestamp, n: int) -> pd.Timestamp:
    """以美股交易日（週一至週五，不含國定假日）粗估 n 個交易日後的日期；網站只拿來顯示。"""
    return (pd.Timestamp(day) + pd.offsets.BDay(max(n, 0))).normalize()


def trades_for(frame: pd.DataFrame, bars: pd.DataFrame, swing: float) -> list[Trade]:
    """frame = low_signal_frame(...)；依序模擬，持有中不接受新訊號。"""
    out, busy_until = [], None
    for day in frame.index[frame["signal"].to_numpy()]:
        if busy_until is not None and day <= busy_until:
            continue
        t = simulate_trade(bars, day, swing)
        out.append(t)
        busy_until = t.exit_date if t.exit_date is not None else day
    return out
