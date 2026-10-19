#!/usr/bin/env python3
"""Freeze and run the SEC Q4 diluted EPS stage-three evidence sample."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = ROOT / "docs/report/sec-q4-eps-stage3"
STAGE2 = ROOT / "docs/report/sec-q4-eps-stage2"
sys.path.insert(0, str(STAGE2))
import stage2_poc as stage2  # noqa: E402

SEED = "20261018-stage3"
SHARDS = {4, 5, 6, 7}
CONTROL_COUNT = 60
MISSING_CLASS_ORDER = stage2.MISSING_CLASS_ORDER


def _control_projection(row):
    hidden = {"company_name", "sic", "sic_description", "exchange", "symbol", "cik",
              "local_quarter_eps_diluted"}
    result = {key: value for key, value in row.items() if key not in hidden}
    result.update({"symbol": row["symbol"], "cik": row["cik"],
                   "company_name": row.get("company_name"), "sic": row.get("sic"),
                   "sic_description": row.get("sic_description")})
    return result


def select_stage3_controls(seed, excluded_symbols, *, answer_set=None, issuers=None):
    """Select new identity-only control periods, excluding every stage-two ticker."""
    answer_path = ROOT / "docs/report/q4-8k/answer-set.json"
    answer_set = answer_set if answer_set is not None else stage2.load_json(answer_path)
    issuers = issuers if issuers is not None else stage2.issuer_index()
    candidates, rejected, total_keys = stage2._control_candidates(answer_set, issuers, {}, seed)
    eligible = [row for row in candidates
                if row.get("answer_key_mod_8") in SHARDS and row["symbol"] not in excluded_symbols]
    best_by_ticker = {}
    for row in eligible:
        current = best_by_ticker.get(row["symbol"])
        rank = (row["selection_rank"], row["fy"], row["q4_end"])
        if current is None or rank < (current["selection_rank"], current["fy"], current["q4_end"]):
            best_by_ticker[row["symbol"]] = row
    ranked = sorted(best_by_ticker.values(), key=lambda row: (
        stage2.seeded_rank(seed, "control-company", row["symbol"]), row["symbol"], row["selection_rank"],
    ))
    if len(ranked) < CONTROL_COUNT:
        raise RuntimeError(f"only {len(ranked)} new control companies remain after stage-two exclusions")
    return ([_control_projection(row) for row in ranked[:CONTROL_COUNT]], {
        "answer_set_sorted_unique_keys": total_keys,
        "control_candidate_periods_after_local_validation_excluding_stage2": len(eligible),
        "control_candidate_rows_rejected_before_symbol_exclusion": dict(sorted(rejected.items())),
        "control_symbols_excluded_from_stage2": len(excluded_symbols),
        "control_unique_companies_available_excluding_stage2": len(best_by_ticker),
        "control_key_indices_selected": sorted(SHARDS),
        "control_period_count": min(len(ranked), CONTROL_COUNT),
    })


def build_sample(seed=SEED):
    stage2_sample_path = STAGE2 / "sample.json"
    stage2_sample = stage2.load_json(stage2_sample_path)
    excluded_symbols = {row["symbol"] for row in stage2_sample.get("controls", [])}
    if len(excluded_symbols) != 60:
        raise RuntimeError("stage-two sample must contain 60 distinct control tickers")

    # The same main-classification and missing-period selector is used with a fresh seed.
    sample = stage2.make_sample(seed)
    answer_set = stage2.load_json(ROOT / "docs/report/q4-8k/answer-set.json")
    issuers = stage2.issuer_index()
    controls, control_audit = select_stage3_controls(
        seed, excluded_symbols, answer_set=answer_set, issuers=issuers)
    if excluded_symbols.intersection(row["symbol"] for row in controls):
        raise RuntimeError("stage-three control sample overlaps the frozen stage-two controls")

    stage2_sample_hash = stage2.sha256_file(stage2_sample_path)
    sample["sample_id"] = "sec-q4-eps-stage3-60x60-20261018"
    sample["stage"] = "stage-3"
    sample["regression_fix_commit"] = stage2.git(
        "log", "-1", "--format=%H", "--", "tests/test_sec_q4_eps_stage2_regression.py")
    sample["stage2_control_exclusion"] = {
        "source_path": str(stage2_sample_path.relative_to(ROOT)),
        "source_sha256": stage2_sample_hash,
        "excluded_tickers": sorted(excluded_symbols),
        "count": len(excluded_symbols),
        "all_stage2_control_periods_excluded": True,
    }
    sample["selection_rules"]["selection_seed"] = seed
    sample["selection_rules"]["control_periods"] = (
        "60 unique tickers with one Q4 period each; answer-set rows whose key index in sorted unique keys "
        "has mod 8 in {4,5,6,7}; deterministic SHA-256(seed|control-company|ticker) ordering; "
        "exclude every ticker in the frozen stage-two control sample")
    sample["selection_rules"]["stage2_control_exclusion"] = (
        "all 60 unique ticker keys in docs/report/sec-q4-eps-stage2/sample.json are excluded before control selection")
    sample["input_hashes"]["stage2_control_exclusion_source"] = {
        "path": str(stage2_sample_path.relative_to(ROOT)), "sha256": stage2_sample_hash}
    sample["controls"] = controls
    sample["selection_audit"].update(control_audit)
    sample["selection_audit"]["missing_company_count"] = len(sample["missing_companies"])
    sample["selection_audit"]["missing_period_count"] = sum(
        len(company["periods"]) for company in sample["missing_companies"])
    sample["selection_frozen_before_sec_requests"] = True
    sample["freeze_sha256"] = stage2.canonical_sha256(sample)
    check_sample(sample)
    return sample


def check_sample(sample):
    stage2.check_sample(sample)
    if sample.get("selection_seed") == stage2.SEED:
        raise ValueError("stage-three seed must differ from stage two")
    excluded = set(sample.get("stage2_control_exclusion", {}).get("excluded_tickers", []))
    controls = sample.get("controls", [])
    if len(excluded) != 60 or excluded.intersection(row["symbol"] for row in controls):
        raise ValueError("stage-three controls overlap stage-two control tickers")
    if any(row.get("answer_key_mod_8") not in SHARDS for row in controls):
        raise ValueError("a control is outside answer-set key-index shards 4–7")
    if len({row["symbol"] for row in controls}) != CONTROL_COUNT:
        raise ValueError("stage-three controls must be 60 unique companies")
    class_counts = Counter(row["class"] for row in sample["missing_companies"])
    if class_counts != Counter({klass: 15 for klass in MISSING_CLASS_ORDER}):
        raise ValueError("stage-three missing sample must contain 15 companies per class")


def freeze_sample(output=STAGE / "sample.json", seed=SEED):
    output = Path(output)
    if not output.is_absolute():
        output = ROOT / output
    if output.exists():
        raise FileExistsError(f"refusing to replace frozen sample: {output}")
    sample = build_sample(seed)
    stage2.dump_json(output, sample)
    (output.parent / "sample.sha256").write_text(
        f"{stage2.sha256_file(output)}  sample.json\n", encoding="ascii")
    return sample


def run_extraction():
    if (STAGE / "extraction.json").exists() or (STAGE / "source-ledger.jsonl").exists():
        raise FileExistsError("refusing to run a second SEC extraction for stage three")
    return stage2.run_extraction(
        STAGE / "sample.json", STAGE / "extraction.json", stage_dir=STAGE,
        cache_dir=Path("/tmp/sec-q4-eps-stage2-cache"),
        state_path=Path("/tmp/sec-q4-eps-stage3-run-state.json"),
    )


def freeze_extraction():
    sample_path = STAGE / "sample.json"
    extraction_path = STAGE / "extraction.json"
    stage2.assert_committed(sample_path.relative_to(ROOT))
    stage2.assert_committed((STAGE / "sample.sha256").relative_to(ROOT))
    expected = (STAGE / "sample.sha256").read_text(encoding="ascii").split()[0]
    if stage2.sha256_file(sample_path) != expected:
        raise RuntimeError("sample hash changed before extraction freeze")
    extraction = stage2.load_json(extraction_path)
    if extraction.get("sample_sha256") != expected:
        raise RuntimeError("extraction refers to a different frozen sample")
    digest = stage2.sha256_file(extraction_path)
    (STAGE / "extraction.sha256").write_text(
        f"{digest}  extraction.json\n", encoding="ascii")
    return {"extraction_sha256": digest, "complete": extraction.get("complete"),
            "stopped_reason": extraction.get("stopped_reason"),
            "metrics": extraction.get("metrics")}


def split_factor(events, filing_date):
    factor = 1.0
    for event in events or []:
        try:
            when, ratio = str(event[0]), float(event[1])
        except (IndexError, TypeError, ValueError):
            continue
        if ratio > 0 and filing_date and when > filing_date:
            factor *= ratio
    return factor


def compare_control_rows(sample, extraction, answer_set):
    answers = {(row.get("key"), row.get("end")): row for row in answer_set.get("rows", [])}
    extracted = {(row.get("symbol"), row.get("q4_end"), row.get("fy")): row
                 for row in extraction.get("period_results", []) if row.get("sample_role") == "control"}
    rows = []
    accepted = correct = sign_errors = 0
    for control in sample["controls"]:
        ticker, end, fy = control["symbol"], control["q4_end"], control["fy"]
        answer = answers.get((ticker, end))
        if not answer or answer.get("fy") != fy:
            raise RuntimeError(f"control no longer maps to its answer-set row: {ticker} FY{fy} {end}")
        result = extracted.get((ticker, end, fy), {})
        chosen = result.get("accepted") or {}
        value = chosen.get("value_as_filed") if result.get("status") == "A_verified_direct_q4_eps" else None
        filing_date = chosen.get("filing_date") if value is not None else None
        events = control.get("split_events") or []
        extracted_factor = split_factor(events, filing_date)
        answer_factor = split_factor(events, answer.get("filed"))
        extracted_current = float(value) / extracted_factor if value is not None and extracted_factor else None
        answer_current = float(answer["value"])
        comparable = extracted_current is not None
        difference = abs(extracted_current - answer_current) if comparable else None
        is_correct = bool(comparable and difference <= 0.005)
        sign_error = bool(comparable and extracted_current * answer_current < 0)
        if comparable:
            accepted += 1
            correct += int(is_correct)
            sign_errors += int(sign_error)
        rows.append({
            "symbol": ticker, "fy": fy, "q4_end": end,
            "answer_key_index": control.get("answer_key_index"),
            "answer_key_mod_8": control.get("answer_key_mod_8"),
            "status": result.get("status", "missing_extraction_row"),
            "extraction_source": chosen.get("source"), "extraction_accession": chosen.get("accession"),
            "extraction_filing_date": filing_date, "extracted_eps_as_filed": value,
            "extracted_future_split_factor": extracted_factor,
            "extracted_eps_current_basis": extracted_current,
            "answer_set_accession": answer.get("accession"),
            "answer_set_filing_date": answer.get("filed"),
            "answer_set_eps_current_basis": answer_current,
            "answer_set_future_split_factor": answer_factor,
            "absolute_difference_current_basis": difference,
            "correct_within_0_005": is_correct if comparable else None,
            "sign_error": sign_error if comparable else None,
        })
    metrics = {
        "controls_selected": len(rows), "accepted": accepted, "correct": correct,
        "incorrect": accepted - correct, "sign_errors": sign_errors,
        "unverifiable": len(rows) - accepted,
        "accuracy_among_accepted": round(correct / accepted, 6) if accepted else None,
        "accepted_share_of_controls": round(accepted / len(rows), 6) if rows else None,
        "tolerance": 0.005,
        "acceptance_bar": {
            "minimum_controls_accepted": 50,
            "all_accepted_controls_correct": correct == accepted,
            "zero_sign_errors": sign_errors == 0,
            "passed": accepted >= 50 and correct == accepted and sign_errors == 0,
        },
    }
    return {"metrics": metrics, "rows": rows}


def compare_controls():
    sample_path = STAGE / "sample.json"
    extraction_path = STAGE / "extraction.json"
    sample_sha_path = STAGE / "sample.sha256"
    extraction_sha_path = STAGE / "extraction.sha256"
    # Freeze checks precede loading answer-set values.
    sample_rel = sample_path.relative_to(ROOT)
    stage2.assert_committed(sample_rel)
    stage2.assert_committed(sample_sha_path.relative_to(ROOT))
    extraction_rel = extraction_path.relative_to(ROOT)
    stage2.assert_committed(extraction_rel)
    stage2.assert_committed(extraction_sha_path.relative_to(ROOT))
    sample_commit = stage2.git("log", "-1", "--format=%H", "--", str(sample_rel))
    extraction_commit = stage2.git("log", "-1", "--format=%H", "--", str(extraction_rel))
    comparison_base_commit = stage2.git("rev-parse", "HEAD")
    sample_sha = stage2.sha256_file(sample_path)
    extraction_sha = stage2.sha256_file(extraction_path)
    if sample_sha != sample_sha_path.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("committed sample.sha256 does not match sample.json")
    if extraction_sha != extraction_sha_path.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("committed extraction.sha256 does not match extraction.json")
    sample = stage2.load_json(sample_path)
    extraction = stage2.load_json(extraction_path)
    answer_path = ROOT / "docs/report/q4-8k/answer-set.json"
    if stage2.sha256_file(answer_path) != sample["input_hashes"]["answer_set_identity_source"]["sha256"]:
        raise RuntimeError("answer set differs from the committed control identity source")
    answer_set = stage2.load_json(answer_path)
    comparison = compare_control_rows(sample, extraction, answer_set)
    payload = {
        "schema_version": 1,
        "comparison_created_after_extraction_commit": True,
        "sample_sha256": sample_sha,
        "sample_commit": sample_commit,
        "extraction_sha256": extraction_sha,
        "extraction_commit": extraction_commit,
        "comparison_base_commit": comparison_base_commit,
        "answer_set_sha256": stage2.sha256_file(answer_path),
        **comparison,
    }
    stage2.dump_json(STAGE / "controls-comparison.json", payload)
    (STAGE / "controls-comparison.sha256").write_text(
        f"{stage2.sha256_file(STAGE / 'controls-comparison.json')}  controls-comparison.json\n",
        encoding="ascii")
    return payload


def _status_counts(rows):
    return Counter(row.get("status", "unknown") for row in rows)


def _format_number(value):
    if value is None:
        return "—"
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def render_report():
    sample = stage2.load_json(STAGE / "sample.json")
    extraction = stage2.load_json(STAGE / "extraction.json")
    comparison = stage2.load_json(STAGE / "controls-comparison.json")
    results = extraction.get("period_results", [])
    missing = [row for row in results if row.get("sample_role") == "missing"]
    controls = [row for row in results if row.get("sample_role") == "control"]
    accepted_missing = [row for row in missing if row.get("status") == "A_verified_direct_q4_eps"]
    stats = comparison["metrics"]
    class_rows = []
    for klass in MISSING_CLASS_ORDER:
        group = [row for row in missing if row.get("classification_class") == klass]
        counts = _status_counts(group)
        class_rows.append(
            f"| `{klass}` | {len(group)} | {counts.get('A_verified_direct_q4_eps', 0)} | "
            f"{counts.get('B_candidate_needs_verification', 0)} | {counts.get('C_derived_only', 0)} | "
            f"{counts.get('D_no_direct_disclosure_found', 0)} | {counts.get('E_technical_failure', 0)} | "
            f"{counts.get('skipped_fiscal_period_mismatch', 0)} |"
        )
    year_rows = []
    years = sorted({row.get("period_end_year") for row in missing if row.get("period_end_year")})
    for year in years:
        group = [row for row in missing if row.get("period_end_year") == year]
        counts = _status_counts(group)
        year_rows.append(
            f"| {year} | {len(group)} | {counts.get('A_verified_direct_q4_eps', 0)} | "
            f"{counts.get('B_candidate_needs_verification', 0)} | {counts.get('C_derived_only', 0)} | "
            f"{counts.get('D_no_direct_disclosure_found', 0)} | {counts.get('E_technical_failure', 0)} | "
            f"{counts.get('skipped_fiscal_period_mismatch', 0)} |"
        )
    mismatches = extraction.get("skipped_fiscal_period_mismatches", [])
    mismatch_rows = [
        f"| {row.get('sample_role')} | {row.get('symbol')} | "
        f"{row.get('classification_class') or row.get('class') or 'control'} | "
        f"FY{row.get('fy')} | {row.get('local_q4_end')} | {row.get('same_fy_sec_10k_report_end')} |"
        for row in mismatches
    ]
    direct_rows = []
    for row in accepted_missing:
        accepted = row.get("accepted") or {}
        evidence = (
            f"GAAP: {accepted.get('gaap_evidence')}; diluted={accepted.get('diluted')} "
            f"(row label); period: {accepted.get('fiscal_quarter')}; "
            f"{accepted.get('consolidated_scope_evidence')}; {accepted.get('share_class_evidence')}"
        )
        direct_rows.append(
            f"| {row.get('symbol')} | FY{row.get('fy')} / {row.get('q4_end')} | "
            f"{accepted.get('value_as_filed')} | {accepted.get('source')} | "
            f"[{accepted.get('accession')}]({accepted.get('source_url')}) | "
            f"{accepted.get('filing_date')} | {accepted.get('row_text', '').replace('|', '\\|')} | {evidence} | "
            f"{(accepted.get('split_basis') or {}).get('value_basis')} |"
        )
    unique_missing_by_source = {
        source: sum(row.get("status") == "A_verified_direct_q4_eps"
                    and row.get("accepted_source") == source for row in missing)
        for source in ("8-K/8-K-A", "10-K/10-K-A")
    }
    control_source_counts = {
        source: sum(row.get("status") == "A_verified_direct_q4_eps"
                    and (row.get("accepted") or {}).get("source") == source for row in controls)
        for source in ("8-K/8-K-A", "10-K/10-K-A")
    }
    incorrect_controls = [row for row in comparison.get("rows", [])
                          if row.get("correct_within_0_005") is False]
    incorrect_rows = [
        f"| {row.get('symbol')} | FY{row.get('fy')} / {row.get('q4_end')} | "
        f"{_format_number(row.get('extracted_eps_as_filed'))} as filed; current-basis comparator "
        f"{_format_number(row.get('extracted_eps_current_basis'))} | "
        f"{_format_number(row.get('answer_set_eps_current_basis'))} | "
        f"{_format_number(row.get('absolute_difference_current_basis'))} | "
        f"{row.get('extraction_accession')} vs {row.get('answer_set_accession')} |"
        for row in incorrect_controls
    ]
    metrics = extraction.get("metrics") or {}
    missing_share = len(accepted_missing) / len(missing) if missing else 0.0
    yield_pass = missing_share >= 0.20
    control_pass = stats["acceptance_bar"]["passed"]
    content = [
        "# SEC Q4 diluted EPS stage-three evidence run", "",
        f"Frozen sample `{sample['sample_id']}` used new seed `{sample['selection_seed']}`. "
        f"Sample SHA-256 `{stage2.sha256_file(STAGE / 'sample.json')}`; extraction SHA-256 "
        f"`{stage2.sha256_file(STAGE / 'extraction.json')}`.",
        f"Sample freeze commit `{comparison.get('sample_commit')}`; extraction freeze commit "
        f"`{comparison.get('extraction_commit')}`; comparison base commit `{comparison.get('comparison_base_commit')}`.",
        f"Regression fix commit `{sample['regression_fix_commit']}`; main base `{sample['base_commit']}`.",
        f"One SEC extraction run: complete **{extraction.get('complete')}**, stop reason `{extraction.get('stopped_reason')}`.",
        f"The run completed its query plan, with status E (technical failure/incomplete) for "
        f"**{sum(row.get('status') == 'E_technical_failure' for row in missing)} missing periods** and "
        f"**{sum(row.get('status') == 'E_technical_failure' for row in controls)} controls**; a completed run does not mean every period was resolved.",
        "", "## Missing-period yield by classification", "",
        "| Main class | Sampled periods | A: verified direct | B: candidate | C: annual/non-quarter only | D: no direct found | E: incomplete | Skipped fiscal-end mismatch |",
        "|---|---:|---:|---:|---:|---:|---:|---:|", *class_rows,
        "", "## Missing-period yield by period-end year", "",
        "| Period-end year | Sampled periods | A: verified direct | B: candidate | C: annual/non-quarter only | D: no direct found | E: incomplete | Skipped fiscal-end mismatch |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|", *year_rows,
        "",
        f"Verified direct Q4 EPS: **{len(accepted_missing)}/{len(missing)} sampled missing periods ({missing_share:.1%})**; "
        f"yield bar ≥20%: **{'PASS' if yield_pass else 'FAIL'}**. The denominator includes periods skipped for a fiscal-end mismatch.",
        "", "### Verified missing-period value", "",
        "| Ticker | Fiscal period | EPS as filed | Source | SEC accession / URL | Filing date | Original row label | GAAP / diluted / period / scope evidence | Split basis |",
        "|---|---|---:|---|---|---|---|---|---|",
        *(direct_rows or ["| — | — | — | — | — | — | — | — | — |"]),
        "",
        f"Unique missing-period values by source: 8-K/8-K-A **{unique_missing_by_source['8-K/8-K-A']}**; "
        f"10-K/10-K-A **{unique_missing_by_source['10-K/10-K-A']}**. "
        f"Accepted control values by source: 8-K/8-K-A **{control_source_counts['8-K/8-K-A']}**; "
        f"10-K/10-K-A **{control_source_counts['10-K/10-K-A']}**.",
        "", "## Control accuracy and acceptance bar", "",
        f"Controls accepted: **{stats['accepted']}/60**; correct within ±0.005: **{stats['correct']}**; "
        f"incorrect: **{stats['incorrect']}**; sign errors: **{stats['sign_errors']}**; "
        f"unverifiable: **{stats['unverifiable']}**; accuracy among accepted: "
        f"`{stats['accuracy_among_accepted']}`.",
        f"Control bar (≥50 accepted, every accepted value correct, zero sign errors): **{'PASS' if control_pass else 'FAIL'}**.",
        "Controls were compared only after the extraction JSON and its SHA-256 were committed. No extraction rule was changed after the comparison.",
        "", "### Control mismatches", "",
        "| Ticker | Fiscal period | SEC value as filed / comparison basis | Answer-set value | Absolute difference | Source accessions |",
        "|---|---|---|---:|---:|---|",
        *(incorrect_rows or ["| — | — | — | — | — | — |"]),
        "",
        "CTSO is the sole mismatch on the frozen split-normalized comparison basis: its 2022 8-K value is -0.21 as filed; the recorded "
        "future split factor is 0.05, which the frozen comparator converts to -4.20, while the answer set has -0.25. The cause of that "
        "difference remains unresolved. It is counted as incorrect, with no sign error. The comparison logic was not changed after this result.",
        "", "## Fiscal-period mismatches skipped", "",
        f"Skipped **{len(mismatches)}** sampled periods because the local Q4 end differs from the same-FY SEC 10-K report end.",
        "", "| Sample role | Ticker | Class | FY | Local Q4 end | SEC 10-K report end |",
        "|---|---|---|---:|---|---|", *(mismatch_rows or ["| — | — | — | — | — | — |"]),
        "", "## SEC requests, bytes, and controls", "",
        f"Wire request attempts: **{metrics.get('network_request_attempts', 0)} / 12,000**; decoded network-response bytes: "
        f"**{metrics.get('decoded_bytes_received', 0)}**; raw wire bytes received: **{metrics.get('wire_bytes_received', 0)}**; "
        f"decoded cached-body bytes examined: **{metrics.get('decoded_cached_body_bytes_examined', 0)}**; "
        f"decoded response bytes examined total: **{metrics.get('decoded_response_body_bytes_examined', 0)}**; "
        f"cache hits: **{metrics.get('cache_hits', 0)}**; retries: **{metrics.get('retries', 0)}**.",
        f"Identified User-Agent: `{metrics.get('user_agent', 'not recorded')}`; request pacing cap: "
        f"{metrics.get('rate_limit_max_requests_per_second', 4)}/second; first 403 stop enabled. "
        "The 12,000 cap and the per-run ledger are specific to this stage-three run.",
        "", "The sample contains 60 unique missing-period companies (15 per main classification class), up to two periods each, "
        "and 60 controls from sorted-key index shards 4–7. Every ticker in the prior stage-two control sample was excluded.",
        "Financial Statement Data Sets ZIPs were not downloaded. No answer-set value was used during extraction. "
        "No subtraction of annual EPS minus Q1–Q3 was performed.",
        "", "## Final acceptance", "",
        f"Stage-three acceptance: **{'PASS' if control_pass and yield_pass else 'FAIL'}**. "
        "A failed bar is reported as observed; sample membership, extraction rules, and control thresholds were not changed after results.",
        "",
    ]
    path = STAGE / "report.md"
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-sample")
    freeze.add_argument("--seed", default=SEED)
    sub.add_parser("extract")
    sub.add_parser("freeze-extraction")
    sub.add_parser("compare-controls")
    sub.add_parser("render-report")
    args = parser.parse_args(argv)
    if args.command == "freeze-sample":
        sample = freeze_sample(seed=args.seed)
        print(json.dumps({"sample_sha256": stage2.sha256_file(STAGE / "sample.json"),
                          "freeze_sha256": sample["freeze_sha256"],
                          "missing_companies": len(sample["missing_companies"]),
                          "missing_periods": sample["selection_audit"]["missing_period_count"],
                          "controls": len(sample["controls"])}, indent=2))
    elif args.command == "extract":
        result = run_extraction()
        print(json.dumps({"complete": result["complete"], "stopped_reason": result["stopped_reason"],
                          "period_results": len(result["period_results"]), "metrics": result["metrics"]}, indent=2))
    elif args.command == "freeze-extraction":
        print(json.dumps(freeze_extraction(), indent=2))
    elif args.command == "compare-controls":
        print(json.dumps(compare_controls()["metrics"], indent=2))
    else:
        print(render_report())


if __name__ == "__main__":
    main()
