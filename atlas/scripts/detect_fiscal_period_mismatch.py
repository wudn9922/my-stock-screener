"""Validate fiscal-period dry-run labels and Q4/annual end alignment, without network access."""
import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path

from build_financials import assign_fiscal_labels, effective
from repair_fiscal_periods import section_items


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "docs" / "report" / "fiscal-period-repair-dryrun.json"
DEFAULT_FINANCIALS_DIR = ROOT / "docs" / "report" / "financials"


def detect(report_path, financials_dir):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_by_symbol = {item["symbol"]: item for item in report.get("files", [])}
    result = {
        "usFiles": 0,
        "labelMismatchRows": 0,
        "q4EndAnnualEndMismatchRows": [],
        "filesWithoutFlagsWithLabelChanges": [],
        "reportedLabelDiffsWithoutMismatch": [],
    }
    for path in sorted(financials_dir.glob("*.json"), key=lambda item: item.name):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("market") != "US":
            continue
        result["usFiles"] += 1
        symbol = document.get("symbol") or path.stem
        reported = report_by_symbol.get(symbol, {})
        old_annual = section_items(document, "annual")
        old_quarterly = section_items(document, "quarterly")
        expected_annual = copy.deepcopy(old_annual)
        expected_quarterly = copy.deepcopy(old_quarterly)
        month = document.get("fyEndMonth")
        if not isinstance(month, int) or not 1 <= month <= 12:
            month = None
        assign_fiscal_labels(expected_annual, expected_quarterly, fallback_end_month=month)
        old_annual_by_end = {item["end"]: item for item in old_annual}
        old_quarterly_by_end = {item["end"]: item for item in old_quarterly}
        expected_annual_diffs = {
            item["end"]: item.get("fy") for item in expected_annual
            if old_annual_by_end[item["end"]].get("fy") != item.get("fy")
        }
        expected_quarterly_diffs = {
            item["end"]: (item.get("fy"), item.get("fq")) for item in expected_quarterly
            if (old_quarterly_by_end[item["end"]].get("fy"), old_quarterly_by_end[item["end"]].get("fq"))
            != (item.get("fy"), item.get("fq"))
        }
        reported_annual = {
            item["end"]: item["new"].get("fy")
            for item in reported.get("annualLabelChanges", [])
        }
        reported_quarterly = {
            item["end"]: (item["new"].get("fy"), item["new"].get("fq"))
            for item in reported.get("quarterlyLabelChanges", [])
        }
        result["labelMismatchRows"] += len(expected_annual_diffs) + len(expected_quarterly_diffs)
        if not expected_annual_diffs and not expected_quarterly_diffs and (
            reported_annual or reported_quarterly
        ):
            result["filesWithoutFlagsWithLabelChanges"].append(symbol)
        if expected_annual_diffs != reported_annual or expected_quarterly_diffs != reported_quarterly:
            result["reportedLabelDiffsWithoutMismatch"].append(symbol)

        new_labels = {item["end"]: (item.get("fy"), item.get("fq")) for item in old_quarterly}
        for item in reported.get("quarterlyLabelChanges", []):
            new_labels[item["end"]] = (item["new"].get("fy"), item["new"].get("fq"))
        annual_ends = defaultdict(set)
        for item in old_annual:
            annual_end = effective(item["end"])
            annual_ends[item.get("fy")].add((annual_end.year, annual_end.month))
        for item in old_quarterly:
            fy, fq = new_labels[item["end"]]
            quarter_end = effective(item["end"])
            effective_month = (quarter_end.year, quarter_end.month)
            if (fq == 4 and fy in annual_ends and effective_month not in annual_ends[fy]):
                result["q4EndAnnualEndMismatchRows"].append({
                    "symbol": symbol, "end": item["end"], "fy": fy,
                    "annualEnds": sorted(annual_ends[fy]),
                })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--financials-dir", type=Path, default=DEFAULT_FINANCIALS_DIR)
    args = parser.parse_args(argv)
    result = detect(args.report, args.financials_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if (result["q4EndAnnualEndMismatchRows"] or result["filesWithoutFlagsWithLabelChanges"]
            or result["reportedLabelDiffsWithoutMismatch"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
