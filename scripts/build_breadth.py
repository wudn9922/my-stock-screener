#!/usr/bin/env python3
"""
F5 市場寬度：站上 MA20 / MA60 的股票比例 → docs/report/breadth.json。

只讀 data/ 內的日 K CSV（Date,Open,High,Low,Close,Volume），不需要行情網路。
美股 universe 的 S&P 500 名單會呼叫 main.get_us_tickers（只在直接執行時延遲匯入）。

計算規則：
- 台股：檔名 4 碼、非 0 開頭的 .TW（上市）與 .TWO（上櫃）；0 開頭的 ETF、^ 指數不納入。
- 美股：S&P 500 與 CSV 交集；名單取得失敗時退回 data/ 內所有非台股、非指數的 CSV。
- 某日「有效」：該日有 K 線且成交量 > 0；最後一根早於 asOf 的股票不列入當日 count。
- 站上 = 收盤 > SMA_n（嚴格大於）；只有至少 n 根K 的股票才進入該均線的分母。
- asOf = 排除市場共同零量占位日後，眾數日期以前最近有足夠有效資料的交易日。
- 歷史：asOf 以前、有資料檔數不少於「近 20 個交易日有資料檔數的下四分位數」一半的日期，取最後 120 個，逐日重算。
  （不用 universe 總檔數當分母：部分 CSV 長期沒更新時，總檔數會讓覆蓋率門檻失真。）
- 注意：universe 是「現在」的名單，回推的歷史含存活者偏差。
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

BREADTH_JSON_VERSION = 1
MA_PERIODS = (20, 60)
SEGMENT_NAMES = ("twse", "tpex")
HISTORY_DAYS = 120
# 歷史日期的覆蓋率門檻：該日有資料檔數 / 近 REF_WINDOW 日（含 asOf）有資料檔數的下四分位數
HISTORY_MIN_COVERAGE = 0.5
HISTORY_REF_WINDOW = 20
# 用下四分位數而不是中位數：停更的 CSV 讓近期檔數逐日下滑時，asOf 當日仍要能達標；單日缺資料的異常日仍遠低於它
HISTORY_REF_QUANTILE = 0.25
# S&P 500 名單少於這個數量視為抓取失敗（main.get_us_tickers 失敗時會回傳 3 檔預設名單）
US_UNIVERSE_MIN = 400

TW_SYMBOL_PATTERN = re.compile(r"^[1-9]\d{3}\.(TW|TWO)\.csv$")
TW_ANY_PATTERN = re.compile(r"\.TWO?\.csv$")
TW_UNIVERSE_LABEL = "台股上市櫃普通股（4碼、非0開頭）"
US_UNIVERSE_LABEL = "S&P 500 成分股"
US_FALLBACK_LABEL = "S&P 500＋自選美股"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from kline_integrity import usable_frame
from scripts.market_sessions import market_prices
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_OUT = os.path.join(REPO_ROOT, "docs", "report", "breadth.json")


def load_daily_bars(csv_path, price_columns=("Close",)):
    """
    讀取日 K CSV，回傳以日期為 index、欄位為 price_columns＋Volume 的 DataFrame；格式不符回傳 None。
    price_columns 任一欄缺值的列會被丟棄（市場寬度只需要 Close；突破統計另要 High/Low）。
    """
    try:
        raw = pd.read_csv(csv_path)
        if "Date" in raw:
            raw = usable_frame(raw.set_index("Date"), os.path.basename(csv_path).removesuffix(".csv")).reset_index()
    except Exception:
        return None
    if not {"Date", "Volume", *price_columns}.issubset(raw.columns):
        return None

    bars = pd.DataFrame(
        {
            "Date": pd.to_datetime(raw["Date"], errors="coerce"),
            **{column: pd.to_numeric(raw[column], errors="coerce") for column in price_columns},
            "Volume": pd.to_numeric(raw["Volume"], errors="coerce").fillna(0),
        }
    )
    bars = bars.dropna(subset=["Date", *price_columns])
    bars = bars.drop_duplicates("Date", keep="last").sort_values("Date").set_index("Date")
    return bars if len(bars) else None


def select_tw_symbols(filenames):
    """台股 universe：4 碼、非 0 開頭的上市（.TW）與上櫃（.TWO）檔名（去掉 .csv）。"""
    return sorted(name[: -len(".csv")] for name in filenames if TW_SYMBOL_PATTERN.match(name))


def select_us_symbols(filenames, sp500=None):
    """美股 universe。sp500 為 None 時退回所有非台股、非指數的 CSV，並回傳對應的 universe 文字。"""
    candidates = sorted(
        name[: -len(".csv")]
        for name in filenames
        if name.endswith(".csv") and not name.startswith("^") and not TW_ANY_PATTERN.search(name)
    )
    if sp500 is None:
        return candidates, US_FALLBACK_LABEL
    wanted = set(sp500)
    return [symbol for symbol in candidates if symbol in wanted], US_UNIVERSE_LABEL


def _pct(above, total):
    above, total = int(above), int(total)
    return round(above / total * 100, 1) if total else None


def present_matrix(frames):
    """日期 × 股票的「有效日」旗標（該日有 K 且成交量 > 0），對齊到全部日期。"""
    closes = pd.DataFrame({symbol: frame["Close"] for symbol, frame in frames.items()})
    volumes = pd.DataFrame({symbol: frame["Volume"] for symbol, frame in frames.items()})
    return closes.notna() & (volumes > 0)


def trading_days(present, as_of):
    """
    asOf 以前（含）可視為市場交易日的日期：有資料檔數 > 0，且不少於「近 HISTORY_REF_WINDOW 日有資料檔數的
    下四分位數」的 HISTORY_MIN_COVERAGE 倍。只有少數 CSV 有資料的日期（假日誤植、單檔異常）會被排除。
    """
    counts = present.sum(axis=1)
    counts = counts[counts.index <= as_of]
    # 以近期有資料檔數為基準，而不是 universe 總檔數：停更的 CSV 會讓總檔數遠大於實際可用檔數
    reference = float(counts.iloc[-HISTORY_REF_WINDOW:].quantile(HISTORY_REF_QUANTILE))
    keep = (counts > 0) & (counts >= HISTORY_MIN_COVERAGE * reference)
    return counts.index[keep.values]


def _daily_flags(frames):
    """各檔逐日旗標，對齊到全部日期：present（有K且量>0）、valid_n（已有 n 根K）、above_n（站上 SMA_n）。"""
    closes = pd.DataFrame({symbol: frame["Close"] for symbol, frame in frames.items()})
    present = present_matrix(frames)
    flags = {"present": present, "valid": {}, "above": {}}
    for period in MA_PERIODS:
        # 均線要在每檔自己的K線上滾動計算，缺日不會打斷視窗
        sma = pd.DataFrame({symbol: frame["Close"].rolling(period, min_periods=period).mean() for symbol, frame in frames.items()})
        flags["valid"][period] = sma.notna() & present
        flags["above"][period] = flags["valid"][period] & (closes > sma)
    return flags


def _tally(flags, symbols):
    """指定股票子集在每個日期的計數：count、maNAbove、maNTotal。"""
    out = pd.DataFrame({"count": flags["present"][symbols].sum(axis=1)})
    for period in MA_PERIODS:
        out[f"ma{period}Above"] = flags["above"][period][symbols].sum(axis=1)
        out[f"ma{period}Total"] = flags["valid"][period][symbols].sum(axis=1)
    return out


def _segment(row):
    segment = {"count": int(row["count"])}
    for period in MA_PERIODS:
        above = int(row[f"ma{period}Above"])
        total = int(row[f"ma{period}Total"])
        segment[f"ma{period}"] = {"above": above, "total": total, "pct": _pct(above, total)}
    return segment


def as_of_date(frames):
    """各檔最後日期的眾數；次數相同時取較晚的日期。"""
    counts = Counter(frame.index.max() for frame in frames.values())
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def build_market(frames, universe_label, segment_of=None):
    """
    單一市場的寬度（純計算，不讀檔）。
    frames: {symbol: DataFrame（index 為日期，欄位 Close、Volume）}
    segment_of: {symbol: "twse" | "tpex"}，台股才需要；沒有時只輸出 all。
    """
    frames, _ = market_prices(frames)
    if not frames:
        return None
    as_of = as_of_date(frames)
    flags = _daily_flags(frames)
    symbols = list(frames)
    tally = _tally(flags, symbols)

    dates = trading_days(flags["present"], as_of)
    if dates.empty:
        return None
    # The modal last row can be an unpublished/zero-volume placeholder. Use the
    # latest evaluable session rather than publishing an empty market snapshot.
    as_of = dates[-1]
    segments = {"all": _segment(tally.loc[as_of])}
    if segment_of is not None:
        for name in SEGMENT_NAMES:
            members = [symbol for symbol in symbols if segment_of.get(symbol) == name]
            segments[name] = _segment(_tally(flags, members).loc[as_of])

    dates = dates[-HISTORY_DAYS:]
    history_tally = tally.loc[dates]
    history = {
        "dates": [day.strftime("%Y-%m-%d") for day in dates],
        "ma20Pct": [_pct(a, t) for a, t in zip(history_tally["ma20Above"], history_tally["ma20Total"])],
        "ma60Pct": [_pct(a, t) for a, t in zip(history_tally["ma60Above"], history_tally["ma60Total"])],
    }
    return {
        "asOf": as_of.strftime("%Y-%m-%d"),
        "universe": universe_label,
        "segments": segments,
        "history": history,
    }


def build_breadth_payload(inputs, generated_at):
    """
    inputs: {"tw": {"frames": {...}, "universe": str, "segment_of": {...} | None}, "us": {...}}
    沒有任何股票的市場會被略過；全部略過時 markets 為空。
    """
    markets = {}
    for key, spec in inputs.items():
        if not spec["frames"]:
            continue
        market = build_market(spec["frames"], spec["universe"], spec.get("segment_of"))
        if market is not None:
            markets[key] = market
    return {"version": BREADTH_JSON_VERSION, "generatedAt": generated_at, "markets": markets}


def write_json_atomic(path, payload):
    """寫到同目錄暫存檔後 os.replace，避免網站讀到寫到一半的檔案。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    file_descriptor, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_sp500(us_tickers_fn):
    """回傳 S&P 500 代號集合；沒有提供函式、拋出例外或名單太短時回傳 None（改用退回規則）。"""
    if us_tickers_fn is None:
        return None
    try:
        tickers = list(us_tickers_fn())
    except Exception as exc:
        print(f"⚠️ S&P 500 名單取得失敗，美股改用 data/ 內所有 CSV：{exc}")
        return None
    if len(tickers) < US_UNIVERSE_MIN:
        print(f"⚠️ S&P 500 名單只有 {len(tickers)} 檔，視為抓取失敗，美股改用 data/ 內所有 CSV")
        return None
    return set(tickers)


