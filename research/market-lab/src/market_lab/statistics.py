from __future__ import annotations

import math
import hashlib
from typing import Iterable

import numpy as np
import pandas as pd

MIN_EVENT_DATES_FOR_INFERENCE = 10


def benjamini_hochberg(p_values: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(p_values), dtype=float)
    adjusted = np.full(values.shape, np.nan, dtype=float)
    valid = np.isfinite(values)
    p = values[valid]
    if not len(p):
        return adjusted
    order = np.argsort(p)
    ranked = p[order]
    q = ranked * len(ranked) / np.arange(1, len(ranked) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0.0, 1.0)
    restored = np.empty_like(q)
    restored[order] = q
    adjusted[valid] = restored
    return adjusted


def moving_block_mean_inference(
    values: np.ndarray,
    event_dates: pd.Series,
    timeline: list[str],
    horizon: int,
    seed: int,
    replicates: int = 500,
) -> tuple[float, float, float, int]:
    """Date-clustered circular block bootstrap for the mean event return."""
    good = np.isfinite(values)
    values = np.asarray(values[good], dtype=float)
    dates = event_dates.loc[good].astype(str).to_numpy()
    if len(values) < 2 or len(timeline) < 2:
        return np.nan, np.nan, np.nan, 0
    date_to_idx = {date: i for i, date in enumerate(timeline)}
    sums = np.zeros(len(timeline), dtype=float)
    counts = np.zeros(len(timeline), dtype=float)
    for date, value in zip(dates, values):
        idx = date_to_idx.get(date)
        if idx is not None:
            sums[idx] += value
            counts[idx] += 1
    observed = float(values.mean())
    residual_sums = sums - observed * counts
    block_len = min(len(timeline), max(10, min(60, int(horizon))))
    blocks_needed = int(math.ceil(len(timeline) / block_len))
    offsets = np.arange(block_len, dtype=int)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(timeline), size=(replicates, blocks_needed))
    indices = (starts[:, :, None] + offsets[None, None, :]) % len(timeline)
    indices = indices.reshape(replicates, -1)[:, : len(timeline)]
    boot_counts = counts[indices].sum(axis=1)
    boot_residuals = residual_sums[indices].sum(axis=1) / np.maximum(boot_counts, 1.0)
    boot_residuals = boot_residuals[boot_counts > 0]
    if not len(boot_residuals):
        return np.nan, np.nan, np.nan, block_len
    low, high = np.quantile(observed + boot_residuals, [0.025, 0.975])
    p = (1 + np.sum(np.abs(boot_residuals) >= abs(observed))) / (1 + len(boot_residuals))
    return float(low), float(high), float(p), block_len


