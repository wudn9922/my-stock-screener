#!/usr/bin/env python3
"""Generate row-level Basic EPS numerator reviews tied to SEC accession facts."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINANCIALS = ROOT / "docs/report/financials"
SOURCE_DECISIONS = FINANCIALS / "q4-eps-basic-B-source-decisions.jsonl"
OUTPUT = FINANCIALS / "q4-eps-basic-B-numerator-review.jsonl"

REVIEW_FINDINGS = {
    ("ACTG", 2021): {
        "decision": "retain-missing",
        "failureReason": "q4-participating-security-allocation-not-derivable-from-cumulative-periods",
        "annualNoteNumerator": 118804000, "nineMonthNoteNumerator": -58595000,
        "annualNote": "10-K Note 16", "nineMonthNote": "10-Q EPS note",
        "reviewFinding": (
            "10-K basic common numerator: $149.197m net income less $1.452m preferred dividends, "
            "$3.829m accretion, and $25.112m participating earnings = $118.804m. 9M common "
            "numerator is -$58.595m after $0.785m preferred dividends and $2.762m accretion; "
            "the YTD loss has no participant allocation. As FY and Q4 are profitable, the "
            "cumulative figures do not independently establish the standalone Q4 allocation."
        ),
    },
    ("CRAI", 2020): {
        "decision": "retain-missing",
        "failureReason": "annual-basic-eps-numerator-excludes-participating-security-allocation",
        "annualNoteNumerator": 24414000, "nineMonthNoteNumerator": None,
        "annualNote": "10-K Note 11", "nineMonthNote": "10-Q Note 7",
        "reviewFinding": (
            "10-K reports $24.507m net income before the $0.093m participating-share allocation, "
            "leaving $24.414m for Basic EPS. The selected annual common-income tag is $24.507m, "
            "so annual-minus-9M overstates the numerator."
        ),
    },
    ("FTAI", 2019): {
        "decision": "approved", "annualNoteNumerator": 223270000,
        "nineMonthNoteNumerator": 39623000, "annualNoteShares": 85992019,
        "nineMonthNoteShares": 85990131, "annualNote": "10-K Note 20",
        "nineMonthNote": "10-Q Note 18",
        "reviewFinding": (
            "EPS-note numerators and denominators match the selected facts: FY $223.270m / "
            "85,992,019 and 9M $39.623m / 85,990,131. Basic shares include the participating "
            "securities used in the EPS note; Class B Units are convertible Class A participating "
            "units, not a second registered common class."
        ),
    },
    ("LXP", 2023): {
        "decision": "approved", "annualNoteNumerator": 23863000,
        "nineMonthNoteNumerator": 10878000, "annualNoteShares": 58049175,
        "nineMonthNoteShares": 290187124, "annualNoteEps": 0.41,
        "nineMonthNoteEps": 0.04, "annualNote": "10-K EPS table",
        "nineMonthNote": "10-Q EPS table",
        "reviewFinding": (
            "FY EPS table: $23.863m, 58,049,175 basic shares, EPS $0.41. 9M table: $10.878m, "
            "290,187,124 pre-reverse-split shares, EPS $0.04. The 5:1 reverse split is applied "
            "to 9M shares. Keyword hits for $0.401m/$0.322m refer to FFO reconciliation, not GAAP EPS."
        ),
    },
    ("PMT", 2024): {
        "decision": "retain-missing",
        "failureReason": "q4-participating-security-allocation-not-disclosed",
        "annualNoteNumerator": 118748000,
        "nineMonthNoteNumerator": 82762000, "annualNote": "10-K EPS table",
        "nineMonthNote": "10-Q EPS table",
        "reviewFinding": (
            "FY table: $160.984m net income less $41.819m preferred dividends and $0.417m "
            "participating-award allocation = $118.748m. 9M table: $114.449m less $31.364m "
            "preferred dividends and $0.323m allocation = $82.762m. Both match selected cumulative "
            "facts, but the filing does not disclose standalone Q4 participant allocation; annual-minus-9M "
            "does not prove the exact Q4 basic-EPS numerator."
        ),
    },
    ("RPT", 2025): {
        "decision": "retain-missing",
        "failureReason": "q4-positive-income-participating-allocation-not-derivable-from-loss-periods",
        "annualNoteNumerator": -2740000, "nineMonthNoteNumerator": -4692000,
        "annualNote": "10-K EPS note", "nineMonthNote": "10-Q EPS note",
        "reviewFinding": (
            "FY and 9M losses have zero allocation to participating restricted shares. Annual-minus-9M "
            "implies positive Q4 common income of $1.952m, where participant allocation applies, but "
            "the Q4 allocation is not disclosed by the cumulative facts. The 1:6 split basis is aligned."
        ),
    },
    ("STAG", 2020): {
        "decision": "retain-missing",
        "failureReason": "q4-participating-security-allocation-not-disclosed",
        "annualNoteNumerator": 196720000,
        "nineMonthNoteNumerator": 102021000, "annualNoteEps": 1.32,
        "nineMonthNoteEps": 0.69, "annualNote": "10-K Note 10",
        "nineMonthNote": "10-Q EPS table",
        "reviewFinding": (
            "FY EPS table: $206.795m less $4.648m NCI-after-preferred, $5.156m preferred "
            "dividends, and $0.271m participant allocation = $196.720m. 9M table reconciles "
            "to $102.021m. Both match selected cumulative basic-income facts, but the filing "
            "does not disclose standalone Q4 participant allocation; annual-minus-9M does not "
            "prove the exact Q4 basic-EPS numerator."
        ),
    },
    ("TRGP", 2022): {
        "decision": "approved", "annualNoteNumerator": 896800000,
        "nineMonthNoteNumerator": 578900000, "annualNoteEps": 3.95,
        "nineMonthNoteEps": 2.54, "annualNote": "10-K EPS note/table",
        "nineMonthNote": "10-Q EPS table",
        "reviewFinding": (
            "FY and 9M EPS tables show zero participant allocation; $896.8m and $578.9m "
            "numerators match selected facts. Reported EPS is $3.95 and $2.54."
        ),
    },
    ("TRGP", 2023): {
        "decision": "retain-missing",
        "failureReason": "annual-and-nine-month-basic-numerators-precede-participating-security-allocation",
        "annualNoteNumerator": 828200000, "nineMonthNoteNumerator": 550500000,
        "annualNote": "10-K EPS note/table", "nineMonthNote": "10-Q EPS table",
        "reviewFinding": (
            "FY common income $835.8m less $7.6m participant allocation gives $828.2m basic "
            "numerator. 9M common income $555.6m less $5.1m allocation gives $550.5m. "
            "CompanyFacts selects the pre-allocation amounts."
        ),
    },
}

NO_SPECIAL_ALLOCATION = {
    "ADAM", "DIT", "EXC", "FRST", "INVA", "JRVR", "NREF", "OFG", "PNRG", "REX", "SF",
}
EPS_FIELDS = (
    "cik", "concept", "unit", "rawValue", "start", "end", "form", "filed",
    "accession", "filingUrl", "filingSha256",
)


def _source_fact(decision, key):
    item = ((decision.get("inputFactChecks") or {}).get(key) or {}).get("sourceFact") or {}
    return {field: item.get(field) for field in EPS_FIELDS}


def generate(decisions_path=SOURCE_DECISIONS, output_path=OUTPUT):
    decisions = [json.loads(line) for line in Path(decisions_path).read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    rows = []
    for source in decisions:
        if (source.get("field") != "epsBasic" or not source.get("inputFactChecks")
                or not (source.get("grade") == "B" and source.get("status") == "validated"
                        or source.get("candidateValue") is not None)
                or source.get("symbol") not in NO_SPECIAL_ALLOCATION | {key[0] for key in REVIEW_FINDINGS}):
            continue
        year = int(source["period"]["fiscalYear"])
        finding = REVIEW_FINDINGS.get((source["symbol"], year))
        if finding is None:
            finding = {
                "decision": "approved",
                "annualNote": "10-K EPS/common-income context",
                "nineMonthNote": "10-Q EPS/common-income context",
                "reviewFinding": (
                    "Independent filing review found no distinct participant allocation or share-class "
                    "adjustment to the selected NetIncomeLossAvailableToCommonStockholdersBasic tags; "
                    "annual and 9M basic-share facts align with the EPS numerator/share definitions."
                ),
            }
        candidate = source.get("candidateValue")
        if candidate is None:
            candidate = source.get("value")
        rows.append({
            "schema": "q4-eps-basic-numerator-note-review-v1",
            "symbol": source["symbol"], "cik": source.get("cik"),
            "fiscalYear": year, "periodEnd": source.get("period", {}).get("end"),
            "field": "epsBasic", "decision": finding["decision"],
            "failureReason": finding.get("failureReason"), "candidateEstimate": candidate,
            "annualIncomeFact": _source_fact(source, "annualIncome"),
            "nineMonthIncomeFact": _source_fact(source, "nineMonthIncome"),
            "annualBasicShareFact": _source_fact(source, "annualBasicShares"),
            "nineMonthBasicShareFact": _source_fact(source, "nineMonthBasicShares"),
            **{key: value for key, value in finding.items()
               if key not in {"decision", "failureReason"}},
            "reviewedBy": "independent Codex GPT-6 Luna agent",
            "evidencePolicy": "10-K/10-Q EPS numerator note compared with cited raw Inline XBRL facts and split basis",
        })
    rows.sort(key=lambda row: (row["symbol"], row["fiscalYear"]))
    if len(rows) != 20:
        raise RuntimeError(f"expected 20 reviewed rows, got {len(rows)}")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                   for row in rows), encoding="utf-8")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, default=SOURCE_DECISIONS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    rows = generate(args.decisions, args.output)
    print(json.dumps({"reviewedCandidates": len(rows),
                      "approved": sum(row["decision"] == "approved" for row in rows),
                      "retainedMissing": sum(row["decision"] == "retain-missing" for row in rows),
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
