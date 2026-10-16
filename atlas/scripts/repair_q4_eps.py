"""
Repair missing Q4 EPS in docs/report/financials/*.json. Default mode is offline method A; --sec fetches
official SEC 3-month EPS facts, validates them, and only then fills null values.

seed_financials_sec.py derives Q4 EPS = annual EPS − (Q1 + Q2 + Q3 EPS) on the basis the periods hold after
the split adjustment (build_financials.fill_q4_eps). Files seeded before that step keep a null Q4 EPS; this
script applies the same rule to the stored files (build_financials.repair_q4_document):

- only null values change: an existing EPS is never overwritten, and a filled period gets the derived flag
  (quarterly "derived") and "q4-derived" in quality.issues, as the seed writes them;
- idempotent: a second run finds nothing to fill;
- --dry-run lists the files and periods that would change and writes nothing.

The repaired data files are not committed by the change that adds this script.

Usage:
    python atlas/scripts/repair_q4_eps.py --dry-run
    python atlas/scripts/repair_q4_eps.py --symbol NFLX --verbose
    python atlas/scripts/repair_q4_eps.py --sec --budget 3000
    python atlas/scripts/repair_q4_eps.py --dir <copy of docs/report/financials>
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import date

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402
import seed_financials_sec as sec  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DIR = os.path.join(REPO_ROOT, "docs", "report", "financials")
DEFAULT_SEC_STATE = os.path.join(REPO_ROOT, ".cache", "q4-eps-sec-state.json")
EPS_CONCEPTS = {"epsDiluted": "EarningsPerShareDiluted", "epsBasic": "EarningsPerShareBasic"}
SEC_DURATION_DAYS = (80, 100)
SEC_END_TOLERANCE_DAYS = 5
SEC_Q4_MAX_RELATIVE_ERROR = 0.25


def repair_directory(directory, dry_run=False, symbols=None):
    """
    Repair every stored file of `directory` (index.json excluded). symbols: None or the symbols to limit to.
    → {"scanned": int, "changed": [(key, [(end, field, value)])], "skipped": Counter of reasons}.
    Files that cannot be read as a version 1 document are skipped as "corrupt", never rewritten.
    """
    wanted = None if symbols is None else {fin.financials_key(symbol) for symbol in symbols}
    summary = {"scanned": 0, "changed": [], "skipped": Counter()}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name == "index.json":
            continue
        key = name[: -len(".json")]
        if wanted is not None and key not in wanted:
            continue
        summary["scanned"] += 1
        path = os.path.join(directory, name)
        document = read_json(path)
        try:
            fin.read_sections(document)
        except ValueError:
            summary["skipped"]["corrupt"] += 1
            continue
        if document.get("market") != "US":
            summary["skipped"]["not-us"] += 1
            continue
        filled = fin.repair_q4_document(document)
        if not filled:
            continue
        ends = document["quarterly"]["end"]
        values = [
            (end, field, document["quarterly"]["values"][field][ends.index(end)])
            for end, field in filled
        ]
        summary["changed"].append((key, values))
        if not dry_run:
            write_atomic(path, document)
    return summary


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def extract_q4_eps_facts(companyfacts, period_ends):
    """Return valid USD/share 80–100-day EPS facts near each requested fiscal Q4 end."""
    us_gaap = ((companyfacts or {}).get("facts") or {}).get("us-gaap") or {}
    target_dates = {end: _parse_date(end) for end in period_ends}
    result = {end: {field: [] for field in EPS_CONCEPTS} for end in period_ends}
    for field, concept in EPS_CONCEPTS.items():
        concept_facts = us_gaap.get(concept) or {}
        units = concept_facts.get("units") or {}
        for unit, facts in units.items():
            if str(unit).replace(" ", "").lower() != "usd/shares":
                continue
            for fact in facts if isinstance(facts, list) else []:
                start, end = _parse_date(fact.get("start")), _parse_date(fact.get("end"))
                amount = _number(fact.get("val"))
                if start is None or end is None or amount is None:
                    continue
                duration = (end - start).days
                if not SEC_DURATION_DAYS[0] <= duration <= SEC_DURATION_DAYS[1]:
                    continue
                for target_end, target_date in target_dates.items():
                    if target_date is None or abs((end - target_date).days) > SEC_END_TOLERANCE_DAYS:
                        continue
                    result[target_end][field].append({
                        "concept": concept,
                        "unit": "USD/shares",
                        "value": amount,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "durationDays": duration,
                        "form": fact.get("form"),
                        "filed": fact.get("filed"),
                        "accn": fact.get("accn"),
                    })
    return result


def best_q4_fact(facts, target_end):
    target = _parse_date(target_end)
    if target is None:
        return None

    def rank(fact):
        end = _parse_date(fact.get("end"))
        end_distance = abs((end - target).days) if end else 999
        form = str(fact.get("form") or "").upper()
        filed = str(fact.get("filed") or "")
        filed_number = int(filed.replace("-", "")) if filed.replace("-", "").isdigit() else 0
        return (end_distance, abs(int(fact.get("durationDays") or 0) - 90),
                0 if form.startswith("10-K") else 1, -filed_number)

    return min(facts, key=rank) if facts else None


def normalize_and_check_q4_eps(document, end, q4_income, q3_shares, fact):
    """Put a SEC fact on the stored split basis using seed's suffix-factor logic, then validate it."""
    income, shares, raw = _number(q4_income), _number(q3_shares), _number((fact or {}).get("value"))
    if income is None:
        return None, 1.0, "missing-q4-net-income"
    if shares is None or shares <= 0:
        return None, 1.0, "missing-q3-shares"
    if raw is None:
        return None, 1.0, "invalid-sec-value"
    if income == 0:
        return None, 1.0, "zero-q4-net-income"
    if raw == 0 or (raw > 0) != (income > 0):
        return None, 1.0, "sign-mismatch"
    expected = income / shares
    if expected == 0:
        return None, 1.0, "zero-eps-or-implied-eps"

    events = []
    for event in ((document.get("split") or {}).get("events") or []):
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            continue
        split_day, ratio = _parse_date(event[0]), _number(event[1])
        if split_day is None or ratio is None or ratio <= 0:
            continue
        events.append((split_day.isoformat(), ratio))
    usable = sorted((day, ratio) for day, ratio in events
                    if ratio >= sec.MIN_SPLIT_RATIO or ratio <= 1 / sec.MIN_SPLIT_RATIO)
    factor = sec._suffix_factor(usable, end, raw / expected)
    adjusted = raw / factor
    if (adjusted > 0) != (income > 0):
        return None, factor, "sign-mismatch"
    relative_error = abs(adjusted - expected) / abs(expected)
    if relative_error > SEC_Q4_MAX_RELATIVE_ERROR:
        return None, factor, "outside-q4-income-q3-shares-25pct"
    return adjusted, factor, None


