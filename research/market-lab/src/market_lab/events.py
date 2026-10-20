from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def load_event_spec(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tag(value: float) -> str:
    return f"{int(round(value * 100)):02d}"


def expand_event_catalog(spec: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for family in spec["families"]:
        kind = family["type"]
        if kind == "daily_return":
            for threshold in family["thresholds"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"daily_return_{direction}_{_tag(threshold)}", "family": "price", "type": kind, "direction": direction, "threshold": threshold, "condition": f"單日收盤報酬 {direction} {threshold:.0%}"})
        elif kind == "overnight_gap":
            for threshold in family["thresholds"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"overnight_gap_{direction}_{_tag(threshold)}", "family": "price", "type": kind, "direction": direction, "threshold": threshold, "condition": f"開盤相對前收 {direction} {threshold:.0%}"})
        elif kind == "streak":
            for length in family["lengths"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"streak_{direction}_{length}", "family": "price", "type": kind, "direction": direction, "length": length, "condition": f"連續 {length} 個交易日收盤{('上漲' if direction == 'up' else '下跌')}"})
        elif kind == "new_extreme":
            for window in family["windows"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"new_{direction}_{window}", "family": "price", "type": kind, "direction": direction, "window": window, "condition": f"收盤創前 {window} 日新{('高' if direction == 'high' else '低')}"})
        elif kind == "volume_spike":
            for multiple in family["multiples"]:
                catalog.append({"event_id": f"volume_spike_{int(multiple)}", "family": "volume_volatility", "type": kind, "multiple": multiple, "condition": f"成交量至少為前 20 日中位數 {multiple:g} 倍"})
        elif kind == "high_volume_small_move":
            for limit in family["max_abs_returns"]:
                catalog.append({"event_id": f"high_volume_small_move_{_tag(limit)}", "family": "volume_volatility", "type": kind, "multiple": family["volume_multiple"], "threshold": limit, "condition": f"量至少 2 倍且單日絕對報酬不超過 {limit:.1%}"})
        elif kind == "price_volume_divergence":
            catalog.extend([
                {"event_id": "up_day_low_volume", "family": "volume_volatility", "type": kind, "direction": "up_low_volume", "condition": "單日漲幅至少 2% 且量低於前 20 日中位數一半"},
                {"event_id": "down_day_high_volume", "family": "volume_volatility", "type": kind, "direction": "down_high_volume", "condition": "單日跌幅至少 2% 且量至少為前 20 日中位數 2 倍"},
            ])
        elif kind == "volatility_expansion":
            for metric in family["metrics"]:
                for ratio in family["ratios"]:
                    catalog.append({"event_id": f"{metric}_{_tag(ratio)}", "family": "volume_volatility", "type": kind, "metric": metric, "ratio": ratio, "condition": f"{metric} 達歷史基準 {ratio:g} 倍"})
        elif kind == "compression_expansion":
            for trigger in ("return", "range"):
                catalog.append({"event_id": f"compression_expansion_{trigger}", "family": "volume_volatility", "type": kind, "trigger": trigger, "window": family["window"], "reference_window": family["reference_window"], "quantile": family["compression_quantile"], "threshold": family[f"{trigger}_trigger"], "condition": f"前 {family['window']} 日波動位於 {family['reference_window']} 日低波動區，當日 {trigger} 擴張"})
        elif kind == "sma_cross":
            for fast, slow in family["pairs"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"sma_cross_{fast}_{slow}_{direction}", "family": "trend", "type": kind, "fast": fast, "slow": slow, "direction": direction, "condition": f"SMA {fast}/{slow} 向{('上' if direction == 'up' else '下')}交叉"})
        elif kind == "ma_deviation":
            for window in family["windows"]:
                for threshold in family["thresholds"]:
                    for direction in family["directions"]:
                        catalog.append({"event_id": f"ma_deviation_{window}_{direction}_{_tag(threshold)}", "family": "trend", "type": kind, "window": window, "threshold": threshold, "direction": direction, "condition": f"收盤價相對 SMA {window} 偏離 {direction} {threshold:.0%}"})
        elif kind == "failed_level":
            for window in family["windows"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"failed_{direction}_{window}", "family": "trend", "type": kind, "window": window, "direction": direction, "condition": f"前一日收盤突破前 {window} 日{('高點' if direction == 'breakout' else '低點')}，今日收回線內"})
        elif kind == "trend_flip":
            catalog.extend([
                {"event_id": "trend_flip_down_up", "family": "trend", "type": kind, "direction": "down_up", "prior_window": family["prior_return_window"], "prior_threshold": -family["prior_threshold"], "daily_trigger": family["daily_trigger"], "condition": "前 5 日跌幅至少 10%，當日反彈至少 3%"},
                {"event_id": "trend_flip_up_down", "family": "trend", "type": kind, "direction": "up_down", "prior_window": family["prior_return_window"], "prior_threshold": family["prior_threshold"], "daily_trigger": -family["daily_trigger"], "condition": "前 5 日漲幅至少 10%，當日回落至少 3%"},
            ])
        elif kind == "otc_index_return":
            for threshold in family["thresholds"]:
                for direction in family["directions"]:
                    catalog.append({"event_id": f"otc_index_return_{direction}_{_tag(threshold)}", "family": "market", "type": kind, "symbol": family["symbol"], "direction": direction, "threshold": threshold, "condition": f"櫃買指數單日報酬 {direction} {threshold:.0%}"})
        elif kind == "market_breadth":
            for threshold in family["thresholds"]:
                for direction in family["directions"]:
                    side = "上漲" if direction == "advance" else "下跌"
                    catalog.append({"event_id": f"breadth_{direction}_{_tag(threshold)}", "family": "market", "type": kind, "direction": direction, "threshold": threshold, "min_symbols": int(family["min_symbols"]), "condition": f"當期 repo 股票橫斷面至少 {threshold:.0%} 收盤{side}；當日有效股票至少 {family['min_symbols']} 檔"})

    for item in catalog:
        item["observable_at"] = "close_t"
        item["entry_assumption"] = "next_available_symbol_bar_open"
        item["holding_sessions"] = ",".join(str(value) for value in spec["holding_sessions"])
        item["parameters_json"] = json.dumps({k: v for k, v in item.items() if k not in {"event_id", "family", "type", "condition", "observable_at", "entry_assumption", "holding_sessions", "parameters_json"}}, sort_keys=True)
    return catalog


