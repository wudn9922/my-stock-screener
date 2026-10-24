#!/usr/bin/env python3
"""
近一週／近一月全市場「突破買進法」勝率 → docs/report/breakouts.json。

全市場產品由 build_full_market_breakouts.py 呼叫本計算引擎，只發布波段前高、箱型、20EMA 三法。
舊 N 日前高與放量變體保留作相容／研究用途，不再作為產品的「突破前高」。
波段前高採 swing_breakouts.py 的 ATR 波段；盤中嚴格突破即記嘗試，收盤失敗保留關鍵點可重試。
波段法理論進場價＝max(當日開盤, 已確認波段高點)，每個新波段都可貢獻樣本；其餘方法維持收盤進場。
產品四種勝率使用 comparison 欄位：共通收盤門檻＋同一批後續完整5交易日資料，第三組採先觸發判定。
原本持有至asOf的平面統計欄位僅保留供舊格式與研究相容，新畫面不使用這些欄位作四組勝率。

本檔舊命令列模式只讀 data/ 內的日 K CSV（Date,Open,High,Low,Close,Volume），universe 與市場寬度（build_breadth.py）相同：
台股上市櫃普通股（4 碼、非 0 開頭）、美股 S&P 500 ∩ data/（名單取得失敗時退回 data/ 內所有美股 CSV）。

口徑（全部寫進輸出的 params，前端照抄顯示）：
- asOf：各檔最後日期的眾數（與市場寬度相同）；asOf 之後的 K 線一律截掉，不參與任何計算。
- 有效日：平日、有 K 且成交量 > 0，且 High/Low/Close 皆 > 0。無效日直接丟掉、不補值（週末K線視為資料錯誤並計數）；
  所有回看視窗都以「該檔自己的有效 K 線」計數。High/Low 若比收盤還窄（資料錯誤），以收盤補成包含收盤。
- 交易日曆：asOf 以前、有資料檔數達近期水準一半的日期（build_breadth.trading_days）。突破日必須落在日曆上，
  持有天數＝日曆上突破日到 asOf 相隔的交易日數。
- 價格跳空異常：相鄰兩個有效日收盤漲跌幅絕對值 > JUMP_LIMIT（30%）視為疑似分割／減資／資料錯誤，
  該檔「最後一次異常日之前」的 K 線一律不用（等於從異常日重新起算歷史），並計數輸出。
- 突破前高 N（20/60/120）：收盤 > 前 N 個有效日（不含當日）最高價的最大值，嚴格大於。
- 突破箱型：前 BOX_LOOKBACK 個有效日（不含當日）的 (最高−最低)/最低 ≤ 市場門檻（台股 14%、美股 10%，
  約為兩市場 20 日高低區間的下四分位數），且收盤 > 箱頂（＝前 20 日最高價）。
- 放量變體：當日成交量 ≥ 前 20 個有效日均量 × 1.5（均量不含當日）。
- 突破20EMA：前日收盤 ≤ 前日 EMA20，當日收盤 > 當日 EMA20。EMA 以最初 20 日收盤均值起算，
  之後以 2/21 平滑；前 10 個有效日沒有向上穿越才算新訊號。EMA 只用截至當日收盤的資料。
- 同一波不重複：突破當日之前 COOLDOWN 個有效日都沒有「收盤 > 各自前 N 日高」才算新突破；
  每檔每個策略在窗口內只取第一次新突破。回看資料不足（< N + COOLDOWN 根）的股票不納入該策略。
- 窗口：近 1 週＝日曆上最後 5 個交易日（含 asOf），近 1 月＝最後 21 個交易日。
- 結果：買在突破日收盤、以 asOf 收盤標記；勝＝報酬 > 0；突破失敗＝asOf 收盤 < 突破參考價（前高或箱頂）。
  統計只用持有 ≥ MATURE_DAYS（3）個交易日的成熟樣本；n 為窗口內全部突破（含未成熟）。
- 基準：每一筆成熟突破，取同一進場日、全 universe 有效股票從該日收盤持有到 asOf 的上漲比例與平均報酬，
  再對所有突破平均（＝與策略相同的進場日分布與持有天數）。另給窗口層級的全市場基準
  （窗口內每個成熟進場日等權平均）。
- 偏誤：universe 是「現在」的名單（存活者偏差）；CSV 歷史約 200 根，120 日前高只涵蓋約半年；
  台股為未還原股價，持有期間除息會壓低報酬；asOf 當日沒有更新的 CSV 不列入。
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_breadth import (  # noqa: E402
    REPO_ROOT,
    as_of_date,
    load_market_inputs,
    present_matrix,
    trading_days,
    us_tickers_from_main,
    write_json_atomic,
)
from swing_breakouts import ATR_PERIOD, REVERSAL_ATR, SWING_KEY, SWING_METHOD, swing_high_signals, wilder_atr  # noqa: E402
from breakout_outcomes import GATE_ATR_PERIOD, OBSERVATION_DAYS, evaluate_attempt, summarize_attempts, success_params  # noqa: E402

BREAKOUTS_JSON_VERSION = 2
HIGH_LOOKBACKS = (20, 60, 120)
BOX_LOOKBACK = 20
# 箱型寬度門檻：(箱頂−箱底)/箱底。依 data/ 實際分布（2026-10，20 日高低區間的約下四分位數）挑選：
# 台股 25% 分位 ≈ 14.2%、美股 ≈ 9.5%。台股漲跌幅限制 10%、個股波動普遍較大，所以門檻較寬。
BOX_MAX_WIDTH = {"tw": 0.14, "us": 0.10}
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLE = 1.5
COOLDOWN_DAYS = 10
WINDOWS = (("1w", 5, "近1週"), ("1m", 21, "近1月"))
MATURE_DAYS = 3
MIN_SAMPLE = 20
JUMP_LIMIT = 0.30
# asOf 當日有有效 K 線的股票少於這個數量就視為輸入不足：不覆蓋舊檔並回傳錯誤
MIN_EVALUATED = 200
ANOMALY_LIST_LIMIT = 20
PRICE_COLUMNS = ("Open", "High", "Low", "Close")

DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_OUT = os.path.join(REPO_ROOT, "docs", "report", "breakouts.json")


def _strategy_specs():
    """策略清單（輸出順序即前端顯示順序）：key、中文名稱、種類、回看、是否放量確認、文字定義。"""
    specs = []
    bases = [("high", n, f"突破{n}日高", f"收盤 > 前 {n} 個有效交易日（不含當日）的最高價") for n in HIGH_LOOKBACKS]
    bases.append(
        (
            "box",
            BOX_LOOKBACK,
            f"突破{BOX_LOOKBACK}日箱型",
            f"前 {BOX_LOOKBACK} 個有效交易日（不含當日）高低區間 (最高−最低)/最低 ≤ 門檻，且收盤 > 箱頂",
        )
    )
    for volume in (False, True):
        for kind, lookback, label, definition in bases:
            key = f"{kind}{lookback}{'Vol' if volume else ''}"
            specs.append(
                {
                    "key": key,
                    "label": f"{label}＋放量" if volume else label,
                    "kind": kind,
                    "lookback": lookback,
                    "volume": volume,
                    "definition": definition
                    + (f"；且當日量 ≥ 前 {VOLUME_LOOKBACK} 日均量 × {VOLUME_MULTIPLE}" if volume else ""),
                }
            )
    specs.append({
        "key": "ema20", "label": "突破20EMA", "kind": "ema", "lookback": 20, "volume": False,
        "definition": "前日收盤 ≤ 前日 EMA20，且當日收盤 > 當日 EMA20；EMA 以最初 20 日收盤均值起算，之後以 2/21 平滑",
    })
    specs.append({
        "key": SWING_KEY, "label": "突破波段前高", "kind": "swing", "lookback": None, "volume": False,
        "definition": f"收盤自低點上漲至少 {REVERSAL_ATR:g} 倍 ATR({ATR_PERIOD}) 才形成上升段；"
                      f"其最高影線在後續收盤回落至少 {REVERSAL_ATR:g} 倍該高點當時 ATR 後確認，下一根才可使用；"
                      "橫盤小反彈不取代前高。盤中最高價嚴格超過已確認波段高點即觸發，"
                      "收盤未達關鍵點＋0.1ATR(14)則保留前高，後續可重試；合格收盤後同高點不重複，不要求完整123型態、放量或冷卻期",
    })
    return specs


STRATEGIES = _strategy_specs()


def clean_bars(bars, as_of, jump_limit=JUMP_LIMIT):
    """
    單檔K線清理（純函式）：截到 asOf、去掉無效日、High/Low 補成包含收盤、從最後一次異常跳空日起算。
    回傳 (clean, anomalies)。clean 為 None 代表沒有可用K線；anomalies 為 [(日期, 漲跌幅)]（全部異常日）。
    """
    bars = bars[bars.index <= as_of]
    valid = (bars["Volume"] > 0) & (bars["Close"] > 0) & (bars["High"] > 0) & (bars["Low"] > 0)
    bars = bars[valid]
    if bars.empty:
        return None, []
    close = bars["Close"].astype(float)
    clean = pd.DataFrame(
        {
            # No invented open: legacy callers without it cannot produce swing entries.
            "Open": bars["Open"].astype(float) if "Open" in bars else np.nan,
            "Close": close,
            "High": np.maximum(bars["High"].astype(float), close),
            "Low": np.minimum(bars["Low"].astype(float), close),
            "Volume": bars["Volume"].astype(float),
        },
        index=bars.index,
    )
    change = close.pct_change()
    # 容許浮點誤差：剛好 30%（例如 100 → 130）不算異常
    jumps = change.abs() > jump_limit + 1e-9
    anomalies = [(day, float(change.loc[day])) for day in change.index[jumps.values]]
    if anomalies:
        # 異常日（含）之後的K線才用：異常日本身的收盤是新尺度下的價格，可當回看起點
        clean = clean.loc[anomalies[-1][0]:]
    return clean, anomalies


def strategy_signals(clean, box_max_width, strategy_specs=None):
    """
    逐日訊號（純函式，只用當日與過去的K線）：{key: (fresh, level, known)}。
    fresh：新突破（含放量／箱型條件）；level：突破參考價（前高或箱頂）；known：回看與冷卻期資料都齊全。
    """
    specs = STRATEGIES if strategy_specs is None else strategy_specs
    close, high, low, volume = clean["Close"], clean["High"], clean["Low"], clean["Volume"]
    volume_avg = volume.shift(1).rolling(VOLUME_LOOKBACK, min_periods=VOLUME_LOOKBACK).mean()
    volume_ok = (volume >= VOLUME_MULTIPLE * volume_avg).fillna(False)

    base = {}
    for lookback in sorted({spec["lookback"] for spec in specs if spec["kind"] in ("high", "box")} | {BOX_LOOKBACK}):
        level = high.shift(1).rolling(lookback, min_periods=lookback).max()
        raw = (close > level).fillna(False)
        recent = raw.shift(1, fill_value=False).astype(int).rolling(COOLDOWN_DAYS, min_periods=COOLDOWN_DAYS).sum()
        # 冷卻期最早那天的前高也要算得出來，才能確認冷卻期內沒有突破
        known = level.shift(COOLDOWN_DAYS).notna()
        fresh = raw & (recent == 0) & known
        base[lookback] = (fresh, level, known)

    _, box_level, _ = base[BOX_LOOKBACK]
    box_bottom = low.shift(1).rolling(BOX_LOOKBACK, min_periods=BOX_LOOKBACK).min()
    tight = ((box_level - box_bottom) / box_bottom <= box_max_width + 1e-12).fillna(False)

    # Same SMA seed and alpha as Atlas's chart EMA; never seed with future observations.
    ema_level = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) >= 20:
        value = float(close.iloc[:20].mean())
        ema_level.iloc[19] = value
        for i in range(20, len(close)):
            value += (float(close.iloc[i]) - value) * (2 / 21)
            ema_level.iloc[i] = value
    cross = (close > ema_level) & (close.shift(1) <= ema_level.shift(1))
    recent_crosses = cross.shift(1, fill_value=False).astype(int).rolling(COOLDOWN_DAYS).sum()
    ema_known = ema_level.shift(COOLDOWN_DAYS + 1).notna()
    ema_fresh = cross & (recent_crosses == 0) & ema_known

    out = {}
    for spec in specs:
        if spec["kind"] == "swing":
            out[spec["key"]] = swing_high_signals(clean)
            continue
        fresh, level, known = (ema_fresh, ema_level, ema_known) if spec["kind"] == "ema" else base[spec["lookback"]]
        if spec["kind"] == "box":
            fresh = fresh & tight
        if spec["volume"]:
            fresh = fresh & volume_ok
        out[spec["key"]] = (fresh, level, known)
    return out


def _pct(part, whole, digits=1):
    return round(part / whole * 100, digits) if whole else None


def _ret_pct(value):
    return round(float(value) * 100, 2)


def summarize(events, eligible, base_by_day, mature_days=MATURE_DAYS):
    """
    events：[{date, ret, hold, fail}]；base_by_day：{日期: (上漲檔數, 檔數, 平均報酬)}。
    統計只用成熟樣本（hold ≥ mature_days）；基準以每筆成熟突破的進場日配對後平均。
    """
    matured = [event for event in events if event["hold"] >= mature_days]
    out = {"eligible": eligible, "n": len(events), "maturedN": len(matured),
           "wins": int(sum(event["ret"] > 0 for event in matured))}
    if not matured:
        out.update(
            winRate=None, avgReturn=None, medianReturn=None, failRate=None,
            avgHoldDays=None, baseRate=None, baseAvgReturn=None,
        )
        return out
    returns = np.array([event["ret"] for event in matured])
    base = [base_by_day[event["date"]] for event in matured]
    out.update(
        winRate=_pct(int((returns > 0).sum()), len(matured)),
        avgReturn=_ret_pct(returns.mean()),
        medianReturn=_ret_pct(np.median(returns)),
        failRate=_pct(sum(event["fail"] for event in matured), len(matured)),
        avgHoldDays=round(float(np.mean([event["hold"] for event in matured])), 1),
        baseRate=round(float(np.mean([up / total for up, total, _ in base])) * 100, 1),
        baseAvgReturn=_ret_pct(np.mean([mean for _, _, mean in base])),
    )
    return out


def build_market(frames, universe_label, market_key, as_of=None, *, strategy_specs=None, window_specs=None):
    """
    單一市場的突破統計（純計算，不讀檔）。
    frames：{symbol: DataFrame（index 為日期，欄位 High、Low、Close、Volume）}
    as_of：預設為各檔最後日期的眾數；指定時只用該日（含）以前的資料。
    """
    specs = STRATEGIES if strategy_specs is None else strategy_specs
    windows_to_build = WINDOWS if window_specs is None else window_specs
    universe_count = len(frames)
    # 兩個市場週六日都不交易：週末K線一律視為無效日（2026-09-20 週日曾有 591 檔台股出現
    # 高低價＝漲跌停價、收盤沿用前一日的假K線，會墊高前高並讓箱型失真）
    weekend_dates = [bars.index[bars.index.day_of_week >= 5] for bars in frames.values()]
    frames = {symbol: bars[bars.index.day_of_week < 5] for symbol, bars in frames.items()}
    frames = {symbol: bars for symbol, bars in frames.items() if not bars.empty}
    if not frames:
        return None
    as_of = as_of_date(frames) if as_of is None else pd.Timestamp(as_of)
    weekend_bars = sum(int((dates <= as_of).sum()) for dates in weekend_dates)
    calendar = list(trading_days(present_matrix(frames), as_of))
    if not calendar or calendar[-1] != as_of:
        calendar.append(as_of)
    position = {day: index for index, day in enumerate(calendar)}
    last_position = len(calendar) - 1
    box_max_width = BOX_MAX_WIDTH[market_key]

    stocks = []
    anomaly_rows = []
    for symbol, bars in frames.items():
        clean, anomalies = clean_bars(bars, as_of)
        if clean is None or clean.index[-1] != as_of:
            continue
        anomaly_rows.extend((day, symbol, change) for day, change in anomalies)
        signals = strategy_signals(clean, box_max_width, specs)
        stocks.append(
            {
                "dates": clean.index,
                # 每根K在交易日曆上的位置；不在日曆上的日期（只有少數 CSV 有資料的日子）為 -1
                "pos": np.array([position.get(day, -1) for day in clean.index]),
                "close": clean["Close"].to_numpy(),
                "open": clean["Open"].to_numpy(),
                "bars": clean,
                "atr14": wilder_atr(clean, GATE_ATR_PERIOD),
                "signals": {
                    key: (fresh.to_numpy(), level.to_numpy(), known.to_numpy())
                    for key, (fresh, level, known) in signals.items()
                },
            }
        )

    windows = {}
    for window_key, days, _ in windows_to_build:
        window_days = calendar[-days:]
        start = window_days[0]
        first_position = last_position - len(window_days) + 1
        # 基準：每個窗口日，所有可評估股票從該日收盤持有到 asOf
        base_returns = {}
        masks = []
        for stock in stocks:
            mask = stock["pos"] >= first_position
            masks.append(mask)
            last_close = stock["close"][-1]
            for index in np.flatnonzero(mask):
                base_returns.setdefault(stock["pos"][index], []).append(last_close / stock["close"][index] - 1)
        base_by_day = {
            calendar[pos]: (int((np.array(values) > 0).sum()), len(values), float(np.mean(values)))
            for pos, values in base_returns.items()
        }

        strategies = {}
        for spec in specs:
            events = []
            attempts = []
            eligible = 0
            for stock, mask in zip(stocks, masks):
                fresh, level, known = stock["signals"][spec["key"]]
                in_window = np.flatnonzero(mask)
                # 窗口第一天就要有完整回看＋冷卻期，才能確定窗口內第一次突破真的是「第一次」
                swing = spec["kind"] == "swing"
                if not len(in_window) or not (known[in_window].any() if swing else known[in_window[0]]):
                    continue
                eligible += 1
                hits = np.flatnonzero(mask & fresh)
                if not len(hits):
                    continue
                last_close = stock["close"][-1]
                # All distinct swing pivots count, including multiple waves in one window.
                for index in hits if swing else hits[:1]:
                    if spec['key'] in (SWING_KEY, 'box20', 'ema20'):
                        attempts.append(evaluate_attempt(stock['bars'], index, float(level[index]), stock['atr14'], calendar, position))
                    # Stop-entry approximation: a gap above the pivot fills at the open.
                    # Daily bars cannot establish actual tick fills, fees or slippage.
                    entry = max(stock["open"][index], level[index]) if swing else stock["close"][index]
                    events.append(
                        {
                            "date": calendar[stock["pos"][index]],
                            "ret": last_close / entry - 1,
                            "hold": int(last_position - stock["pos"][index]),
                            "fail": bool(last_close < level[index]),
                        }
                    )
            strategies[spec["key"]] = summarize(events, eligible, base_by_day)
            if spec['key'] in (SWING_KEY, 'box20', 'ema20'):
                strategies[spec['key']]['comparison'] = summarize_attempts(attempts)

        matured_days = [day for day in window_days if last_position - position[day] >= MATURE_DAYS and day in base_by_day]
        windows[window_key] = {
            "days": days,
            "start": start.strftime("%Y-%m-%d"),
            "maturedThrough": calendar[-1 - MATURE_DAYS].strftime("%Y-%m-%d") if len(calendar) > MATURE_DAYS else None,
            "observedThrough": calendar[-1 - OBSERVATION_DAYS].strftime("%Y-%m-%d") if len(calendar) > OBSERVATION_DAYS else None,
            "market": {
                "entryDays": len(matured_days),
                "pairs": sum(base_by_day[day][1] for day in matured_days),
                "upRate": round(float(np.mean([base_by_day[d][0] / base_by_day[d][1] for d in matured_days])) * 100, 1)
                if matured_days
                else None,
                "avgReturn": _ret_pct(np.mean([base_by_day[d][2] for d in matured_days])) if matured_days else None,
            },
            "strategies": strategies,
        }

    anomaly_rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return {
        "asOf": as_of.strftime("%Y-%m-%d"),
        "universe": universe_label,
        "universeCount": universe_count,
        "evaluated": len(stocks),
        # asOf 當日沒有有效K線（CSV 停更、當日無成交、只有週末K線）的檔數
        "stale": universe_count - len(stocks),
        "weekendBars": weekend_bars,
        "boxMaxWidthPct": round(box_max_width * 100, 1),
        "anomalies": {
            "stocks": len({symbol for _, symbol, _ in anomaly_rows}),
            "days": len(anomaly_rows),
            "recent": [
                {"symbol": symbol, "date": day.strftime("%Y-%m-%d"), "changePct": _ret_pct(change)}
                for day, symbol, change in anomaly_rows[:ANOMALY_LIST_LIMIT]
            ],
        },
        "windows": windows,
    }


def params_meta():
    """口徑參數（前端的口徑說明直接讀這裡）。"""
    return {
        **success_params(),
        "windows": [{"key": key, "days": days, "label": label} for key, days, label in WINDOWS],
        "matureDays": MATURE_DAYS,
        "minSample": MIN_SAMPLE,
        "cooldownDays": COOLDOWN_DAYS,
        "jumpLimitPct": round(JUMP_LIMIT * 100, 1),
        "volumeLookback": VOLUME_LOOKBACK,
        "volumeMultiple": VOLUME_MULTIPLE,
        "boxLookback": BOX_LOOKBACK,
        "swingMethod": SWING_METHOD,
        "swingAtrPeriod": ATR_PERIOD,
        "swingReversalAtr": REVERSAL_ATR,
        "minEvaluated": MIN_EVALUATED,
        "entry": "波段前高突破以 max(開盤價, 波段高點) 作理論停買進場價；其餘方法以訊號日收盤進場",
        "mark": "asOf 收盤標記",
        "strategies": [
            {key: spec[key] for key in ("key", "label", "kind", "lookback", "volume", "definition")} for spec in STRATEGIES
        ],
    }


def build_breakouts_payload(inputs, generated_at):
    """inputs：與 build_breadth.load_market_inputs 相同格式；沒有股票的市場略過。"""
    markets = {}
    for key, spec in inputs.items():
        market = build_market(spec["frames"], spec["universe"], key) if spec["frames"] else None
        if market is not None:
            markets[key] = market
    return {
        "version": BREAKOUTS_JSON_VERSION,
        "generatedAt": generated_at,
        "params": params_meta(),
        "markets": markets,
    }


def insufficient_markets(payload, expected=("tw", "us")):
    """回傳輸入不足的市場說明（缺市場或 asOf 有效股票 < MIN_EVALUATED）；空 list 代表可以寫檔。"""
    problems = []
    for key in expected:
        market = payload["markets"].get(key)
        if market is None:
            problems.append(f"{key.upper()} 沒有任何日 K")
        elif market["evaluated"] < MIN_EVALUATED:
            problems.append(
                f"{key.upper()} asOf {market['asOf']} 只有 {market['evaluated']} 檔有有效 K 線"
                f"（universe {market['universeCount']} 檔，門檻 {MIN_EVALUATED}）"
            )
    return problems


def main(argv=None, us_tickers_fn=None):
    parser = argparse.ArgumentParser(description="產生 docs/report/breakouts.json（突破買進法勝率）")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="日 K CSV 目錄（預設 data/）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="輸出路徑（預設 docs/report/breakouts.json）")
    args = parser.parse_args(argv)

    payload = build_breakouts_payload(
        load_market_inputs(args.data_dir, us_tickers_fn, PRICE_COLUMNS),
        datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    problems = insufficient_markets(payload)
    if problems:
        print("❌ 突破勝率輸入不足，breakouts.json 不更新：" + "；".join(problems))
        return 1

    write_json_atomic(args.out, payload)
    for key, market in payload["markets"].items():
        print(
            f"✅ 突破勝率 {key.upper()}：asOf {market['asOf']}，可評估 {market['evaluated']} / {market['universeCount']} 檔，"
            f"異常跳空 {market['anomalies']['stocks']} 檔"
        )
        for window_key, window in market["windows"].items():
            base = window["market"]
            print(f"   [{window_key}] 自 {window['start']}，全市場上漲 {base['upRate']}%（{base['entryDays']} 個成熟進場日）")
            for spec in STRATEGIES:
                stats = window["strategies"][spec["key"]]
                print(
                    f"     {spec['label']}: n={stats['n']} 成熟={stats['maturedN']} 勝率={stats['winRate']}% "
                    f"基準={stats['baseRate']}% 均報={stats['avgReturn']}% 失敗={stats['failRate']}%"
                )
    print(f"輸出：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(us_tickers_fn=us_tickers_from_main))