def _q4_rows(document):
    quarterly = document.get("quarterly") or {}
    rows = []
    fys = quarterly.get("fy") or []
    fqs = quarterly.get("fq") or []
    ends = quarterly.get("end") or []
    values = quarterly.get("values") or {}
    for index, (fy, fq, end) in enumerate(zip(fys, fqs, ends)):
        if fq != 4:
            continue
        current = {field: _column_value(values, field, index) for field in EPS_CONCEPTS}
        rows.append({"index": index, "fy": fy, "end": end, "values": current})
    return rows


def _column_value(values, field, index):
    column = values.get(field) if isinstance(values, dict) else None
    return column[index] if isinstance(column, list) and index < len(column) else None


def _q3_diluted_shares(document, fy):
    quarterly = document.get("quarterly") or {}
    matches = [index for index, (row_fy, fq) in enumerate(zip(quarterly.get("fy") or [],
                                                              quarterly.get("fq") or []))
               if row_fy == fy and fq == 3]
    if len(matches) != 1:
        return None
    return _column_value(quarterly.get("values") or {}, "dilutedShares", matches[0])


def _signature(rows):
    body = json.dumps([(row["end"], row["fy"]) for row in rows], separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _read_state(path):
    state = read_json(path)
    if not isinstance(state, dict) or state.get("version") != 1:
        return {"version": 1, "tickerMap": None, "facts": {}, "requests": 0}
    state.setdefault("facts", {})
    state.setdefault("requests", 0)
    return state


def _save_state(path, state):
    write_atomic(path, state)


def _sec_targets(directory):
    """Companies with at least one missing Q4 EPS value; retain all Q4 rows for validation."""
    targets = {}
    counts = Counter()
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name == "index.json":
            continue
        key = name[:-5]
        path = os.path.join(directory, name)
        document = read_json(path)
        try:
            fin.read_sections(document)
        except (TypeError, ValueError):
            counts["corrupt"] += 1
            continue
        if document.get("market") != "US":
            continue
        rows = _q4_rows(document)
        missing = [row for row in rows if any(row["values"].get(field) is None for field in EPS_CONCEPTS)]
        if not missing:
            continue
        targets[key] = {"path": path, "rows": rows, "signature": _signature(rows)}
        counts["companies"] += 1
        counts["missing_periods"] += len(missing)
        counts["missing_fields"] += sum(sum(row["values"].get(field) is None for field in EPS_CONCEPTS)
                                         for row in missing)
    return targets, counts


def _fact_matches(state, symbol, signature, end, field):
    company = (state.get("facts") or {}).get(symbol) or {}
    if company.get("signature") != signature or company.get("status") != "ok":
        return []
    return ((company.get("q4") or {}).get(end) or {}).get(field) or []


def sec_repair_directory(directory, *, state_path=DEFAULT_SEC_STATE, budget=None, client=None,
                         environ=None, clock=None):
    """Fetch, validate, then fill null Q4 EPS; cached matches make --budget runs resumable."""
    environ = os.environ if environ is None else environ
    clock = time.monotonic if clock is None else clock
    started = clock()
    targets, target_counts = _sec_targets(directory)
    state = _read_state(state_path)
    summary = {
        "status": "done", "targets": target_counts, "requests": 0, "companiesFetched": 0,
        "factsFetched": 0, "fills": [], "rejected": Counter(), "validationErrors": [],
        "validationCompared": 0, "validationMedian": None, "noFact": Counter(),
    }
    if not targets:
        print("No companies with missing Q4 EPS.")
        return summary

    reason = sec.refusal_reason(environ)
    if reason:
        summary["status"] = "refused"
        print(f"Refusing to run: {reason}")
        return summary
    try:
        user_agent = sec.resolve_user_agent(environ)
    except sec.UserAgentError as error:
        summary["status"] = "refused"
        print(f"Refusing to run: {error}")
        return summary
    client = client or sec.SecClient(user_agent, stop_on_forbidden=True)

    def save_request_count(before):
        delta = client.requests - before
        state["requests"] = int(state.get("requests", 0)) + delta
        summary["requests"] = client.requests
        _save_state(state_path, state)

    def budget_expired():
        return budget is not None and clock() - started >= budget

    if not isinstance(state.get("tickerMap"), dict):
        if budget_expired():
            summary["status"] = "paused"
            print("PAUSED before SEC ticker map; rerun the same command to resume.")
            return summary
        before = client.requests
        try:
            state["tickerMap"] = client.ticker_map()
        except sec.SecForbidden as error:
            save_request_count(before)
            summary["status"] = "stopped-403"
            print(f"STOPPED: {error}")
            return summary
        except (sec.FetchError, sec.TooManyFailures, OSError) as error:
            save_request_count(before)
            summary["status"] = "stopped-fetch-error"
            print(f"STOPPED: {error}")
            return summary
        save_request_count(before)

    ticker_map = state.get("tickerMap") or {}
    facts_cache = state.setdefault("facts", {})
    pending = [symbol for symbol, item in targets.items()
               if (facts_cache.get(symbol) or {}).get("signature") != item["signature"]]
    for done, symbol in enumerate(pending, start=1):
        if budget_expired():
            _save_state(state_path, state)
            summary["status"] = "paused"
            summary["companiesFetched"] = done - 1
            print(f"PAUSED: {done - 1}/{len(pending)} new companyfacts responses cached; "
                  "rerun the same command to resume.")
            break
        item = targets[symbol]
        cik = ticker_map.get(symbol)
        if not cik:
            facts_cache[symbol] = {"signature": item["signature"], "status": "no-cik", "q4": {}}
            _save_state(state_path, state)
            continue
        before = client.requests
        try:
            payload = client.company_facts(cik)
        except sec.SecForbidden as error:
            save_request_count(before)
            summary["status"] = "stopped-403"
            print(f"STOPPED: {error}")
            return summary
        except (sec.FetchError, sec.TooManyFailures, OSError) as error:
            save_request_count(before)
            summary["status"] = "stopped-fetch-error"
            print(f"STOPPED: {error}")
            return summary
        save_request_count(before)
        if payload is None:
            facts_cache[symbol] = {"signature": item["signature"], "status": "no-facts", "q4": {}}
        else:
            q4 = extract_q4_eps_facts(payload, [row["end"] for row in item["rows"]])
            facts_cache[symbol] = {"signature": item["signature"], "status": "ok", "q4": q4}
            summary["factsFetched"] += 1
        summary["companiesFetched"] = done
        if done % 50 == 0:
            print(f"SEC companyfacts cached {done}/{len(pending)} this run", flush=True)
    if summary["status"] == "paused":
        return summary

    validation_errors = []
    fills = []
    for symbol, item in targets.items():
        document = read_json(item["path"])
        q4_rows = item["rows"]
        quarterly = document.get("quarterly") or {}
        for row in q4_rows:
            income = _column_value(quarterly.get("values") or {}, "netIncome", row["index"])
            shares = _q3_diluted_shares(document, row["fy"])
            for field in EPS_CONCEPTS:
                fact = best_q4_fact(_fact_matches(state, symbol, item["signature"], row["end"], field), row["end"])
                if fact is None:
                    if row["values"].get(field) is None:
                        summary["noFact"][field] += 1
                    continue
                adjusted, factor, rejection = normalize_and_check_q4_eps(document, row["end"], income, shares, fact)
                if rejection:
                    summary["rejected"][rejection] += 1
                    continue
                stored_eps = row["values"].get(field)
                if stored_eps is not None:
                    actual = _number(stored_eps)
                    if actual is None:
                        summary["rejected"]["invalid-existing-eps-for-validation"] += 1
                        continue
                    if actual == 0:
                        summary["rejected"]["zero-existing-eps-for-validation"] += 1
                        continue
                    validation_errors.append(abs(adjusted - actual) / abs(actual))
                    continue
                fills.append({
                    "symbol": symbol, "path": item["path"], "index": row["index"], "end": row["end"],
                    "field": field, "value": round(adjusted, 4), "factor": factor, "fact": fact,
                })

    summary["validationErrors"] = validation_errors
    summary["validationCompared"] = len(validation_errors)
    if validation_errors:
        summary["validationMedian"] = sorted(validation_errors)[(len(validation_errors) - 1) // 2]
        if len(validation_errors) % 2 == 0:
            ordered = sorted(validation_errors)
            middle = len(ordered) // 2
            summary["validationMedian"] = (ordered[middle - 1] + ordered[middle]) / 2
    else:
        summary["validationMedian"] = None
    if not validation_errors or summary["validationMedian"] > 0.01:
        summary["status"] = "validation-failed"
        median_text = "N/A" if summary["validationMedian"] is None else f"{summary['validationMedian'] * 100:.2f}%"
        print(f"STOPPED: SEC Q4 validation median error {median_text} over {len(validation_errors)} comparisons; "
              "required <= 1.00%; no values written.")
        return summary

    filled_periods = set()
    written_files = set()
    for fill in fills:
        document = read_json(fill["path"])
        quarterly = document["quarterly"]
        column = quarterly.setdefault("values", {}).setdefault(fill["field"], [None] * len(quarterly["end"]))
        if column[fill["index"]] is not None:
            continue
        column[fill["index"]] = fill["value"]
        trace = document.setdefault("secQ4EpsFacts", {}).setdefault(fill["end"], {})
        trace[fill["field"]] = {
            "concept": fill["fact"]["concept"], "unit": fill["fact"]["unit"],
            "start": fill["fact"]["start"], "end": fill["fact"]["end"],
            "durationDays": fill["fact"]["durationDays"], "form": fill["fact"].get("form"),
            "filed": fill["fact"].get("filed"), "accession": fill["fact"].get("accn"),
            "reportedValue": fill["fact"]["value"], "splitFactor": fill["factor"],
            "value": fill["value"],
        }
        write_atomic(fill["path"], document)
        filled_periods.add((fill["symbol"], fill["end"]))
        written_files.add(fill["symbol"])
    summary["fills"] = fills
    summary["filledPeriods"] = len(filled_periods)
    summary["writtenFiles"] = len(written_files)
    summary["status"] = "done"
    print(f"SEC Q4 validation: median error {summary['validationMedian'] * 100:.2f}% "
          f"({len(validation_errors)} comparisons; threshold <= 1.00%).")
    print(f"SEC Q4 values filled: {len(fills)} EPS values across {len(filled_periods)} quarters "
          f"and {len(written_files)} files; validation passed.")
    if summary["noFact"]:
        print("No matching SEC 3-month fact for still-null fields: " +
              ", ".join(f"{field} {count}" for field, count in sorted(summary["noFact"].items())))
    if summary["rejected"]:
        print("SEC facts rejected by Q4 checks: " +
              ", ".join(f"{reason} {count}" for reason, count in sorted(summary["rejected"].items())))
    print(f"SEC requests cumulative: {state.get('requests', 0)}")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fill null Q4 EPS offline or from validated SEC facts.")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="directory of <KEY>.json files")
    parser.add_argument("--dry-run", action="store_true", help="list what would change, write nothing")
    parser.add_argument("--symbol", action="append", help="limit to this symbol (repeatable)")
    parser.add_argument("--verbose", action="store_true", help="print every filled period with its value")
    parser.add_argument("--sec", action="store_true", help="fetch official SEC Q4 3-month EPS facts first")
    parser.add_argument("--budget", type=int, help="SEC request budget in seconds; rerun same command to resume")
    parser.add_argument("--state", default=DEFAULT_SEC_STATE, help=f"resumable SEC cache (default {DEFAULT_SEC_STATE})")
    args = parser.parse_args(argv)
    if args.budget is not None and args.budget <= 0:
        parser.error("--budget must be a positive number of seconds")
    if args.sec:
        summary = sec_repair_directory(args.dir, state_path=args.state, budget=args.budget)
        return 1 if summary["status"] in ("stopped-403", "stopped-fetch-error", "validation-failed") else 0
    if args.budget is not None or args.state != DEFAULT_SEC_STATE:
        parser.error("--budget and --state require --sec")
    summary = repair_directory(args.dir, dry_run=args.dry_run, symbols=args.symbol)
    changed = summary["changed"]
    fields = Counter(field for _, values in changed for _, field, _ in values)
    if args.verbose or args.symbol:
        for key, values in changed:
            for end, field, value in values:
                print(f"{key} {end} {field} = {value}")
    print(f"scanned {summary['scanned']} files")
    print(f"files to repair: {len(changed)}")
    print(f"periods filled: {sum(len(values) for _, values in changed)} "
          f"(epsDiluted: {fields['epsDiluted']}, epsBasic: {fields['epsBasic']})")
    if summary["skipped"]:
        print("skipped: " + ", ".join(f"{reason} {count}" for reason, count in sorted(summary["skipped"].items())))
    if args.dry_run:
        print("dry run: nothing written")
    else:
        print(f"files written: {len(changed)}" if changed else "nothing to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