def _features(frame: pd.DataFrame) -> dict[str, Any]:
    close = frame["Close"].astype(float)
    open_price = frame["Open"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].astype(float)
    previous = close.shift(1)
    ret = close.pct_change()
    gap = open_price.div(previous).sub(1)
    intraday_return = close.div(open_price).sub(1)
    suspected_action_or_price_error = (
        ret.abs().gt(0.50) | gap.abs().gt(0.50) | intraday_return.abs().gt(0.50)
    ).fillna(False)
    volume_baseline = volume.shift(1).rolling(20, min_periods=10).median()
    volume_ratio = volume.div(volume_baseline.replace(0, np.nan))
    true_range = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    prior_atr20 = true_range.shift(1).rolling(20, min_periods=15).mean()
    rv5 = ret.rolling(5, min_periods=5).std()
    prior_rv60_median = rv5.shift(1).rolling(60, min_periods=30).median()
    rv20 = ret.rolling(20, min_periods=20).std()
    prior_rv252_q20 = rv20.shift(1).rolling(252, min_periods=80).quantile(0.20)
    ma = {n: close.rolling(n, min_periods=n).mean() for n in (5, 20, 50, 60, 120, 200)}
    prior_high = {n: close.shift(1).rolling(n, min_periods=n).max() for n in (20, 60, 120, 252)}
    prior_low = {n: close.shift(1).rolling(n, min_periods=n).min() for n in (20, 60, 120, 252)}
    return {
        "close": close, "open": open_price, "high": high, "low": low, "volume": volume,
        "return": ret, "gap": gap, "volume_ratio": volume_ratio, "true_range": true_range,
        "suspected_action_or_price_error": suspected_action_or_price_error,
        "atr_ratio": true_range.div(prior_atr20.replace(0, np.nan)),
        "rv_ratio": rv5.div(prior_rv60_median.replace(0, np.nan)),
        "range_pct": (high - low).div(previous.replace(0, np.nan)),
        "rv20": rv20, "prior_rv252_q20": prior_rv252_q20,
        "ma": ma, "prior_high": prior_high, "prior_low": prior_low,
    }