def load_frames(data_dir, symbols, price_columns=("Close",)):
    frames = {}
    for symbol in symbols:
        bars = load_daily_bars(os.path.join(data_dir, f"{symbol}.csv"), price_columns)
        if bars is not None:
            frames[symbol] = bars
    return frames


def load_market_inputs(data_dir, us_tickers_fn=None, price_columns=("Close",)):
    """
    讀 data/ 的台股與美股 universe：{"tw": {"frames", "universe", "segment_of"}, "us": {...}}。
    市場寬度與突破勝率（build_breakouts.py）共用同一套名單規則。
    """
    filenames = sorted(os.listdir(data_dir)) if os.path.isdir(data_dir) else []
    tw_symbols = select_tw_symbols(filenames)
    us_symbols, us_label = select_us_symbols(filenames, load_sp500(us_tickers_fn))

    tw_frames = load_frames(data_dir, tw_symbols, price_columns)
    segment_of = {symbol: ("tpex" if symbol.endswith(".TWO") else "twse") for symbol in tw_frames}
    return {
        "tw": {"frames": tw_frames, "universe": TW_UNIVERSE_LABEL, "segment_of": segment_of},
        "us": {"frames": load_frames(data_dir, us_symbols, price_columns), "universe": us_label, "segment_of": None},
    }


