from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from .events import composite_strategy_signal
from .statistics import moving_block_mean_inference


def expand_strategy_catalog(spec: dict[str, Any], created_at: str) -> list[dict[str, Any]]:
    strategies: list[dict[str, Any]] = []
    sequence = 1
    for candidate in spec["candidates"]:
        for horizon in spec["holding_sessions"]:
            strategies.append({
                "strategy_id": f"MLAB-S{sequence:03d}",
                "candidate_key": f"{candidate['base_id']}_h{horizon}",
                "base_id": candidate["base_id"],
                "family": candidate["family"],
                "event_id": candidate.get("event_id", ""),
                "composite": candidate.get("composite", ""),
                "holding_sessions": int(horizon),
                "signal_observable_at": "close_t",
                "entry_assumption": "next_available_symbol_bar_open",
                "exit_assumption": f"close_after_{horizon}_subsequent_symbol_bars",
                "long_only": True,
                "cost_scenarios_round_trip_bps_by_market": json.dumps(spec["sensitivity_cost_round_trip_bps_by_market"], sort_keys=True),
                "ranking_cost_round_trip_bps_by_market": json.dumps(spec["ranking_cost_round_trip_bps_by_market"], sort_keys=True),
                "us_min_median_20d_turnover": spec["liquidity_filter_median_20d_turnover"]["US"],
                "tw_min_median_20d_turnover": spec["liquidity_filter_median_20d_turnover"]["TW"],
                "position_size_adv_fraction_scenario": spec["position_size_adv_fraction_scenario"],
                "first_created_at": created_at,
                "spec_frozen_on": spec["frozen_on"],
                "parameters_json": json.dumps({"holding_sessions": int(horizon), "event_id": candidate.get("event_id"), "composite": candidate.get("composite"), "long_only": True, "liquidity_filter": spec["liquidity_filter_median_20d_turnover"]}, sort_keys=True),
            })
            sequence += 1
    return strategies


def split_market_sessions(sessions: list[str]) -> dict[str, dict[str, Any]]:
    if not sessions:
        return {name: {"start": "", "end": "", "n_sessions": 0} for name in ("train", "validation", "test")}
    count = len(sessions)
    train_end = max(1, int(count * 0.60))
    validation_end = max(train_end + 1, int(count * 0.80))
    validation_end = min(validation_end, count)
    pieces = {
        "train": sessions[:train_end],
        "validation": sessions[train_end:validation_end],
        "test": sessions[validation_end:],
    }
    return {
        name: {"start": dates[0] if dates else "", "end": dates[-1] if dates else "", "n_sessions": len(dates)}
        for name, dates in pieces.items()
    }


def apply_round_trip_cost(gross_return: float, cost_round_trip_bps: int) -> float:
    cost_each_side = int(cost_round_trip_bps) / 20000.0
    return float((1.0 + float(gross_return)) * (1.0 - cost_each_side) ** 2 - 1.0)


def _signal_for_strategy(strategy: dict[str, Any], signals: dict[str, pd.Series], frame: pd.DataFrame) -> pd.Series:
    if strategy.get("event_id"):
        return signals.get(strategy["event_id"], pd.Series(False, index=frame.index)).reindex(frame.index).fillna(False).astype(bool)
    return composite_strategy_signal(frame, str(strategy.get("composite", "")))


