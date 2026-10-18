#!/usr/bin/env python3
"""Small, independent SEC Q4 diluted EPS proof of concept.

The freeze-sample command is entirely local and reads the current financials and
SEC issuer map. It does not contact SEC or modify production financial files.
The retrieval and parsing commands are implemented below independently of Atlas
production calculations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATLAS_SCRIPTS = ROOT / "atlas" / "scripts"
sys.path.insert(0, str(ATLAS_SCRIPTS))
import build_financials as financials  # noqa: E402


CLASSIFICATIONS = (
    "q4_net_income_missing",
    "annual_diluted_eps_missing",
    "q1_q3_incomplete",
    "derivation_rejected",
)
EARLY = (2012, 2018)
RECENT = (2019, 2025)
SIZE_LIMIT = 1_000_000_000
POPULAR_TICKERS = {
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "GOOG",
    "IBM", "ORCL", "INTC", "QCOM", "NFLX", "DIS", "COIN", "ELF", "TLRY",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def col(section, name, length, default=None):
    values = section.get(name)
    if not isinstance(values, list):
        return [default] * length
    return values[:length] + [default] * max(0, length - len(values))


def source_map(progress):
    """Current ticker→CIK map from the repo's SEC submissions evidence."""
    mapped = defaultdict(list)
    for key, issuer in progress.get("issuers", {}).items():
        evidence = issuer.get("screeningEvidence") or {}
        cik = str(evidence.get("cik") or issuer.get("cik") or key).zfill(10)
        for ticker in evidence.get("submissionsTickers") or []:
            mapped[str(ticker).upper()].append({
                "cik": cik,
                "name": evidence.get("entityName"),
                "sic": evidence.get("sic"),
                "sicDescription": evidence.get("sicDescription"),
            })
    return mapped


