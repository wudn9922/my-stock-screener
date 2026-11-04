"""全市場寬度計算——與網站 scripts/build_breadth.py 同一套定義，用來產生長歷史（校準用）。

定義（對應 build_breadth.py / market_sessions.py）：
- 「有效日」：該日有收盤價且成交量 > 0。
- SMA_n 在每檔自己的 K 線上滾動（缺日不打斷視窗）；至少 n 根 K 才有 SMA。
- 分母：當日有效且已有 SMA_n 的股票；分子：其中 收盤 > SMA_n（嚴格大於）。
- 排除週末，以及「至少 2 檔有報價、但沒有任何一檔成交量 > 0」的全市場休市占位日。
- 交易日：當日有效檔數 ≥ 近 20 個交易日有效檔數下四分位數的一半（排除只有零星報價的假日）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_PERIODS = (20, 60)


def non_trading_dates(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DatetimeIndex:
    present = close.notna() & (close > 0)
    counts = present.sum(axis=1)
    active = (present & (volume > 0)).sum(axis=1)
    unknown = (present & (volume.isna() | (volume < 0))).sum(axis=1)
    reference = counts.rolling(20, min_periods=1).quantile(0.25)
    closed = (counts >= 2) & (counts >= reference * 0.5) & (active == 0) & (unknown == 0)
    closed |= close.index.dayofweek >= 5
    return close.index[closed.to_numpy()]


def breadth_history(close: pd.DataFrame, volume: pd.DataFrame, periods=MA_PERIODS) -> pd.DataFrame:
    """close / volume：日期 × 代號（沒有 K 線 = NaN）。回傳 pct{n}、above{n}、total{n}、count。"""
    close = close.sort_index()
    volume = volume.reindex_like(close)
    drop = non_trading_dates(close, volume)
    close, volume = close.drop(index=drop), volume.drop(index=drop)
    present = close.notna() & (volume > 0)
    out = pd.DataFrame(index=close.index)
    out["count"] = present.sum(axis=1)
    cols = {}
    for n in periods:
        sma = {}
        for sym in close.columns:
            s = close[sym].dropna()
            if len(s) >= n:
                sma[sym] = s.rolling(n, min_periods=n).mean()
        sma = pd.DataFrame(sma).reindex(index=close.index, columns=close.columns)
        valid = sma.notna() & present
        above = valid & (close > sma)
        cols[f"above{n}"] = above.sum(axis=1)
        cols[f"total{n}"] = valid.sum(axis=1)
    for k, v in cols.items():
        out[k] = v
    for n in periods:
        tot = out[f"total{n}"]
        out[f"pct{n}"] = (out[f"above{n}"] / tot.where(tot > 0) * 100).round(1)
    # 交易日：有效檔數不少於近 20 日下四分位數的一半
    ref = out["count"].rolling(20, min_periods=1).quantile(0.25)
    keep = (out["count"] > 0) & (out["count"] >= 0.5 * ref)
    return out[keep]


def barrier_base_rate(bars: pd.DataFrame, swing: float, max_hold: int, start=None, end=None) -> float:
    """任意一天收盤後隔天開盤買進、同一套停損停利，「先碰到停利」的比例（對照組）。"""
    d = bars.index
    O, H, L, C = (bars[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    m = np.ones(len(d), bool)
    if start is not None:
        m &= d >= pd.Timestamp(start)
    if end is not None:
        m &= d < pd.Timestamp(end)
    wins = tot = 0
    for s in np.flatnonzero(m)[:-1]:
        i = s + 1
        if i + max_hold > len(d):
            break
        e = O[i] if O[i] > 0 else C[i]
        stop, tgt = e * (1 - swing), e * (1 + swing)
        lo = np.flatnonzero(L[i:i + max_hold] <= stop)
        hi = np.flatnonzero(H[i:i + max_hold] >= tgt)
        a = lo[0] if len(lo) else 10 ** 9
        b = hi[0] if len(hi) else 10 ** 9
        wins += int(b < a)
        tot += 1
    return wins / tot if tot else float("nan")


def zigzag_lows(close: pd.Series, swing: float) -> pd.DatetimeIndex:
    """收盤價 ZigZag（反轉 ≥ swing）的波段低點日期；只用來畫圖與補充統計。"""
    c = close.dropna()
    v, d = c.to_numpy(), c.index
    lows, trend, ext = [], 0, 0
    for i in range(1, len(v)):
        if trend >= 0:
            if v[i] > v[ext]:
                ext = i
            if v[i] <= v[ext] * (1 - swing):
                trend, ext = -1, i
        else:
            if v[i] < v[ext]:
                ext = i
            elif v[i] >= v[ext] * (1 + swing):
                lows.append(d[ext])
                trend, ext = 1, i
    return pd.DatetimeIndex(lows)
