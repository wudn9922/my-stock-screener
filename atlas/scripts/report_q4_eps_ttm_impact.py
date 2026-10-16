#!/usr/bin/env python3
"""Compare P/E-river EPS(TTM)/annual inputs before and after Q4 grade gating.

This mirrors FinancialHistory.parseSeries, peRiver.buildEpsPeriods, and epsAt for a
fixed as-of date. It is a data impact report; it does not run or change any screener rule.
"""
import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

QUARTER_GAP_MIN_DAYS = 60
QUARTER_GAP_MAX_DAYS = 130
MAX_TTM_AGE_DAYS = 220
MAX_ANNUAL_AGE_DAYS = 550


def number_or_none(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def periods(document, section_name, formal_q4):
    section = document.get(section_name) if isinstance(document.get(section_name), dict) else {}
    ends = section.get("end") if isinstance(section.get("end"), list) else []
    fq = section.get("fq") if isinstance(section.get("fq"), list) else []
    values = section.get("values") if isinstance(section.get("values"), dict) else {}
    quality = section.get("epsQuality") if isinstance(section.get("epsQuality"), list) else []
    q4_facts = document.get("secQ4EpsFacts") if isinstance(document.get("secQ4EpsFacts"), dict) else {}
    us_financials = document.get("market") != "TW"
    output = []
    for index, end_text in enumerate(ends):
        try:
            end = date.fromisoformat(end_text)
        except (TypeError, ValueError):
            continue
        quarter = fq[index] if section_name == "quarterly" and index < len(fq) else None
        grade = quality[index] if index < len(quality) and isinstance(quality[index], dict) else {}
        diluted_values = values.get("epsDiluted") if isinstance(values.get("epsDiluted"), list) else []
        basic_values = values.get("epsBasic") if isinstance(values.get("epsBasic"), list) else []
        diluted = number_or_none(diluted_values[index]) if index < len(diluted_values) else None
        basic = number_or_none(basic_values[index]) if index < len(basic_values) else None
        if us_financials and grade.get("epsDiluted") in ("C", "D"):
            diluted = None
        if us_financials and grade.get("epsBasic") in ("C", "D"):
            basic = None
        if us_financials and section_name == "quarterly" and formal_q4 and quarter == 4:
            if grade.get("epsDiluted") not in ("A", "B"):
                diluted = None
            if grade.get("epsBasic") not in ("A", "B"):
                basic = None
        eps = diluted if diluted is not None else basic
        filed = grade.get("epsDilutedFiled") if diluted is not None else grade.get("epsBasicFiled")
        if not filed:
            fact = q4_facts.get(end_text) if isinstance(q4_facts.get(end_text), dict) else {}
            eps_fact_name = "epsDiluted" if diluted is not None else "epsBasic"
            fact = fact.get(eps_fact_name) if isinstance(fact.get(eps_fact_name), dict) else {}
            filed = fact.get("filed")
        delay = 60 if section_name == "annual" or quarter == 4 else 45
        available = end + timedelta(days=delay)
        try:
            if filed:
                available = max(available, date.fromisoformat(filed))
        except (TypeError, ValueError):
            pass
        output.append({"end": end, "available": available, "eps": eps, "quarter": quarter})
    return output


def latest_eps(document, as_of, formal_q4):
    quarterly = periods(document, "quarterly", formal_q4)
    annual = periods(document, "annual", False)
    split = document.get("split") if isinstance(document.get("split"), dict) else {}
    events = split.get("events") if isinstance(split.get("events"), list) else []
    if split.get("adjusted") is not True and events:
        split_dates = []
        for event in events:
            try:
                split_dates.append(date.fromisoformat(event[0]))
            except (IndexError, TypeError, ValueError):
                continue
        all_ends = [period["end"] for period in quarterly + annual]
        if split_dates and all_ends and max(split_dates) > min(all_ends):
            cutoff = max(split_dates)
            quarterly = [period for period in quarterly if period["end"] >= cutoff]
            annual = [period for period in annual if period["end"] >= cutoff]
    issues = (document.get("quality") or {}).get("issues") or []
    if "quarterly-ytd-suspected" in issues:
        quarterly = []
    published_quarters = sorted(
        (period for period in quarterly if period["available"] <= as_of),
        key=lambda period: period["end"],
    )
    last_four = published_quarters[-4:]
    if (len(last_four) == 4 and all(period["eps"] is not None for period in last_four)
            and all(QUARTER_GAP_MIN_DAYS <= (last_four[i]["end"] - last_four[i - 1]["end"]).days <= QUARTER_GAP_MAX_DAYS
                    for i in range(1, 4))
            and (as_of - last_four[-1]["end"]).days <= MAX_TTM_AGE_DAYS):
        return {"eps": sum(period["eps"] for period in last_four),
                "basis": "ttm", "periodEnd": last_four[-1]["end"].isoformat()}
    published_annual = sorted(
        (period for period in annual if period["available"] <= as_of),
        key=lambda period: period["end"],
    )
    if published_annual:
        latest = published_annual[-1]
        if latest["eps"] is not None and (as_of - latest["end"]).days <= MAX_ANNUAL_AGE_DAYS:
            return {"eps": latest["eps"], "basis": "annual", "periodEnd": latest["end"].isoformat()}
    return None


def read_document(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def report(baseline_dir, current_dir, as_of, output_json, output_csv):
    before_dir = Path(baseline_dir)
    after_dir = Path(current_dir)
    symbols = {
        path.stem for directory in (before_dir, after_dir)
        for path in directory.glob("*.json")
        if path.name != "index.json" and not path.name.startswith("q4-eps-")
    }
    rows = []
    for symbol in sorted(symbols):
        results = []
        for directory, formal_q4 in ((before_dir, False), (after_dir, True)):
            document = read_document(directory / f"{symbol}.json")
            results.append(latest_eps(document or {}, as_of, formal_q4))
        after_document = read_document(after_dir / f"{symbol}.json")
        before, after = results
        rows.append({
            "symbol": symbol,
            "market": (after_document or {}).get("market"),
            "before_eps": before["eps"] if before else None,
            "before_basis": before["basis"] if before else None,
            "before_period_end": before["periodEnd"] if before else None,
            "after_eps": after["eps"] if after else None,
            "after_basis": after["basis"] if after else None,
            "after_period_end": after["periodEnd"] if after else None,
            "positive_before": bool(before and before["eps"] > 0),
            "positive_after": bool(after and after["eps"] > 0),
        })
    changed = [row for row in rows if (row["before_eps"], row["before_basis"], row["before_period_end"])
               != (row["after_eps"], row["after_basis"], row["after_period_end"])]
    summary = {
        "schema": "q4-eps-ttm-impact-v1",
        "asOf": as_of.isoformat(),
        "baselineRule": "Q4 EPS stored in baseline files was accepted unless null/C/D; no A/B requirement.",
        "currentRule": "P/E river Q4 inputs require A/B, reject C/D, and use filing dates when available.",
        "symbols": len(rows),
        "markets": {
            market: {
                "symbols": sum(row["market"] == market for row in rows),
                "epsInputChanged": sum(row["market"] == market and row in changed for row in rows),
                "ttmCountBefore": sum(row["market"] == market and row["before_basis"] == "ttm" for row in rows),
                "ttmCountAfter": sum(row["market"] == market and row["after_basis"] == "ttm" for row in rows),
                "annualFallbackBefore": sum(row["market"] == market and row["before_basis"] == "annual" for row in rows),
                "annualFallbackAfter": sum(row["market"] == market and row["after_basis"] == "annual" for row in rows),
                "noEpsBefore": sum(row["market"] == market and row["before_eps"] is None for row in rows),
                "noEpsAfter": sum(row["market"] == market and row["after_eps"] is None for row in rows),
                "positiveEpsBefore": sum(row["market"] == market and row["positive_before"] for row in rows),
                "positiveEpsAfter": sum(row["market"] == market and row["positive_after"] for row in rows),
                "positiveToNonpositiveOrNull": sum(row["market"] == market and row["positive_before"] and not row["positive_after"] for row in changed),
                "nonpositiveOrNullToPositive": sum(row["market"] == market and not row["positive_before"] and row["positive_after"] for row in changed),
            }
            for market in ("US", "TW")
        },
        "epsInputChanged": len(changed),
        "ttmCountBefore": sum(row["before_basis"] == "ttm" for row in rows),
        "ttmCountAfter": sum(row["after_basis"] == "ttm" for row in rows),
        "annualFallbackBefore": sum(row["before_basis"] == "annual" for row in rows),
        "annualFallbackAfter": sum(row["after_basis"] == "annual" for row in rows),
        "noEpsBefore": sum(row["before_eps"] is None for row in rows),
        "noEpsAfter": sum(row["after_eps"] is None for row in rows),
        "positiveEpsBefore": sum(row["positive_before"] for row in rows),
        "positiveEpsAfter": sum(row["positive_after"] for row in rows),
        "positiveToNonpositiveOrNull": sum(row["positive_before"] and not row["positive_after"] for row in changed),
        "nonpositiveOrNullToPositive": sum(not row["positive_before"] and row["positive_after"] for row in changed),
        "changedRowsCsv": Path(output_csv).name,
    }
    Path(output_json).write_text(json.dumps({"summary": summary, "rows": changed}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with Path(output_csv).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False))


def main(argv=None):
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--current-dir", default=str(repo / "docs/report/financials"))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--output-json", default=str(repo / "docs/report/financials/q4-eps-ttm-impact.json"))
    parser.add_argument("--output-csv", default=str(repo / "docs/report/financials/q4-eps-ttm-impact-by-symbol.csv"))
    args = parser.parse_args(argv)
    report(args.baseline_dir, args.current_dir, date.fromisoformat(args.as_of), args.output_json, args.output_csv)


if __name__ == "__main__":
    main()