def document_year_rows(doc, ticker, cik, company, sic, sic_description, path):
    annual = doc.get("annual") or {}
    quarterly = doc.get("quarterly") or {}
    qends = quarterly.get("end") if isinstance(quarterly.get("end"), list) else []
    aends = annual.get("end") if isinstance(annual.get("end"), list) else []
    qsrc = col(quarterly, "src", len(qends), "yahoo")
    asrc = col(annual, "src", len(aends), "yahoo")
    qeps = col(quarterly.get("values") or {}, "epsDiluted", len(qends))
    aeps = col(annual.get("values") or {}, "epsDiluted", len(aends))
    qni = col(quarterly.get("values") or {}, "netIncome", len(qends))
    qshares = col(quarterly.get("values") or {}, "dilutedShares", len(qends))
    fy = col(quarterly, "fy", len(qends))
    fq = col(quarterly, "fq", len(qends))
    derived = col(quarterly, "derived", len(qends))

    annual_by_fy = defaultdict(list)
    for index, year in enumerate(col(annual, "fy", len(aends))):
        if asrc[index] == "sec":
            annual_by_fy[year].append({
                "index": index,
                "end": aends[index],
                "epsDiluted": aeps[index],
                "values": {field: values[index] for field, values in (annual.get("values") or {}).items()
                           if isinstance(values, list) and len(values) > index},
            })

    sec_q_by_fy_q = defaultdict(list)
    for index, (year, quarter) in enumerate(zip(fy, fq)):
        if qsrc[index] == "sec" and quarter in (1, 2, 3, 4):
            sec_q_by_fy_q[(year, quarter)].append(index)

    split = doc.get("split") if isinstance(doc.get("split"), dict) else {}
    split_events = split.get("events") if isinstance(split.get("events"), list) else []
    quality = doc.get("quality") if isinstance(doc.get("quality"), dict) else {}
    out = []
    for index, (end, year, quarter, source, eps) in enumerate(zip(qends, fy, fq, qsrc, qeps)):
        if quarter != 4 or source != "sec" or eps is not None:
            continue
        try:
            year_number = int(year)
        except (TypeError, ValueError):
            continue
        if not 2012 <= year_number <= 2025:
            continue
        annual_rows = annual_by_fy.get(year, [])
        annual_row = annual_rows[0] if len(annual_rows) == 1 else None
        q_indices = [
            sec_q_by_fy_q.get((year, number), [None])[0]
            if len(sec_q_by_fy_q.get((year, number), [])) == 1 else None
            for number in (1, 2, 3)
        ]
        q_items = []
        for qindex in q_indices:
            if qindex is None:
                q_items.append(None)
                continue
            q_items.append({
                "end": qends[qindex],
                "epsDiluted": qeps[qindex],
                "netIncome": qni[qindex],
                "dilutedShares": qshares[qindex],
                "shareBasis": financials.share_basis(qshares[qindex], qni[qindex], qeps[qindex]),
            })

        if qni[index] is None:
            reason = "q4_net_income_missing"
            details = ["Q4 SEC row has no netIncome"]
        elif annual_row is None or annual_row["epsDiluted"] is None:
            reason = "annual_diluted_eps_missing"
            details = ["no unique SEC annual row for this FY" if annual_row is None
                       else "SEC annual epsDiluted is null"]
        elif any(item is None or item["epsDiluted"] is None or item["shareBasis"] is None for item in q_items):
            reason = "q1_q3_incomplete"
            details = []
            for number, item in enumerate(q_items, 1):
                if item is None:
                    details.append(f"Q{number} has no unique SEC period")
                elif item["epsDiluted"] is None:
                    details.append(f"Q{number} epsDiluted is null")
                elif item["shareBasis"] is None:
                    details.append(f"Q{number} has no usable diluted share basis")
        else:
            reason, details = "derivation_rejected", []
            annual_values = annual_row["values"]
            if financials.eps_inconsistent(annual_values):
                details.append("annual EPS does not agree with annual net income / diluted shares")
            annual_basis = financials.share_basis(
                annual_values.get("dilutedShares"), annual_values.get("netIncome"), annual_row["epsDiluted"]
            )
            bases = [annual_basis] + [item["shareBasis"] for item in q_items]
            if any(not basis for basis in bases):
                details.append("annual or quarterly diluted share basis unavailable")
            elif max(bases) / min(bases) > 1 + financials.SPLIT_MATCH:
                details.append("annual and Q1–Q3 share bases differ beyond split tolerance")
            else:
                value = financials.q4_eps(annual_row["epsDiluted"], [item["epsDiluted"] for item in q_items], bases)
                income = qni[index]
                if value is None:
                    details.append("annual less Q1–Q3 EPS could not be evaluated")
                elif value and (value > 0) != (income > 0):
                    details.append("candidate EPS sign disagrees with Q4 net income")
                elif abs(value - income / bases[0]) > (
                    financials.EPS_TOLERANCE * abs(value) + financials.Q4_EPS_ROUNDING
                ):
                    details.append("candidate EPS disagrees with Q4 net income / annual share basis")
                else:
                    details.append("existing production rule still leaves this Q4 EPS empty")

        out.append({
            "ticker": ticker,
            "company_name": company,
            "cik": cik,
            "sic": sic,
            "sic_description": sic_description,
            "category": reason,
            "fy": year_number,
            "q4_end": end,
            "filing_period_source": source,
            "current_q4_eps_diluted": None,
            "current_q4_net_income": qni[index],
            "current_annual_eps_diluted": annual_row["epsDiluted"] if annual_row else None,
            "q1_q3": q_items,
            "category_evidence": details,
            "quarterly_derived_flag": derived[index],
            "financials_path": str(path.relative_to(ROOT)),
            "financials_sha256": sha256_file(path),
            "split_adjusted": bool(split.get("adjusted")),
            "split_events": split_events,
            "existing_quality_level": quality.get("level"),
            "existing_quality_issues": quality.get("issues", []),
        })
    return out, annual_by_fy, qends, qeps, qsrc, derived, split_events


def median_sec_revenue(doc):
    annual = doc.get("annual") or {}
    ends = annual.get("end") if isinstance(annual.get("end"), list) else []
    values = annual.get("values") or {}
    revenue = col(values, "revenue", len(ends))
    src = col(annual, "src", len(ends), "yahoo")
    years = col(annual, "fy", len(ends))
    selected = [value for year, source, value in zip(years, src, revenue)
                if source == "sec" and value is not None and 2019 <= int(year) <= 2025]
    return statistics.median(selected) if selected else None


def seed_rank(seed, category, ticker):
    return sha256_bytes(f"{seed}|{category}|{ticker}".encode("utf-8"))


