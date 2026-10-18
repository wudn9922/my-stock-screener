#!/usr/bin/env python3
"""Compare frozen SEC extraction values to the 60 answer-set controls after extraction commit."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = ROOT / "docs/report/sec-q4-eps-stage2"
sys.path.insert(0, str(STAGE))
import stage2_poc as stage2  # noqa: E402


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


def compare_controls():
    extraction_path = STAGE / "extraction.json"
    extraction_sha_path = STAGE / "extraction.sha256"
    # These checks happen before loading the answer set. They prevent value comparison against an unfrozen run.
    extraction_commit = stage2.assert_committed(extraction_path.relative_to(ROOT))
    stage2.assert_committed(extraction_sha_path.relative_to(ROOT))
    extraction_sha = stage2.sha256_file(extraction_path)
    expected_sha = extraction_sha_path.read_text(encoding="ascii").split()[0]
    if extraction_sha != expected_sha:
        raise RuntimeError("committed extraction.sha256 does not match extraction.json")
    sample_path = STAGE / "sample.json"
    stage2.assert_committed(sample_path.relative_to(ROOT))
    sample = stage2.load_json(sample_path)
    extraction = stage2.load_json(extraction_path)
    answer_path = ROOT / "docs/report/q4-8k/answer-set.json"
    if stage2.sha256_file(answer_path) != sample["input_hashes"]["answer_set_identity_source"]["sha256"]:
        raise RuntimeError("answer set differs from the committed control-selection source")
    answer_set = stage2.load_json(answer_path)
    answers = {(row.get("key"), row.get("end")): row for row in answer_set.get("rows", [])}
    extracted = {(row.get("symbol"), row.get("q4_end"), row.get("fy")): row
                 for row in extraction.get("period_results", []) if row.get("sample_role") == "control"}
    rows = []
    accepted = correct = sign_errors = 0
    for control in sample["controls"]:
        key, end, fy = control["symbol"], control["q4_end"], control["fy"]
        answer = answers.get((key, end))
        if not answer or answer.get("fy") != fy:
            raise RuntimeError(f"control selection no longer maps to its answer-set row: {key} {fy} {end}")
        result = extracted.get((key, end, fy), {})
        accepted_value = ((result.get("accepted") or {}).get("value_as_filed")
                          if result.get("status") == "A_verified_direct_q4_eps" else None)
        filing_date = ((result.get("accepted") or {}).get("filing_date")
                       if result.get("status") == "A_verified_direct_q4_eps" else None)
        answer_filing_date = answer.get("filed")
        events = control.get("split_events") or []
        answer_factor = split_factor(events, answer_filing_date)
        extracted_factor = split_factor(events, filing_date)
        answer_as_filed = float(answer["value"]) * answer_factor
        extracted_current = float(accepted_value) / extracted_factor if accepted_value is not None and extracted_factor else None
        answer_current = float(answer["value"])
        comparable = accepted_value is not None and extracted_current is not None
        difference = abs(extracted_current - answer_current) if comparable else None
        is_correct = bool(comparable and difference <= 0.005)
        sign_error = bool(comparable and extracted_current * answer_current < 0)
        if comparable:
            accepted += 1
            correct += int(is_correct)
            sign_errors += int(sign_error)
        rows.append({
            "symbol": key,
            "fy": fy,
            "q4_end": end,
            "status": result.get("status", "missing_extraction_row"),
            "extraction_source": (result.get("accepted") or {}).get("source"),
            "extraction_accession": (result.get("accepted") or {}).get("accession"),
            "extraction_filing_date": filing_date,
            "extracted_eps_as_filed": accepted_value,
            "extracted_future_split_factor": extracted_factor,
            "extracted_eps_current_basis": extracted_current,
            "answer_set_accession": answer.get("accession"),
            "answer_set_filing_date": answer_filing_date,
            "answer_set_eps_current_basis": answer_current,
            "answer_set_eps_as_filed_estimate": answer_as_filed,
            "answer_future_split_factor": answer_factor,
            "absolute_difference_current_basis": difference,
            "correct_within_0_005": is_correct if comparable else None,
            "sign_error": sign_error if comparable else None,
        })
    metrics = {
        "controls_selected": len(rows),
        "accepted": accepted,
        "correct": correct,
        "incorrect": accepted - correct,
        "sign_errors": sign_errors,
        "unverifiable": len(rows) - accepted,
        "accuracy_among_accepted": round(correct / accepted, 6) if accepted else None,
        "accepted_share_of_controls": round(accepted / len(rows), 6) if rows else None,
        "tolerance": 0.005,
    }
    output = {
        "schema_version": 1,
        "comparison_created_after_extraction_commit": True,
        "sample_sha256": stage2.sha256_file(sample_path),
        "extraction_sha256": extraction_sha,
        "extraction_commit": extraction_commit,
        "answer_set_sha256": stage2.sha256_file(answer_path),
        "metrics": metrics,
        "rows": rows,
    }
    stage2.dump_json(STAGE / "controls-comparison.json", output)
    (STAGE / "controls-comparison.sha256").write_text(
        f"{stage2.sha256_file(STAGE / 'controls-comparison.json')}  controls-comparison.json\n", encoding="ascii"
    )
    return output


if __name__ == "__main__":
    result = compare_controls()
    print(json.dumps(result["metrics"], indent=2))