def summarize_events(
    event_results: pd.DataFrame,
    holding_sessions: list[int],
    market_timelines: dict[str, list[str]],
    seed: int = 20261019,
    replicates: int = 500,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = [
        "event_id", "family", "market", "holding_sessions", "total_signals", "valid_event_observations",
        "unique_symbols", "unique_equities", "unique_indexes", "unique_event_dates", "mean_return", "median_return", "positive_probability",
        "p_return_gt_3pct", "p_return_gt_5pct", "p_return_gt_10pct", "p_return_lt_minus_3pct",
        "p_return_lt_minus_5pct", "p_return_lt_minus_10pct", "var_5pct", "cvar_5pct",
        "mean_excess_return", "ci95_low", "ci95_high", "p_value_block_bootstrap", "q_value_fdr",
        "bootstrap_block_sessions", "sample_start", "sample_end", "inference_warning",
    ]
    if event_results.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(columns=["market", "family", "holding_sessions", "month", "return_bin", "count"])

    summaries: list[dict] = []
    all_tests: list[tuple[int, str, float]] = []
    bins = [-np.inf, -0.20, -0.10, -0.05, -0.03, 0.0, 0.03, 0.05, 0.10, 0.20, np.inf]
    labels = ["<-20%", "-20:-10%", "-10:-5%", "-5:-3%", "-3:0%", "0:3%", "3:5%", "5:10%", "10:20%", ">=20%"]
    distribution_rows: list[dict] = []

    for (event_id, market), signal_group in event_results.groupby(["event_id", "market"], sort=True):
        for horizon in holding_sessions:
            value_col = f"return_{horizon}d"
            excess_col = f"excess_return_{horizon}d"
            values_all = pd.to_numeric(signal_group[value_col], errors="coerce")
            # Ambiguous action windows have already been nulled per horizon by
            # the event builder; a later anomaly must not erase earlier clean horizons.
            valid = values_all.notna()
            group = signal_group.loc[valid].copy()
            values = values_all.loc[valid].to_numpy(dtype=float)
            if len(values):
                mean_value = float(np.mean(values))
                median = float(np.median(values))
                unique_event_dates = int(group["event_date"].nunique())
                if unique_event_dates >= MIN_EVENT_DATES_FOR_INFERENCE:
                    ci_low, ci_high, p_value, block_len = moving_block_mean_inference(
                        values,
                        group["event_date"],
                        market_timelines.get(market, []),
                        horizon,
                        seed + int(hashlib.sha256(f"{event_id}|{market}|{horizon}".encode()).hexdigest()[:8], 16),
                        replicates,
                    )
                else:
                    ci_low, ci_high, p_value, block_len = np.nan, np.nan, np.nan, 0
                excess = pd.to_numeric(group[excess_col], errors="coerce").dropna()
                row = {
                    "event_id": event_id,
                    "family": str(signal_group["family"].iloc[0]),
                    "market": market,
                    "holding_sessions": horizon,
                    "total_signals": int(len(signal_group)),
                    "valid_event_observations": int(len(values)),
                    "unique_symbols": int(group["symbol"].nunique()),
                    "unique_equities": int(group.loc[group["instrument_type"] == "equity", "symbol"].nunique()),
                    "unique_indexes": int(group.loc[group["instrument_type"] == "index", "symbol"].nunique()),
                    "unique_event_dates": unique_event_dates,
                    "mean_return": mean_value,
                    "median_return": median,
                    "positive_probability": float(np.mean(values > 0)),
                    "p_return_gt_3pct": float(np.mean(values > 0.03)),
                    "p_return_gt_5pct": float(np.mean(values > 0.05)),
                    "p_return_gt_10pct": float(np.mean(values > 0.10)),
                    "p_return_lt_minus_3pct": float(np.mean(values < -0.03)),
                    "p_return_lt_minus_5pct": float(np.mean(values < -0.05)),
                    "p_return_lt_minus_10pct": float(np.mean(values < -0.10)),
                    "var_5pct": float(np.quantile(values, 0.05)),
                    "cvar_5pct": float(np.mean(values[values <= np.quantile(values, 0.05)])),
                    "mean_excess_return": float(excess.mean()) if len(excess) else np.nan,
                    "ci95_low": ci_low,
                    "ci95_high": ci_high,
                    "p_value_block_bootstrap": p_value,
                    "q_value_fdr": np.nan,
                    "bootstrap_block_sessions": block_len,
                    "sample_start": str(group["event_date"].min()),
                    "sample_end": str(group["event_date"].max()),
                    "inference_warning": "" if len(values) >= 30 and unique_event_dates >= MIN_EVENT_DATES_FOR_INFERENCE else f"only {unique_event_dates} distinct event dates; descriptive statistics only, no inference",
                }
                all_tests.append((len(summaries), str(market), p_value))
                summaries.append(row)
                months = pd.to_datetime(group["event_date"]).dt.to_period("M").astype(str)
                categorized = pd.cut(values, bins=bins, labels=labels, right=False)
                hist = pd.DataFrame({"month": months.to_numpy(), "return_bin": categorized.astype(str)})
                family = str(signal_group["family"].iloc[0])
                for (month, return_bin), count in hist.value_counts().items():
                    distribution_rows.append({"market": market, "family": family, "holding_sessions": horizon, "month": month, "return_bin": return_bin, "count": int(count)})
            else:
                summaries.append({
                    "event_id": event_id, "family": str(signal_group["family"].iloc[0]), "market": market,
                    "holding_sessions": horizon, "total_signals": int(len(signal_group)), "valid_event_observations": 0,
                    "unique_symbols": 0, "unique_equities": 0, "unique_indexes": 0, "unique_event_dates": 0, "inference_warning": "no complete, unambiguous forward observations",
                    "sample_start": "", "sample_end": "",
                })

    by_market: dict[str, list[int]] = {}
    for row_index, market, p_value in all_tests:
        if np.isfinite(p_value):
            by_market.setdefault(market, []).append(row_index)
    for market, indexes in by_market.items():
        q_values = benjamini_hochberg([summaries[i]["p_value_block_bootstrap"] for i in indexes])
        for i, q_value in zip(indexes, q_values):
            summaries[i]["q_value_fdr"] = float(q_value)

    distribution = pd.DataFrame(distribution_rows, columns=["market", "family", "holding_sessions", "month", "return_bin", "count"])
    if not distribution.empty:
        distribution = distribution.groupby(["market", "family", "holding_sessions", "month", "return_bin"], as_index=False)["count"].sum()
    return pd.DataFrame(summaries, columns=columns), distribution

