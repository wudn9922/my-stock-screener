"""Opt-in, accession-coherent comparative rows. No I/O and no acceptance-gate rules.

fy/fp describe the filing's focus, not the comparative's year; use actual duration dates.
Missing values in the selected filing stay missing. An accession is deliberately a
conservative basis boundary: equal numbers in different filings do not prove a basis.
"""
import copy
import math
from datetime import date, timedelta

EPS = ("epsBasic", "epsDiluted")
BASIS = "secFilingBasis"
BLOCKED = "secQ4EpsBlockedReason"
REASON = "annual-and-quarterly-filing-bases-differ; restated-quarterly-values-unavailable"


def _day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def accession_payload(payload, accn):
    """Keep every taxonomy/unit, but only facts from one accession (no cross-row fallback)."""
    result = {key: value for key, value in payload.items() if key != "facts"}
    result["facts"] = {
        namespace: {
            concept: {**block, "units": {
                unit: [fact for fact in facts if fact.get("accn") == accn]
                for unit, facts in block.get("units", {}).items()
            }} for concept, block in concepts.items()
        } for namespace, concepts in payload.get("facts", {}).items()
    }
    return result


def filings(payload, namespace, existence, forms, units, days=(350, 380)):
    """Reporting accessions, requiring a mapped existence fact in the correct unit."""
    result = []
    for concept in existence:
        for unit, facts in payload.get("facts", {}).get(namespace, {}).get(concept, {}).get("units", {}).items():
            if unit not in units:
                continue
            for fact in facts:
                start, end, filed = (_day(fact.get(key)) for key in ("start", "end", "filed"))
                if (start and end and filed and isinstance(fact.get("accn"), str) and fact["accn"]
                        and str(fact.get("form", "")).upper() in forms and fact.get("segment") is None
                        and days[0] <= (end - start).days <= days[1]
                        and isinstance(fact.get("val"), (int, float)) and not isinstance(fact["val"], bool)
                        and math.isfinite(fact["val"])):
                    result.append({"start": start.isoformat(), "end": end.isoformat(),
                                   "accn": fact["accn"], "filed": filed.isoformat()})
    return result


def _near(left, right):
    return abs((_day(left) - _day(right)).days) <= 5