def detect_event_signals(frame: pd.DataFrame, symbol: str, catalog: list[dict[str, Any]]) -> dict[str, pd.Series]:
    f = _features(frame)
    close, ret = f["close"], f["return"]
    out: dict[str, pd.Series] = {}
    for event in catalog:
        kind, eid = event["type"], event["event_id"]
        if kind == "daily_return":
            signal = ret >= event["threshold"] if event["direction"] == "up" else ret <= -event["threshold"]
        elif kind == "overnight_gap":
            signal = f["gap"] >= event["threshold"] if event["direction"] == "up" else f["gap"] <= -event["threshold"]
        elif kind == "streak":
            positive = ret.gt(0) if event["direction"] == "up" else ret.lt(0)
            signal = positive.rolling(event["length"], min_periods=event["length"]).sum().eq(event["length"])
        elif kind == "new_extreme":
            ref = f["prior_high"][event["window"]] if event["direction"] == "high" else f["prior_low"][event["window"]]
            signal = close.gt(ref) if event["direction"] == "high" else close.lt(ref)
        elif kind == "volume_spike":
            signal = f["volume_ratio"] >= event["multiple"]
        elif kind == "high_volume_small_move":
            signal = (f["volume_ratio"] >= event["multiple"]) & (ret.abs() <= event["threshold"])
        elif kind == "price_volume_divergence":
            if event["direction"] == "up_low_volume":
                signal = (ret >= 0.02) & (f["volume_ratio"] <= 0.5)
            else:
                signal = (ret <= -0.02) & (f["volume_ratio"] >= 2.0)
        elif kind == "volatility_expansion":
            ratio = f["rv_ratio"] if event["metric"] == "rv5_to_prior60_median" else f["atr_ratio"]
            signal = ratio >= event["ratio"]
        elif kind == "compression_expansion":
            compressed = f["rv20"].shift(1) <= f["prior_rv252_q20"]
            trigger = ret.abs() >= event["threshold"] if event["trigger"] == "return" else f["range_pct"] >= event["threshold"]
            signal = compressed & trigger
        elif kind == "sma_cross":
            fast, slow = f["ma"][event["fast"]], f["ma"][event["slow"]]
            if event["direction"] == "up":
                signal = fast.gt(slow) & fast.shift(1).le(slow.shift(1))
            else:
                signal = fast.lt(slow) & fast.shift(1).ge(slow.shift(1))
        elif kind == "ma_deviation":
            deviation = close.div(f["ma"][event["window"]]).sub(1)
            signal = deviation >= event["threshold"] if event["direction"] == "up" else deviation <= -event["threshold"]
        elif kind == "failed_level":
            window = event["window"]
            if event["direction"] == "breakout":
                level = close.shift(2).rolling(window, min_periods=window).max()
                signal = close.shift(1).gt(level) & close.le(level)
            else:
                level = close.shift(2).rolling(window, min_periods=window).min()
                signal = close.shift(1).lt(level) & close.ge(level)
        elif kind == "trend_flip":
            prior_ret = close.shift(1).div(close.shift(1 + event["prior_window"])).sub(1)
            if event["direction"] == "down_up":
                signal = (prior_ret <= event["prior_threshold"]) & (ret >= event["daily_trigger"])
            else:
                signal = (prior_ret >= event["prior_threshold"]) & (ret <= event["daily_trigger"])
        elif kind == "otc_index_return":
            if symbol != event["symbol"]:
                signal = pd.Series(False, index=frame.index)
            else:
                signal = ret >= event["threshold"] if event["direction"] == "up" else ret <= -event["threshold"]
        elif kind == "market_breadth":
            # Filled from a same-date cross-sectional snapshot in
            # build_event_results; per-symbol features cannot infer breadth.
            signal = pd.Series(False, index=frame.index)
        else:
            signal = pd.Series(False, index=frame.index)
        # Large single-bar discontinuities can be real, but without corporate-
        # action data they are indistinguishable from split/data errors. Avoid
        # using those bars as events and flag their forward windows separately.
        signal = signal & ~f["suspected_action_or_price_error"]
        out[eid] = pd.Series(signal, index=frame.index).fillna(False).astype(bool)
    return out


def composite_strategy_signal(frame: pd.DataFrame, composite: str) -> pd.Series:
    features = _features(frame)
    if composite == "volume_ge_2_and_return_ge_2pct":
        signal = (features["volume_ratio"] >= 2.0) & (features["return"] >= 0.02)
    else:
        signal = pd.Series(False, index=frame.index)
    signal = signal & ~features["suspected_action_or_price_error"]
    return pd.Series(signal, index=frame.index).fillna(False).astype(bool)


