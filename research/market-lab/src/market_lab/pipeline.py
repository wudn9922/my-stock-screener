from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import SPEC_VERSION, __version__
from .data import load_market_data
from .events import build_event_results, detect_event_signals, expand_event_catalog, load_event_spec
from .report import write_data_coverage, write_html_report, write_multiple_testing_report
from .statistics import summarize_events
from .strategies import analyze_strategies, expand_strategy_catalog, split_market_sessions


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_fingerprint(
    data_dir: Path,
    financial_dir: Path | None,
    tw_fundamentals: Path | None,
    financial_inventory_csv: Path | None,
    spec_dir: Path,
    methodology_path: Path,
    source_commit: str,
) -> tuple[str, list[dict[str, str]]]:
    inputs: list[Path] = sorted(data_dir.glob("*.csv"))
    if financial_inventory_csv and financial_inventory_csv.is_file():
        inputs.append(financial_inventory_csv)
    else:
        equity_symbols = []
        for path in data_dir.glob("*.csv"):
            symbol = path.name[:-4].upper()
            if symbol.endswith((".TW", ".TWO")) or symbol == "^TWOII":
                continue
            equity_symbols.append(symbol)
        if financial_dir and financial_dir.is_dir():
            inputs.extend(financial_dir / f"{symbol}.json" for symbol in equity_symbols if (financial_dir / f"{symbol}.json").is_file())
        if tw_fundamentals and tw_fundamentals.is_file():
            inputs.append(tw_fundamentals)
    inputs.extend(sorted(spec_dir.glob("*.json")))
    inputs.append(methodology_path)
    code_root = spec_dir.parent / "src/market_lab"
    inputs.extend(sorted(code_root.glob("*.py")))

    records: list[dict[str, str]] = []
    root = data_dir.parent
    for path in sorted(set(inputs)):
        label = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
        records.append({"path": label, "sha256": _sha256_file(path)})
    canonical = json.dumps({"source_commit": source_commit, "spec_version": SPEC_VERSION, "code_version": __version__, "files": records}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest(), records


def _current_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _market_timelines(frames: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    dates: dict[str, set[str]] = {"US": set(), "TW": set()}
    for symbol, frame in frames.items():
        if symbol.startswith("^"):
            continue
        market = "TW" if symbol.endswith((".TW", ".TWO")) else "US"
        dates[market].update(frame.index.strftime("%Y-%m-%d"))
    return {market: sorted(values) for market, values in dates.items() if values}


def _holdout_overlaps(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_periods = previous.get("market_periods", {}) if isinstance(previous, dict) else {}
    for market, split in current.items():
        old_test = previous_periods.get(market, {}).get("test", {})
        new_test = split.get("test", {})
        old_start, old_end = old_test.get("start", ""), old_test.get("end", "")
        new_start, new_end = new_test.get("start", ""), new_test.get("end", "")
        if old_start and old_end and new_start and new_end and old_start <= new_end and new_start <= old_end:
            return True
    return False


def _complete_event_grid(
    stats: pd.DataFrame,
    catalog: list[dict[str, Any]],
    markets: list[str],
    horizons: list[int],
) -> pd.DataFrame:
    if stats.empty:
        existing: dict[tuple[str, str, int], dict] = {}
    else:
        existing = {(row["event_id"], row["market"], int(row["holding_sessions"])): row for row in stats.to_dict(orient="records")}
    rows = []
    for event in catalog:
        for market in markets:
            for horizon in horizons:
                key = (event["event_id"], market, int(horizon))
                row = existing.get(key)
                if row is not None:
                    rows.append(row)
                else:
                    rows.append({
                        "event_id": event["event_id"], "family": event["family"], "market": market,
                        "holding_sessions": int(horizon), "total_signals": 0, "valid_event_observations": 0,
                        "unique_symbols": 0, "unique_event_dates": 0, "inference_warning": "no signal or no complete forward outcome",
                        "sample_start": "", "sample_end": "",
                    })
    result = pd.DataFrame(rows)
    if "q_value_fdr" not in result:
        result["q_value_fdr"] = np.nan
    return result


def _add_repository_warnings(issues: pd.DataFrame, coverage: pd.DataFrame, pyarrow_available: bool) -> pd.DataFrame:
    rows = issues.to_dict(orient="records") if not issues.empty else []
    def global_issue(code: str, details: str, severity: str = "high") -> None:
        rows.append({"scope": "repository", "market": "", "symbol": "", "issue_code": code, "severity": severity, "details": details})

    if not coverage.empty and (pd.to_numeric(coverage.loc[coverage["instrument_type"] == "equity", "span_years"], errors="coerce").fillna(0) < 9).any():
        global_issue("no_2016_2025_equity_history", "Available equity files do not span nine years; this is not a 2016-2025 study.")
    global_issue("historical_universe_unavailable", "No complete point-in-time constituents, delisted securities, or delisting returns; survivorship and selection bias remain.")
    global_issue("market_breadth_uses_current_snapshot", "Market breadth events use the current repo stock-file universe and are not point-in-time constituent breadth; historical breadth is survivor-biased.")
    global_issue("corporate_action_adjustment_unverified", "Source uses auto_adjust=False and actions=False; split status is not verifiable from OHLCV CSV, and dividends are absent.")
    global_issue("financial_announcement_timestamps_missing", "Current normalized financial rows have period-end dates but no complete point-in-time announcement timestamps; fundamentals events are disabled.")
    global_issue("us_benchmark_missing", "No matched broad US market index series in data/*.csv; US excess return and market regime are unavailable.", "medium")
    global_issue("taiwan_listed_benchmark_missing", "No Taiwan listed-market index series in data/*.csv; TW listed-stock excess return and broad regime are unavailable.", "medium")
    global_issue("costs_are_scenarios", "Trading fees, spreads, taxes, fill prices, and capacity are not source-verified; reported cost basis points are assumptions.", "high")
    if not pyarrow_available:
        global_issue("parquet_writer_missing", "pyarrow is not installed; event outcomes are written as compressed CSV and will not be mislabeled as Parquet.", "medium")
    return pd.DataFrame(rows, columns=["scope", "market", "symbol", "issue_code", "severity", "details"])


def _event_catalog_with_counts(catalog: list[dict[str, Any]], results: pd.DataFrame, markets: list[str]) -> pd.DataFrame:
    counts: dict[tuple[str, str], dict[str, Any]] = {}
    if not results.empty:
        grouped = results.groupby(["event_id", "market"], sort=True).agg(event_count=("symbol", "size"), unique_symbols=("symbol", "nunique"), unique_equities=("instrument_type", lambda s: int((s == "equity").sum()))).reset_index()
        for row in grouped.to_dict(orient="records"):
            counts[(row["event_id"], row["market"])] = row
    rows = []
    for item in catalog:
        row = dict(item)
        for market in markets:
            count = counts.get((item["event_id"], market), {})
            row[f"{market}_event_count"] = int(count.get("event_count", 0))
            row[f"{market}_unique_symbols"] = int(count.get("unique_symbols", 0))
            row[f"{market}_equity_event_count"] = int(count.get("unique_equities", 0))
        rows.append(row)
    return pd.DataFrame(rows)


def _write_event_result_file(results: pd.DataFrame, output_dir: Path, pyarrow_available: bool) -> str:
    if pyarrow_available:
        target = output_dir / "event-study-results.parquet"
        results.to_parquet(target, index=False, compression="zstd")
        (output_dir / "event-study-results.csv.gz").unlink(missing_ok=True)
        return target.name
    target = output_dir / "event-study-results.csv.gz"
    results.to_csv(target, index=False, compression="gzip")
    (output_dir / "event-study-results.parquet").unlink(missing_ok=True)
    return target.name


def run_pipeline(
    data_dir: Path,
    financial_dir: Path | None,
    tw_fundamentals: Path | None,
    financial_inventory_csv: Path | None,
    output_dir: Path,
    cache_dir: Path,
    source_commit: str,
    resume: bool,
    bootstrap_replicates: int,
    spec_dir: Path,
    methodology_path: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    repo_root = data_dir.resolve().parent
    source_commit = source_commit or _current_commit(repo_root)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    run_date = generated_at[:10]
    event_spec = load_event_spec(spec_dir / "events.json")
    strategy_spec = json.loads((spec_dir / "strategies.json").read_text(encoding="utf-8"))
    previous_manifest_path = output_dir / "run-manifest.json"
    try:
        previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous_manifest = {}
    event_catalog = expand_event_catalog(event_spec)
    holding_sessions = [int(value) for value in event_spec["holding_sessions"]]
    strategy_catalog = expand_strategy_catalog(strategy_spec, generated_at)
    cache_key, input_records = _input_fingerprint(data_dir, financial_dir, tw_fundamentals, financial_inventory_csv, spec_dir, methodology_path, source_commit)

    frames, coverage, issues = load_market_data(data_dir, financial_dir, tw_fundamentals, financial_inventory_csv)
    timelines = _market_timelines(frames)
    cache_base = cache_dir / cache_key
    cache_events = cache_base.with_suffix(".events.csv.gz")
    cache_coverage = cache_base.with_suffix(".coverage.csv")
    cache_issues = cache_base.with_suffix(".issues.csv")
    cache_meta = cache_base.with_suffix(".json")
    used_cache = bool(resume and cache_events.exists() and cache_coverage.exists() and cache_issues.exists() and cache_meta.exists())

    if used_cache:
        event_results = pd.read_csv(cache_events, compression="gzip")
        coverage = pd.read_csv(cache_coverage)
        issues = pd.read_csv(cache_issues).fillna("")
        signals_by_symbol = {symbol: detect_event_signals(frame, symbol, event_catalog) for symbol, frame in frames.items()}
    else:
        event_results, signals_by_symbol = build_event_results(frames, event_catalog, holding_sessions)
        event_results.to_csv(cache_events, index=False, compression="gzip")
        coverage.to_csv(cache_coverage, index=False)
        issues.to_csv(cache_issues, index=False)
        cache_meta.write_text(json.dumps({"cache_key": cache_key, "input_records": input_records, "created_at": generated_at}, indent=2) + "\n", encoding="utf-8")

    event_stats, distribution = summarize_events(event_results, holding_sessions, timelines, seed=20261019, replicates=bootstrap_replicates)
    event_stats = _complete_event_grid(event_stats, event_catalog, sorted(timelines), holding_sessions)
    split_manifest = {market: split_market_sessions(dates) for market, dates in timelines.items()}
    holdout_previously_evaluated = _holdout_overlaps(previous_manifest, split_manifest)
    strategies = analyze_strategies(frames, signals_by_symbol, strategy_catalog, strategy_spec, timelines, holdout_previously_evaluated)
    leaderboard, oos_results, train_results = strategies

    pyarrow_available = importlib.util.find_spec("pyarrow") is not None
    if pyarrow_available:
        import pyarrow
        pyarrow_version = pyarrow.__version__
    else:
        pyarrow_version = ""
    issues = _add_repository_warnings(issues, coverage, pyarrow_available)
    result_file = _write_event_result_file(event_results, output_dir, pyarrow_available)
    event_catalog_frame = _event_catalog_with_counts(event_catalog, event_results, sorted(timelines))
    coverage.to_csv(output_dir / "per-symbol-coverage.csv", index=False)
    issues.to_csv(output_dir / "quality-issues.csv", index=False)
    event_catalog_frame.to_csv(output_dir / "event-catalog.csv", index=False)
    event_stats.to_csv(output_dir / "event-statistics.csv", index=False)
    distribution.to_csv(output_dir / "event-return-distribution.csv", index=False)
    pd.DataFrame(strategy_catalog).to_csv(output_dir / "strategy-catalog.csv", index=False)
    leaderboard.to_csv(output_dir / "strategy-leaderboard.csv", index=False)
    oos_results.to_csv(output_dir / "out-of-sample-results.csv", index=False)
    shutil.copyfile(methodology_path, output_dir / "methodology.md")
    write_data_coverage(output_dir / "data-coverage.md", coverage, issues, source_commit, run_date)
    write_multiple_testing_report(output_dir / "multiple-testing-report.md", event_catalog_frame, event_stats, event_results, bootstrap_replicates)

    equity_rows = coverage.loc[coverage["instrument_type"] == "equity"] if not coverage.empty else pd.DataFrame()
    oos_strategy_count = int(oos_results["strategy_id"].nunique()) if not oos_results.empty else 0
    oos_pass_count = int(oos_results.loc[oos_results["oos_pass"], "strategy_id"].nunique()) if not oos_results.empty and "oos_pass" in oos_results else 0
    metadata = {
        "generated_at": generated_at,
        "repo_sha": source_commit,
        "data_fingerprint": cache_key,
        "price_file_count": int(len(coverage)),
        "equity_count": int(len(equity_rows)),
        "event_definition_count": len(event_catalog),
        "event_signal_count": int(len(event_results)),
        "bootstrap_replicates": int(bootstrap_replicates),
        "strategy_candidate_count": len(strategy_catalog),
        "strategy_ranking_cost_bps_by_market": strategy_spec["ranking_cost_round_trip_bps_by_market"],
        "strategy_cost_scenarios_bps_by_market": strategy_spec["sensitivity_cost_round_trip_bps_by_market"],
        "holdout_previously_evaluated": holdout_previously_evaluated,
        "holdout_status": "exposed_test_window" if holdout_previously_evaluated else "not_previously_evaluated",
        "oos_strategy_count": oos_strategy_count,
        "oos_pass_count": oos_pass_count,
        "event_result_storage": result_file,
        "pyarrow_available": pyarrow_available,
        "pyarrow_version": pyarrow_version,
        "cache_reused": used_cache,
        "market_periods": split_manifest,
        "model_execution": "Codex GPT-6 active model; Cloud reports no multi-model delegation capabilities; Sol/Luna/Gemini were not available for delegated execution.",
    }
    testing_log_path = output_dir / "testing-log.csv"
    strategy_spec_sha = _sha256_file(spec_dir / "strategies.json")
    testing_record = {
        "run_id": f"{cache_key[:12]}-{generated_at}",
        "generated_at": generated_at,
        "repo_sha": source_commit,
        "data_fingerprint": cache_key,
        "strategy_spec_sha256": strategy_spec_sha,
        "holdout_periods_json": json.dumps({market: value["test"] for market, value in split_manifest.items()}, sort_keys=True),
        "prior_overlapping_holdout_report_found": holdout_previously_evaluated,
        "holdout_status": metadata["holdout_status"],
        "notes": "Same test date range was previously computed and exposed during development; do not describe this run as untouched. New later history is required for an untouched holdout." if holdout_previously_evaluated else "First run for these test dates; status reflects exposure before this run only.",
    }
    prior_log = pd.read_csv(testing_log_path, dtype=str).fillna("") if testing_log_path.exists() else pd.DataFrame()
    if prior_log.empty or testing_record["run_id"] not in set(prior_log.get("run_id", [])):
        pd.concat([prior_log, pd.DataFrame([testing_record])], ignore_index=True).to_csv(testing_log_path, index=False)
    (output_dir / "run-manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_html_report(output_dir / "research-report.html", event_stats, distribution, leaderboard, oos_results, issues, metadata)
    return metadata