def _performance_metrics(
    daily_returns: pd.Series,
    trades: list[dict[str, Any]],
    cost_bps: int,
    horizon: int,
    market: str,
    seed: int,
) -> dict[str, Any]:
    values = daily_returns.to_numpy(dtype=float)
    n = len(values)
    wealth = np.cumprod(1.0 + values) if n else np.asarray([], dtype=float)
    total_return = float(wealth[-1] - 1) if n else np.nan
    cagr = float(wealth[-1] ** (252.0 / n) - 1) if n and wealth[-1] > 0 else np.nan
    annual_volatility = float(np.std(values, ddof=1) * np.sqrt(252)) if n > 1 else np.nan
    daily_mean = float(np.mean(values)) if n else np.nan
    sharpe = float(daily_mean / np.std(values, ddof=1) * np.sqrt(252)) if n > 1 and np.std(values, ddof=1) > 0 else np.nan
    downside = values[values < 0]
    sortino = float(daily_mean / np.sqrt(np.mean(downside ** 2)) * np.sqrt(252)) if len(downside) and np.mean(downside ** 2) > 0 else np.nan
    drawdown = wealth / np.maximum.accumulate(wealth) - 1 if n else np.asarray([], dtype=float)
    max_drawdown = float(np.min(drawdown)) if n else np.nan
    calmar = float(cagr / abs(max_drawdown)) if np.isfinite(cagr) and max_drawdown < 0 else np.nan
    trade_returns = np.asarray([trade["net_return"] for trade in trades], dtype=float)
    winners = trade_returns[trade_returns > 0]
    losers = trade_returns[trade_returns < 0]
    profit_factor = float(winners.sum() / abs(losers.sum())) if len(losers) and abs(losers.sum()) > 0 else (np.inf if len(winners) else np.nan)
    date_values = pd.Series(daily_returns.index.strftime("%Y-%m-%d"), index=daily_returns.index)
    ci_low, ci_high, p_value, block_len = moving_block_mean_inference(values, date_values, list(date_values), horizon, seed, replicates=500) if n > 2 else (np.nan, np.nan, np.nan, 0)
    annual = daily_returns.groupby(daily_returns.index.year).apply(lambda series: float((1 + series).prod() - 1)).to_dict() if n else {}
    turnover = [trade["median_20d_turnover"] for trade in trades if np.isfinite(trade["median_20d_turnover"])]
    return {
        "cost_round_trip_bps": int(cost_bps),
        "trading_sessions": n,
        "total_return": total_return,
        "cagr": cagr,
        "annual_volatility": annual_volatility,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "calmar_ratio": calmar,
        "trade_count": len(trades),
        "win_rate": float(np.mean(trade_returns > 0)) if len(trade_returns) else np.nan,
        "average_winner": float(np.mean(winners)) if len(winners) else np.nan,
        "average_loser": float(np.mean(losers)) if len(losers) else np.nan,
        "win_loss_ratio": float(np.mean(winners) / abs(np.mean(losers))) if len(winners) and len(losers) and np.mean(losers) != 0 else np.nan,
        "profit_factor": profit_factor,
        "average_holding_sessions": float(np.mean([trade["holding_sessions"] for trade in trades])) if trades else np.nan,
        "average_trade_cost_bps": float(cost_bps) if trades else np.nan,
        "average_median_20d_turnover": float(np.mean(turnover)) if turnover else np.nan,
        "capacity_proxy_at_1pct_adv": float(np.median(turnover) * 0.01) if turnover else np.nan,
        "daily_mean_ci95_low": ci_low,
        "daily_mean_ci95_high": ci_high,
        "daily_mean_bootstrap_p": p_value,
        "bootstrap_block_sessions": block_len,
        "annual_returns_json": json.dumps({str(year): value for year, value in annual.items()}, sort_keys=True),
    }