def select_rows(payload, annual, quarterly, extract, candidates, quarter_candidates=()):
    """Return coherent annual/quarterly rows and original annual revisions.

    extract is the legacy pure extractor on a single-accession payload. Its normal
    concept, unit, plausibility and share-basis checks still apply inside that filing.
    """
    cache = {}

    def rows(filing):
        accn = filing["accn"]
        if accn not in cache:
            cache[accn] = extract(accession_payload(payload, accn))
        return cache[accn]

    selected, originals, years = [], [], []
    for old in annual:
        options = [f for f in candidates if _near(old["end"], f["end"])]
        if not options:
            selected.append(copy.deepcopy(old))
            continue
        latest = max(options, key=lambda f: (f["filed"], f["accn"], f["end"], f["start"]))
        first = min(options, key=lambda f: (f["filed"], f["accn"], f["end"], f["start"]))
        new = next((p for p in rows(latest)[0] if _near(p["end"], old["end"])), None)
        original = next((p for p in rows(first)[0] if _near(p["end"], old["end"])), old)
        # A filing can lack usable values after validation. Never borrow another filing's row.
        new = copy.deepcopy(new) if new is not None else {**copy.deepcopy(old), "values": {}}
        new["end"] = old["end"]
        changed = any(original["values"].get(field) is not None
                      and new["values"].get(field) is not None
                      and value != original["values"][field]
                      for field, value in new["values"].items())
        basis = {**latest, "restated": changed}
        new.setdefault("extra", {})[BASIS] = basis
        selected.append(new)
        years.append((basis, rows(latest)[1]))
        if new["values"] != original["values"]:
            for field in sorted(set(original["values"]) | set(new["values"])):
                before, after = original["values"].get(field), new["values"].get(field)
                if before != after:
                    originals.append({"kind": "restated", "period": "annual", "end": old["end"],
                                      "field": field, "old": before, "new": after,
                                      "accn": latest["accn"], "filed": latest["filed"],
                                      "originalAccn": first["accn"], "originalFiled": first["filed"]})
    quarters = []
    quarterly = list(quarterly)
    for basis, _ in years:
        if (basis["restated"] and not any(_near(p["end"], basis["end"]) for p in quarterly)
                and any(_day(basis["start"]) <= _day(p["end"]) < _day(basis["end"]) for p in quarterly)):
            quarterly.append({"end": basis["end"], "src": "sec", "values": {},
                              "extra": {"secKeepAsReported": True}})
    for old in sorted(quarterly, key=lambda p: p["end"]):
        year = next(((b, qs) for b, qs in years
                     if _day(b["start"]) <= _day(old["end"]) <= _day(b["end"]) + timedelta(days=5)), None)
        new = copy.deepcopy(old)
        if year is not None:
            basis, same_filing = year
            replacement = next((p for p in same_filing if _near(p["end"], old["end"])), None)
            if replacement is not None:
                new = copy.deepcopy(replacement)
                new["end"] = old["end"]
                new.setdefault("extra", {})[BASIS] = dict(basis)
                new["extra"][BLOCKED] = None
                new["extra"]["secKeepAsReported"] = False
                options = [f for f in quarter_candidates if _near(f["end"], old["end"])]
                if options:
                    first = min(options, key=lambda f: (f["filed"], f["accn"], f["end"], f["start"]))
                    original = next((p for p in rows(first)[1] if _near(p["end"], old["end"])), None)
                    if original is not None:
                        for field in sorted(set(original["values"]) | set(new["values"])):
                            before, after = original["values"].get(field), new["values"].get(field)
                            if before != after:
                                originals.append({"kind": "restated", "period": "quarterly", "end": old["end"],
                                                  "field": field, "old": before, "new": after,
                                                  "accn": basis["accn"], "filed": basis["filed"],
                                                  "originalAccn": first["accn"], "originalFiled": first["filed"]})
            else:
                new.setdefault("extra", {})[BASIS] = {
                    "accn": None, "asReported": True,
                    "annualAccn": basis["accn"], "annualFiled": basis["filed"],
                }
            if basis["restated"] and _near(old["end"], basis["end"]) and replacement is None:
                # Legacy extraction may already have derived Q4. Keep directly reported EPS
                # only; an aggregate derived flag cannot establish per-field provenance.
                for field in EPS:
                    if not _direct_eps(payload, old["end"], field, new["values"].get(field)):
                        new["values"].pop(field, None)
                new["extra"][BLOCKED] = REASON
        quarters.append(new)
    return selected, quarters, originals


def _direct_eps(payload, end, field, value):
    if value is None:
        return False
    names = {"epsBasic": ("EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted", "BasicEarningsLossPerShare"),
             "epsDiluted": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "DilutedEarningsLossPerShare")}
    for namespace in ("us-gaap", "ifrs-full"):
        for name in names[field]:
            units = payload.get("facts", {}).get(namespace, {}).get(name, {}).get("units", {})
            for unit, facts in units.items():
                if not unit.endswith("/shares"):
                    continue
                for fact in facts:
                    start, finish = _day(fact.get("start")), _day(fact.get("end"))
                    if (start and finish and 80 <= (finish-start).days <= 120
                            and _near(end, fact["end"]) and fact.get("val") == value
                            and fact.get("form") in {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "6-K", "6-K/A"}):
                        return True
    return False


def same_basis(year, pieces):
    """Legacy rows have no marker: default-off behavior is exactly unchanged."""
    basis = year.get("extra", {}).get(BASIS)
    if basis is None or not basis.get("restated"):
        return True
    accn = basis.get("accn")
    return bool(accn) and all(p.get("extra", {}).get(BASIS, {}).get("accn") == accn for p in pieces)


def preserve_quality(document, previous, revisions, seen):
    """Append audit records and flags, retaining every existing flag and original value."""
    grade = document.setdefault("quality", {})
    old_quality = (previous or {}).get("quality") or {}
    grade["issues"] = list(old_quality.get("issues") or []) + [
        code for code in grade.get("issues", []) if code not in (old_quality.get("issues") or [])]
    audit = copy.deepcopy(old_quality.get("revisions") or [])
    for entry in list(grade.get("revisions") or []) + [{**r, "seen": seen} for r in revisions]:
        identity = {k: v for k, v in entry.items() if k != "seen"}
        if not any({k: v for k, v in r.items() if k != "seen"} == identity for r in audit):
            audit.append(entry)
    if audit:
        grade["revisions"] = audit
    if revisions and "restated" not in grade["issues"]:
        grade["issues"].append("restated")