def diversity_select(candidates, count, seed, category):
    ordered = sorted(candidates, key=lambda item: (seed_rank(seed, category, item["ticker"]), item["ticker"]))
    chosen, used_ciks, used_industries = [], set(), set()
    for enforce_diversity in (True, False):
        for candidate in ordered:
            industry = str(candidate.get("sic") or "")[:2]
            if candidate in chosen or candidate["cik"] in used_ciks:
                continue
            if enforce_diversity and industry in used_industries:
                continue
            chosen.append(candidate)
            used_ciks.add(candidate["cik"])
            used_industries.add(industry)
            if len(chosen) == count:
                return chosen
    return chosen


def freeze_sample(seed="20261018", output=None):
    report_dir = ROOT / "docs" / "report" / "sec-q4-eps-poc"
    output = Path(output) if output else report_dir / "sample.json"
    if not output.is_absolute():
        output = ROOT / output
    if output.exists():
        raise FileExistsError(f"refusing to replace frozen sample: {output}")

    snapshot_path = ROOT / "docs/report/fundamentals/us-market-snapshot.json"
    progress_path = ROOT / "docs/report/fundamentals/us-market-progress-summary.json"
    financial_dir = ROOT / "docs/report/financials"
    progress = load_json(progress_path)
    snapshot = load_json(snapshot_path)
    ticker_map = source_map(progress)
    current_symbols = {item["symbol"]: item for item in snapshot.get("eligibleSymbols", [])}

    docs = {}
    missing_periods = []
    all_us_missing = 0
    for path in sorted(financial_dir.glob("*.json")):
        try:
            doc = load_json(path)
        except (OSError, ValueError):
            continue
        if doc.get("market") != "US":
            continue
        quarterly = doc.get("quarterly") or {}
        ends = quarterly.get("end") if isinstance(quarterly.get("end"), list) else []
        fy = col(quarterly, "fy", len(ends))
        fq = col(quarterly, "fq", len(ends))
        eps = col(quarterly.get("values") or {}, "epsDiluted", len(ends))
        for year, quarter, value in zip(fy, fq, eps):
            if quarter == 4 and value is None:
                all_us_missing += 1
        docs[doc.get("symbol", path.stem)] = (path, doc)

    classified_by_category = defaultdict(list)
    source_eligible_rows = 0
    for ticker, (path, doc) in docs.items():
        maps = ticker_map.get(ticker, [])
        if doc.get("market") != "US" or ticker not in current_symbols or len({x["cik"] for x in maps}) != 1:
            continue
        mapping = maps[0]
        company = mapping["name"] or current_symbols[ticker].get("name")
        records, annual_by_fy, qends, qeps, qsrc, derived, split_events = document_year_rows(
            doc, ticker, mapping["cik"], company, mapping["sic"], mapping["sicDescription"], path
        )
        source_eligible_rows += len(records)
        for record in records:
            classified_by_category[record["category"]].append(record)
        # Positive control rows use the local SEC series only, and are selected
        # without looking at any SEC response from this POC.
        q = doc.get("quarterly") or {}
        fy = col(q, "fy", len(qends))
        fq = col(q, "fq", len(qends))
        for index, (end, year, quarter, source, eps) in enumerate(zip(qends, fy, fq, qsrc, qeps)):
            if (quarter != 4 or source != "sec" or eps is None or derived[index] is not None
                    or not (2012 <= int(year) <= 2025)):
                continue
            record = {
                "ticker": ticker,
                "company_name": company,
                "cik": mapping["cik"],
                "sic": mapping["sic"],
                "sic_description": mapping["sicDescription"],
                "fy": int(year),
                "q4_end": end,
                "existing_q4_eps_diluted": eps,
                "existing_q4_source": source,
                "quarterly_derived_flag": derived[index],
                "financials_path": str(path.relative_to(ROOT)),
                "financials_sha256": sha256_file(path),
                "split_adjusted": bool((doc.get("split") or {}).get("adjusted")),
                "split_events": split_events,
                "existing_quality_level": (doc.get("quality") or {}).get("level"),
            }
            classified_by_category["direct_sec_controls"].append(record)

    by_ticker = defaultdict(list)
    for category, records in classified_by_category.items():
        for record in records:
            by_ticker[(category, record["ticker"])].append(record)

    def period_pair(records):
        early = sorted((r for r in records if EARLY[0] <= r["fy"] <= EARLY[1]), key=lambda r: (r["fy"], r["q4_end"]))
        recent = sorted((r for r in records if RECENT[0] <= r["fy"] <= RECENT[1]), key=lambda r: (r["fy"], r["q4_end"]))
        return (early[0], recent[0]) if early and recent else None

    candidates = {category: [] for category in CLASSIFICATIONS}
    for category in CLASSIFICATIONS:
        tickers = sorted({r["ticker"] for r in classified_by_category[category]})
        for ticker in tickers:
            rows = by_ticker[(category, ticker)]
            pair = period_pair(rows)
            path, doc = docs[ticker]
            size = median_sec_revenue(doc)
            ciks = {row["cik"] for row in rows}
            if pair and len(ciks) == 1 and size is not None and size <= SIZE_LIMIT and ticker not in POPULAR_TICKERS:
                candidates[category].append({
                    "ticker": ticker,
                    "cik": rows[0]["cik"],
                    "company_name": rows[0]["company_name"],
                    "sic": rows[0]["sic"],
                    "sic_description": rows[0]["sic_description"],
                    "median_2019_2025_sec_revenue_proxy": size,
                    "periods": list(pair),
                })

    selected = []
    candidate_counts = {}
    for category in CLASSIFICATIONS:
        candidate_counts[category] = {
            "eligible_tickers_with_early_and_recent_periods": len(candidates[category]),
            "eligible_periods": sum(len(item["periods"]) for item in candidates[category]),
        }
        chosen = diversity_select(candidates[category], 2, seed, category)
        candidate_counts[category]["selected_tickers"] = [item["ticker"] for item in chosen]
        for candidate in chosen:
            selected.append({
                **{key: candidate[key] for key in (
                    "ticker", "cik", "company_name", "sic", "sic_description",
                    "median_2019_2025_sec_revenue_proxy",
                )},
                "sample_role": "missing_q4_eps",
                "missing_reason": category,
                "selection_rank_sha256": seed_rank(seed, category, candidate["ticker"]),
                "selection_rule": "seeded SHA-256 rank, then greedily prefer an unused two-digit SIC group",
                "periods": candidate["periods"],
            })

    control_candidates = []
    control_records = classified_by_category["direct_sec_controls"]
    control_by_ticker = defaultdict(list)
    for row in control_records:
        control_by_ticker[row["ticker"]].append(row)
    for ticker, rows in control_by_ticker.items():
        pair = period_pair(rows)
        if not pair:
            continue
        path, doc = docs[ticker]
        size = median_sec_revenue(doc)
        if size is None or size > SIZE_LIMIT or ticker in POPULAR_TICKERS:
            continue
        control_candidates.append({
            "ticker": ticker,
            "cik": rows[0]["cik"],
            "company_name": rows[0]["company_name"],
            "sic": rows[0]["sic"],
            "sic_description": rows[0]["sic_description"],
            "median_2019_2025_sec_revenue_proxy": size,
            "periods": list(pair),
        })
    # Prefer smaller issuers, with the fixed seed as the tie breaker. One issuer
    # per CIK and two-digit SIC diversity are enforced by diversity_select.
    ordered_controls = sorted(
        control_candidates,
        key=lambda item: (item["median_2019_2025_sec_revenue_proxy"],
                          seed_rank(seed, "direct_sec_controls", item["ticker"]), item["ticker"]),
    )
    control_selected, used_ciks, used_sic = [], set(), set()
    for enforce_diversity in (True, False):
        for item in ordered_controls:
            industry = str(item.get("sic") or "")[:2]
            if item["cik"] in used_ciks or (enforce_diversity and industry in used_sic):
                continue
            control_selected.append(item)
            used_ciks.add(item["cik"])
            used_sic.add(industry)
            if len(control_selected) == 2:
                break
        if len(control_selected) == 2:
            break
    candidate_counts["direct_sec_controls"] = {
        "eligible_tickers_with_early_and_recent_periods_and_sec_revenue_le_1bn": len(control_candidates),
        "selected_tickers": [item["ticker"] for item in control_selected],
        "historical_grade_a_ledger_available": False,
    }
    for candidate in control_selected:
        selected.append({
            **{key: candidate[key] for key in (
                "ticker", "cik", "company_name", "sic", "sic_description",
                "median_2019_2025_sec_revenue_proxy",
            )},
            "sample_role": "positive_control_candidate",
            "missing_reason": None,
            "historical_grade_a_status": "not_auditable: acceptance ledger and original classifier absent from main",
            "selection_rule": "direct SEC Q4 epsDiluted exists locally with no per-row derived flag; smallest revenue proxy, then fixed seed, with SIC diversity",
            "periods": candidate["periods"],
        })

    # The categories are required to have two selected tickers; fail closed rather
    # than silently making a post-query substitution.
    gaps = {category: candidate_counts[category]["selected_tickers"] for category in CLASSIFICATIONS
            if len(candidate_counts[category]["selected_tickers"]) != 2}
    if gaps or len(control_selected) != 2:
        raise RuntimeError(f"frozen sample incomplete; no SEC query permitted: {gaps or 'positive control gap'}")

    classification_counts = {category: len(classified_by_category[category]) for category in CLASSIFICATIONS}
    payload = {
        "version": 1,
        "sample_id": "sec-q4-diluted-eps-poc-20261018",
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_commit": "dd7ac76821976ea42f6291bc09eb5e63b28c2ecc",
        "selection_seed": seed,
        "selection_constraints": {
            "current_directory_and_cik": "ticker must exist in main's US eligibleSymbols and map to one SEC CIK in us-market-progress-summary.json",
            "financial_rows": "US financials; SEC-sourced Q4 row; fiscal year 2012–2025; Q4 epsDiluted null for missing cases",
            "annual_revenue_proxy": "median of SEC-sourced annual revenue for FY 2019–2025 <= USD 1B",
            "periods_per_ticker": "earliest fiscal year in 2012–2018 and earliest fiscal year in 2019–2025; no SEC response is used in selection",
            "industry_diversity": "one CIK per category; greedily prefer unused two-digit SIC industry",
            "sorting": "missing-category tickers ranked by SHA-256(seed|category|ticker); controls ranked by revenue proxy, then SHA-256; dates sorted by FY and end date",
            "popular_ticker_exclusions": sorted(POPULAR_TICKERS),
            "positive_control_predicate": "existing local Q4 row has source sec, non-null epsDiluted, null quarterly derived flag; original Grade A acceptance ledger is absent, so the historical Grade A label cannot be asserted",
        },
        "input_audit": {
            "missing_inputs": [
                "docs/report/q4-eps-missing-classification.json",
                "docs/report/q4-eps-acceptance-ledger.json",
                "atlas/scripts/classify_q4_eps_missing.py",
            ],
            "temporary_classifier": "in-memory deterministic classification implemented in this POC from main's financials; no input report regenerated or overwritten",
            "financials_dir": "docs/report/financials/",
            "financials_files": len(docs),
            "us_financials_missing_q4_eps_all_sources_all_years": all_us_missing,
            "sec_sourced_current_directory_missing_q4_eps_periods_fy2012_2025": source_eligible_rows,
            "temporary_classification_counts": classification_counts,
            "candidate_pool": candidate_counts,
            "snapshot_sha256": sha256_file(snapshot_path),
            "progress_summary_sha256": sha256_file(progress_path),
        },
        "sample": selected,
        "freeze_note": "Selection is locked before SEC submissions, data-set ZIPs, filing documents, or earnings releases are queried. Any post-freeze retrieval failure remains E/incomplete and will not trigger replacement.",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["freeze_sha256"] = sha256_bytes(canonical)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-sample", help="select and freeze a deterministic 10-ticker sample from local main data")
    freeze.add_argument("--seed", default="20261018")
    freeze.add_argument("--out", default="docs/report/sec-q4-eps-poc/sample.json")
    args = parser.parse_args(argv)
    if args.command == "freeze-sample":
        result = freeze_sample(args.seed, args.out)
        print(json.dumps({
            "sample": str(Path(args.out)),
            "freeze_sha256": result["freeze_sha256"],
            "symbols": [f"{row['ticker']}:{row['sample_role']}" for row in result["sample"]],
            "missing_category_counts": result["input_audit"]["temporary_classification_counts"],
            "candidate_pool": result["input_audit"]["candidate_pool"],
        }, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