def backtest_candidate(
    market: str,
    strategy: dict[str, Any],
    split_name: str,
    split: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    signals_by_symbol: dict[str, dict[str, pd.Series]],
    minimum_turnover: float,
    cost_bps: int,
    market_sessions: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start, end = split.get("start", ""), split.get("end", "")
    horizon = int(strategy["holding_sessions"])
    trades: list[dict[str, Any]] = []
    daily_position_returns: dict[str, list[float]] = {}
    excluded_action_or_price_error = 0
    if start and end:
        for symbol, frame in frames.items():
            if (symbol.startswith("^") or ("TW" if symbol.endswith((".TW", ".TWO")) else "US") != market):
                continue
            signals = signals_by_symbol.get(symbol, {})
            signal = _signal_for_strategy(strategy, signals, frame)
            close = frame["Close"].astype(float)
            open_price = frame["Open"].astype(float)
            turnover = (close * frame["Volume"].astype(float)).shift(1).rolling(20, min_periods=10).median()
            eligible = signal & (turnover >= minimum_turnover)
            dates = frame.index.strftime("%Y-%m-%d").to_numpy()
            close_values, open_values = close.to_numpy(), open_price.to_numpy()
            turnover_values = turnover.to_numpy()
            next_allowed = -1
            for i in np.flatnonzero(eligible.to_numpy()):
                signal_date = dates[i]
                if signal_date < start or signal_date > end or i < next_allowed:
                    continue
                entry_i, exit_i = i + 1, i + horizon
                if exit_i >= len(frame) or dates[exit_i] > end or open_values[entry_i] <= 0 or close_values[exit_i] <= 0:
                    continue
                holding_closes = close_values[entry_i:exit_i + 1]
                holding_opens = open_values[entry_i:exit_i + 1]
                discontinuities = np.concatenate([
                    holding_closes / holding_opens - 1.0,
                    holding_closes[1:] / holding_closes[:-1] - 1.0,
                    [open_values[entry_i] / close_values[i] - 1.0],
                ])
                if not np.isfinite(discontinuities).all() or np.any(np.abs(discontinuities) > 0.50):
                    excluded_action_or_price_error += 1
                    continue
                entry_cost = cost_bps / 20000.0
                for j in range(entry_i, exit_i + 1):
                    raw_daily = close_values[j] / open_values[j] - 1 if j == entry_i else close_values[j] / close_values[j - 1] - 1
                    net_daily = (1 + raw_daily) * (1 - entry_cost) - 1 if j in (entry_i, exit_i) else raw_daily
                    daily_position_returns.setdefault(dates[j], []).append(float(net_daily))
                gross_trade_return = float(close_values[exit_i] / open_values[entry_i] - 1)
                net_trade_return = apply_round_trip_cost(gross_trade_return, cost_bps)
                trades.append({
                    "strategy_id": strategy["strategy_id"],
                    "market": market,
                    "split": split_name,
                    "symbol": symbol,
                    "signal_date": signal_date,
                    "entry_date": dates[entry_i],
                    "exit_date": dates[exit_i],
                    "gross_return": gross_trade_return,
                    "net_return": net_trade_return,
                    "holding_sessions": horizon,
                    "cost_round_trip_bps": int(cost_bps),
                    "median_20d_turnover": float(turnover_values[i]),
                })
                next_allowed = exit_i

    split_sessions = [date for date in market_sessions if start <= date <= end] if start and end else []
    daily = pd.Series(
        [float(np.mean(daily_position_returns[date])) if daily_position_returns.get(date) else 0.0 for date in split_sessions],
        index=pd.to_datetime(split_sessions),
        dtype=float,
    )
    seed_text = f"{strategy['strategy_id']}|{market}|{split_name}|{cost_bps}"
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
    metrics = _performance_metrics(daily, trades, cost_bps, horizon, market, seed)
    metrics.update({
        "strategy_id": strategy["strategy_id"],
        "candidate_key": strategy["candidate_key"],
        "market": market,
        "split": split_name,
        "period_start": start,
        "period_end": end,
        "market_sessions": int(split.get("n_sessions", 0)),
        "benchmark_excess_return": np.nan,
        "strategy_status": "exploratory_only; not deployment validated",
        "suspected_action_or_price_error_trades_excluded": excluded_action_or_price_error,
    })
    return metrics, trades


def analyze_strategies(
    frames: dict[str, pd.DataFrame],
    signals_by_symbol: dict[str, dict[str, pd.Series]],
    strategies: list[dict[str, Any]],
    spec: dict[str, Any],
    market_timelines: dict[str, list[str]],
    holdout_previously_evaluated: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ranking_costs = {market: int(cost) for market, cost in spec["ranking_cost_round_trip_bps_by_market"].items()}
    sensitivity_costs = spec["sensitivity_cost_round_trip_bps_by_market"]
    minimums = spec["liquidity_filter_median_20d_turnover"]
    market_splits = {market: split_market_sessions(dates) for market, dates in market_timelines.items()}
    train_rows: list[dict[str, Any]] = []
    train_results: dict[tuple[str, str], dict[str, Any]] = {}
    for market in sorted(market_timelines):
        train_split = market_splits[market]["train"]
        for strategy in strategies:
            result, _ = backtest_candidate(market, strategy, "train", train_split, frames, signals_by_symbol, minimums[market], ranking_costs[market], market_timelines[market])
            train_rows.append(result)
            train_results[(market, strategy["strategy_id"])] = result

    selected: dict[str, list[dict[str, Any]]] = {}
    top_n = int(spec["select_top_n_per_market_by_training_net_sharpe"])
    for market in sorted(market_timelines):
        candidates = [row for row in train_rows if row["market"] == market and row["trade_count"] >= 30 and np.isfinite(row["sharpe_ratio"])]
        candidates.sort(key=lambda row: (row["sharpe_ratio"], row["total_return"] if np.isfinite(row["total_return"]) else -np.inf), reverse=True)
        ids = {row["strategy_id"] for row in candidates[:top_n]}
        selected[market] = [strategy for strategy in strategies if strategy["strategy_id"] in ids]

    leaderboard_rows: list[dict[str, Any]] = []
    oos_rows: list[dict[str, Any]] = []
    for train_row in train_rows:
        market, strategy_id = train_row["market"], train_row["strategy_id"]
        strategy = next(item for item in strategies if item["strategy_id"] == strategy_id)
        row = {
            "strategy_id": strategy_id,
            "candidate_key": strategy["candidate_key"],
            "family": strategy["family"],
            "market": market,
            "event_id": strategy["event_id"],
            "holding_sessions": strategy["holding_sessions"],
            "parameters_json": strategy["parameters_json"],
            "train_total_return": train_row["total_return"],
            "train_cagr": train_row["cagr"],
            "train_sharpe_ratio": train_row["sharpe_ratio"],
            "train_max_drawdown": train_row["max_drawdown"],
            "train_trade_count": train_row["trade_count"],
            "train_win_rate": train_row["win_rate"],
            "train_profit_factor": train_row["profit_factor"],
            "train_annual_returns_json": train_row["annual_returns_json"],
            "validation_total_return": np.nan,
            "validation_sharpe_ratio": np.nan,
            "validation_trade_count": 0,
            "validation_ci95_low": np.nan,
            "validation_ci95_high": np.nan,
            "test_total_return": np.nan,
            "test_sharpe_ratio": np.nan,
            "test_trade_count": 0,
            "test_ci95_low": np.nan,
            "test_ci95_high": np.nan,
            "validation_annual_returns_json": "{}",
            "test_annual_returns_json": "{}",
            "selected_from_training": strategy_id in {item["strategy_id"] for item in selected.get(market, [])},
            "out_of_sample_pass": False,
            "rank_metric": f"training_net_sharpe_at_assumed_{ranking_costs[market]}bps_round_trip",
            "status": "exploratory_only; test window previously evaluated during development" if holdout_previously_evaluated else "exploratory_only; final test computed once; all source prices have unverified action adjustment and no delisted universe",
            "holdout_status": "exposed_test_window" if holdout_previously_evaluated else "not_previously_evaluated",
        }
        if row["selected_from_training"]:
            validation, _ = backtest_candidate(market, strategy, "validation", market_splits[market]["validation"], frames, signals_by_symbol, minimums[market], ranking_costs[market], market_timelines[market])
            test, _ = backtest_candidate(market, strategy, "test", market_splits[market]["test"], frames, signals_by_symbol, minimums[market], ranking_costs[market], market_timelines[market])
            row.update({
                "validation_total_return": validation["total_return"],
                "validation_sharpe_ratio": validation["sharpe_ratio"],
                "validation_trade_count": validation["trade_count"],
                "validation_ci95_low": validation["daily_mean_ci95_low"],
                "validation_ci95_high": validation["daily_mean_ci95_high"],
                "test_total_return": test["total_return"],
                "test_sharpe_ratio": test["sharpe_ratio"],
                "test_trade_count": test["trade_count"],
                "test_ci95_low": test["daily_mean_ci95_low"],
                "test_ci95_high": test["daily_mean_ci95_high"],
                "validation_annual_returns_json": validation["annual_returns_json"],
                "test_annual_returns_json": test["annual_returns_json"],
            })
            for cost in sensitivity_costs[market]:
                cost_result, _ = backtest_candidate(market, strategy, "test", market_splits[market]["test"], frames, signals_by_symbol, minimums[market], int(cost), market_timelines[market])
                duration_ok = cost_result["trading_sessions"] >= 756
                statistically_positive = np.isfinite(cost_result["daily_mean_ci95_low"]) and cost_result["daily_mean_ci95_low"] > 0
                pass_flag = bool(not holdout_previously_evaluated and duration_ok and statistically_positive and cost_result["trade_count"] >= 100 and cost_result["total_return"] > 0)
                oos_rows.append({
                    "strategy_id": strategy_id,
                    "candidate_key": strategy["candidate_key"],
                    "market": market,
                    "event_id": strategy["event_id"],
                    "holding_sessions": strategy["holding_sessions"],
                    "selection_rule": "top 3 by training net Sharpe; selection frozen before validation/test calculation",
                    "split": "exposed_test_window" if holdout_previously_evaluated else "final_test",
                    "holdout_status": "exposed_test_window" if holdout_previously_evaluated else "not_previously_evaluated",
                    "period_start": cost_result["period_start"],
                    "period_end": cost_result["period_end"],
                    **{key: cost_result.get(key) for key in ("cost_round_trip_bps", "trading_sessions", "total_return", "cagr", "annual_volatility", "sharpe_ratio", "sortino_ratio", "max_drawdown", "calmar_ratio", "trade_count", "win_rate", "win_loss_ratio", "profit_factor", "average_holding_sessions", "average_trade_cost_bps", "average_median_20d_turnover", "capacity_proxy_at_1pct_adv", "daily_mean_ci95_low", "daily_mean_ci95_high", "daily_mean_bootstrap_p", "annual_returns_json", "suspected_action_or_price_error_trades_excluded")},
                    "sufficient_3y_test_history": duration_ok,
                    "oos_pass": pass_flag,
                    "decision": "not_validated: prior report exposed the same test window during development" if holdout_previously_evaluated else ("not_validated: test window shorter than 3 years and corporate-action/delisting data incomplete" if not pass_flag else "eligible for independent replication only"),
                })
        leaderboard_rows.append(row)

    leaderboard = pd.DataFrame(leaderboard_rows)
    if not leaderboard.empty:
        leaderboard["training_rank"] = leaderboard.groupby("market")["train_sharpe_ratio"].rank(method="min", ascending=False)
        leaderboard = leaderboard.sort_values(["market", "training_rank", "strategy_id"], na_position="last")
    out_of_sample = pd.DataFrame(oos_rows)
    return leaderboard, out_of_sample, pd.DataFrame(train_rows)