def build_event_results(
    frames: dict[str, pd.DataFrame],
    catalog: list[dict[str, Any]],
    holding_sessions: list[int],
) -> tuple[pd.DataFrame, dict[str, dict[str, pd.Series]]]:
    definitions = {item["event_id"]: item for item in catalog}
    benchmark = frames.get("^TWOII")
    signals_by_symbol: dict[str, dict[str, pd.Series]] = {}
    rows: list[dict[str, Any]] = []
    breadth_events = [item for item in catalog if item["type"] == "market_breadth"]
    breadth_signals: dict[str, dict[str, pd.Series]] = {}
    if breadth_events:
        equity_symbols = [symbol for symbol in frames if not symbol.startswith("^")]
        for market in ("US", "TW"):
            returns: dict[str, pd.Series] = {}
            for symbol in equity_symbols:
                symbol_market = "TW" if symbol.endswith((".TW", ".TWO")) else "US"
                if symbol_market != market:
                    continue
                frame = frames[symbol]
                features = _features(frame)
                symbol_returns = features["return"].copy()
                symbol_returns.loc[features["suspected_action_or_price_error"]] = np.nan
                returns[symbol] = symbol_returns
            if not returns:
                continue
            panel = pd.concat(returns, axis=1)
            available = panel.notna().sum(axis=1)
            advances = panel.gt(0).sum(axis=1).div(available.replace(0, np.nan))
            declines = panel.lt(0).sum(axis=1).div(available.replace(0, np.nan))
            breadth_signals[market] = {}
            for event in breadth_events:
                ratio = advances if event["direction"] == "advance" else declines
                breadth_signals[market][event["event_id"]] = ratio.ge(event["threshold"]) & available.ge(event["min_symbols"])

    for symbol, frame in frames.items():
        signals = detect_event_signals(frame, symbol, catalog)
        market = "TW" if symbol == "^TWOII" or symbol.endswith((".TW", ".TWO")) else "US"
        instrument = "index" if symbol.startswith("^") else "equity"
        for event in breadth_events:
            if instrument == "equity" and market in breadth_signals:
                signals[event["event_id"]] = breadth_signals[market][event["event_id"]].reindex(frame.index).fillna(False).astype(bool)
            else:
                signals[event["event_id"]] = pd.Series(False, index=frame.index)
        signals_by_symbol[symbol] = signals
        close = frame["Close"].to_numpy(dtype=float)
        open_price = frame["Open"].to_numpy(dtype=float)
        dates = frame.index
        for event_id, mask in signals.items():
            event = definitions[event_id]
            if instrument == "index" and event["type"] != "otc_index_return":
                continue
            for i in np.flatnonzero(mask.to_numpy()):
                entry_i = i + 1
                row: dict[str, Any] = {
                    "event_id": event_id,
                    "family": event["family"],
                    "condition": event["condition"],
                    "market": market,
                    "symbol": symbol,
                    "instrument_type": instrument,
                    "event_date": dates[i].date().isoformat(),
                    "observable_at": "close_t",
                    "entry_date": dates[entry_i].date().isoformat() if entry_i < len(frame) else "",
                    "entry_price": float(open_price[entry_i]) if entry_i < len(frame) else np.nan,
                    "signal_close": float(close[i]),
                    "possible_action_or_price_error": False,
                    "benchmark_symbol": "^TWOII" if symbol.endswith(".TWO") and benchmark is not None else "",
                }
                entry_gap = open_price[entry_i] / close[i] - 1 if entry_i < len(frame) and close[i] else np.nan
                for horizon in holding_sessions:
                    value_col = f"return_{horizon}d"
                    benchmark_col = f"benchmark_return_{horizon}d"
                    excess_col = f"excess_return_{horizon}d"
                    exit_i = i + horizon
                    if entry_i >= len(frame) or exit_i >= len(frame) or not np.isfinite(open_price[entry_i]) or open_price[entry_i] <= 0:
                        row[value_col] = np.nan
                        row[benchmark_col] = np.nan
                        row[excess_col] = np.nan
                        continue
                    exit_close = close[exit_i]
                    if not np.isfinite(exit_close) or exit_close <= 0:
                        row[value_col] = np.nan
                        row[benchmark_col] = np.nan
                        row[excess_col] = np.nan
                        continue
                    path_returns = [close[entry_i] / open_price[entry_i] - 1]
                    if horizon > 1:
                        path_returns.extend((close[j] / close[j - 1] - 1) for j in range(entry_i + 1, exit_i + 1) if close[j - 1] > 0)
                    ambiguous = abs(entry_gap) > 0.50 or any(abs(value) > 0.50 for value in path_returns)
                    row["possible_action_or_price_error"] = bool(row["possible_action_or_price_error"] or ambiguous)
                    row[value_col] = np.nan if ambiguous else float(exit_close / open_price[entry_i] - 1)

                    if row["benchmark_symbol"] and benchmark is not None:
                        entry_date, exit_date = dates[entry_i], dates[exit_i]
                        if entry_date in benchmark.index and exit_date in benchmark.index:
                            b_open = float(benchmark.loc[entry_date, "Open"])
                            b_close = float(benchmark.loc[exit_date, "Close"])
                            b_return = b_close / b_open - 1 if b_open > 0 else np.nan
                            row[benchmark_col] = b_return
                            stock_return = row[value_col]
                            row[excess_col] = stock_return - b_return if pd.notna(stock_return) and np.isfinite(b_return) else np.nan
                        else:
                            row[benchmark_col] = np.nan
                            row[excess_col] = np.nan
                    else:
                        row[benchmark_col] = np.nan
                        row[excess_col] = np.nan
                rows.append(row)

    result = pd.DataFrame(rows)
    return result, signals_by_symbol