def main(argv=None, us_tickers_fn=None):
    parser = argparse.ArgumentParser(description="產生 docs/report/breadth.json（市場寬度）")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="日 K CSV 目錄（預設 data/）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="輸出路徑（預設 docs/report/breadth.json）")
    args = parser.parse_args(argv)

    payload = build_breadth_payload(
        load_market_inputs(args.data_dir, us_tickers_fn),
        datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    if not payload["markets"]:
        print("⚠️ data/ 沒有可用的日 K，breadth.json 不更新")
        return 1

    # One source can be unavailable while the other market refreshes. Do not
    # erase its last evaluable snapshot as a side effect of that partial run.
    try:
        with open(args.out, encoding="utf-8") as file:
            previous = json.load(file)
    except (OSError, ValueError):
        previous = {}
    old_markets = previous.get("markets", {}) if isinstance(previous, dict) else {}
    for key in ("tw", "us"):
        old = old_markets.get(key) if isinstance(old_markets, dict) else None
        count = ((old or {}).get("segments", {}).get("all", {}).get("count")) if isinstance(old, dict) else None
        if key not in payload["markets"] and isinstance(count, (int, float)) and count > 0 and old.get("asOf"):
            payload["markets"][key] = old
            print(f"ℹ️ {key.upper()} 本次無有效資料，沿用資料日 {old['asOf']}")

    write_json_atomic(args.out, payload)
    for key, market in payload["markets"].items():
        all_segment = market["segments"]["all"]
        print(
            f"✅ 市場寬度 {key.upper()}：asOf {market['asOf']}，"
            f"{all_segment['count']} 檔，MA20 {all_segment['ma20']['pct']}%，"
            f"MA60 {all_segment['ma60']['pct']}%，歷史 {len(market['history']['dates'])} 日"
        )
    print(f"輸出：{args.out}")
    return 0


def us_tickers_from_main():
    """延遲匯入 main.get_us_tickers：main.py 頂層會 import yfinance，只在實際執行時才載入。"""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from main import get_us_tickers

    return get_us_tickers()


if __name__ == "__main__":
    sys.exit(main(us_tickers_fn=us_tickers_from_main))
