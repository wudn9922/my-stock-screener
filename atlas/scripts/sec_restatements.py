"""Opt-in material comparative restatements. No I/O and no acceptance-gate rules.

fy/fp describe the filing's focus, not the comparative's year; use actual duration dates.
Missing values in a restating filing retain their current value with a mixed-basis audit. An accession is deliberately a
conservative basis boundary: equal numbers in different filings do not prove a basis.
"""
import copy
import math
from datetime import date, timedelta

EPS = ("epsBasic", "epsDiluted")
PER_SHARE = frozenset((*EPS, "dilutedShares"))
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


INCOME_FIELDS = frozenset({
    "revenue", "costOfRevenue", "grossProfit", "operatingIncome", "pretaxIncome",
    "taxProvision", "netIncome", "interestExpense",
})


def material_fields(before, after):
    """Strictly >2%, measured against the current value; missing is not a restatement."""
    return sorted(field for field in INCOME_FIELDS
                  if before.get(field) is not None and after.get(field) is not None
                  and before[field] != after[field]
                  and (before[field] == 0 or (abs(after[field] - before[field]) > .02 * abs(before[field])
                       and not math.isclose(abs(after[field] - before[field]), .02 * abs(before[field]), rel_tol=1e-12, abs_tol=1e-12))))


def share_basis_ratio(before, after):
    """Conservative split/ADR suspicion: <=1% relative error from an integer factor.

    This is a retention guard, not evidence to rescale monetary income. It works
    without a Yahoo split event (ADR ratio changes often have no such event).
    """
    for field in (*EPS, "dilutedShares"):
        old, new = before.get(field), after.get(field)
        if not old or not new or old * new <= 0:
            continue
        raw_ratio = new / old
        ratios = [(raw_ratio, 3 if field in EPS else 2)]
        if field in EPS and before.get("netIncome") and after.get("netIncome"):
            income_ratio = after["netIncome"] / before["netIncome"]
            if income_ratio > 0:
                ratios.append((raw_ratio / income_ratio, 2))
        for ratio, minimum in ratios:
            factor = max(ratio, 1 / ratio)
            integer = round(factor)
            if integer >= minimum and abs(factor / integer - 1) <= .01:
                return {"field": field, "ratio": ratio, "factor": integer if ratio > 1 else 1 / integer}
    return None


def _quarterly_issue(item):
    extra = item.setdefault("extra", {})
    issues = extra.setdefault("secRestatementIssues", [])
    if "quarterly-as-reported" not in issues:
        issues.append("quarterly-as-reported")
    extra[BLOCKED] = REASON


def complete_quarters(basis, rows, references):
    """Require three unique same-accession quarters, with all annual EPS inputs."""
    fields = [f for f in EPS if f in basis.get("reportedFields", EPS)]
    end = _day(basis.get("end"))
    if end is None:
        return False
    start = _day(basis.get("start")) or end - timedelta(days=370)
    relevant = [p for p in references if start <= _day(p["end"]) < end - timedelta(days=25)]
    if len(relevant) != 3:
        return False
    for old in relevant:
        matches = [p for p in rows if abs((_day(p["end"]) - _day(old["end"])).days) <= 25]
        if len(matches) != 1 or any(matches[0]["values"].get(f) is None for f in fields):
            return False
        marker = matches[0].get("extra", {}).get(BASIS)
        if marker is not None and (marker.get("accn") != basis.get("accn")
                or any(f in marker.get("retainedFields", []) for f in fields)):
            return False
    return True


def _merge_filed_row(old, filed, basis, revisions, frequency, original=None):
    audit_start = len(revisions)
    new = copy.deepcopy(old)
    provided = {field: value for field, value in filed["values"].items() if value is not None}
    share_guard = share_basis_ratio(old["values"], provided)
    if share_guard:
        provided = {field: value for field, value in provided.items() if field not in PER_SHARE}
    retained = sorted(field for field, value in old["values"].items()
                      if value is not None and field not in provided)
    new["values"].update(provided)
    if filed.get("extra", {}).get("derived"):
        new.setdefault("extra", {})["derived"] = 1
    new.setdefault("extra", {})[BASIS] = {**basis, "reportedFields": sorted(provided), "retainedFields": retained}
    issues = list(new["extra"].get("secRestatementIssues") or [])
    for code in (["restated", "mixed-basis"] if retained else ["restated"]):
        if code not in issues:
            issues.append(code)
    new["extra"]["secRestatementIssues"] = issues
    for field in retained:
        revisions.append({"kind": "basis-unrestated", "period": frequency, "end": old["end"],
                          "field": field, "old": old["values"][field], "new": old["values"][field],
                          "accn": basis["accn"], "filed": basis["filed"],
                          "originalAccn": basis.get("originalAccn"), "originalFiled": basis.get("originalFiled"),
                          "reason": ("split-or-adr-ratio-candidate; existing-share-basis-retained"
                                     if share_guard and field in PER_SHARE else
                                     "not-reported-in-restating-filing; current-value-retained")})
    for field, value in provided.items():
        before = (original or old)["values"].get(field)
        if before != value:
            revisions.append({"kind": "restated", "period": frequency, "end": old["end"],
                              "field": field, "old": before, "new": value,
                              "accn": basis["accn"], "filed": basis["filed"],
                              "originalAccn": basis.get("originalAccn"),
                              "originalFiled": basis.get("originalFiled")})
    if frequency == "annual" and basis.get("end") and basis["end"] != old["end"]:
        for entry in revisions[audit_start:]:
            entry["secEnd"] = basis["end"]
    return new


def select_rows(payload, annual, quarterly, extract, candidates, quarter_candidates=(), compare_current=False, current_document=None):
    """Switch only materially restated annuals; preserve all omitted current fields.

    Stored-document callers compare against current values. Extraction without a stored
    document compares first vs later filings, retaining legacy values for missing fields.
    """
    cache = {}
    def rows(filing):
        if filing["accn"] not in cache:
            extracted = extract(accession_payload(payload, filing["accn"]))
            # A document-wide adjusted flag does not prove that an individual
            # historical row was adjusted. Infer its existing units from the
            # row's shares/EPS and known split ratios, never change the document.
            split = (current_document or {}).get("split") or {}
            if split.get("adjusted") and split.get("events"):
                from build_financials import clean_value
                for frequency, items in enumerate(extracted):
                    references = annual if frequency == 0 else quarterly
                    window = 10 if frequency == 0 else 25
                    for item in items:
                        matches = [p for p in references if abs((_day(p["end"]) - _day(item["end"])).days) <= window]
                        if not matches:
                            continue
                        target = min(matches, key=lambda p: abs((_day(p["end"]) - _day(item["end"])).days))["values"]
                        values = item["values"]
                        ratios = [ratio for day, ratio in split["events"] if day > item["end"] and ratio > 0]
                        known = {1.0}
                        for start in range(len(ratios)):
                            product = 1.0
                            for ratio in ratios[start:]:
                                product *= ratio
                                known.update((product, 1 / product))
                        def compatible(ratio):
                            possible = [f for f in known if abs(ratio / f - 1) <= .02]
                            return min(possible, key=lambda f: abs(ratio / f - 1)) if possible else None
                        factor = None
                        if values.get("dilutedShares") and target.get("dilutedShares"):
                            factor = compatible(target["dilutedShares"] / values["dilutedShares"])
                        if factor is None and any(values.get(field) is not None and target.get(field) is not None
                                                  and abs(values[field] - target[field]) <= .02 * abs(target[field])
                                                  for field in EPS):
                            factor = 1.0
                        for field in EPS:
                            if factor is not None:
                                break
                            before, after = target.get(field), values.get(field)
                            if not before or not after or before * after <= 0:
                                continue
                            if target.get("netIncome") and values.get("netIncome"):
                                inferred = abs((target["netIncome"] / before) / (values["netIncome"] / after))
                                factor = compatible(inferred)
                            if factor is None:
                                factor = compatible(after / before)
                        item.setdefault("extra", {})["secShareBasisFactor"] = factor or 1.0
                        if factor is not None and factor != 1:
                            for field in EPS:
                                if values.get(field) is not None:
                                    values[field] = clean_value(values[field] / factor)
                            if values.get("dilutedShares") is not None:
                                values["dilutedShares"] = clean_value(values["dilutedShares"] * factor)
            cache[filing["accn"]] = extracted
        return cache[filing["accn"]]

    selected, revisions, years = [], [], []
    for old in annual:
        options = [f for f in candidates if abs((_day(old["end"]) - _day(f["end"])).days) <= 10]
        if not options:
            selected.append(copy.deepcopy(old))
            continue
        latest = max(options, key=lambda f: (f["filed"], f["accn"], f["end"], f["start"]))
        first = min(options, key=lambda f: (f["filed"], f["accn"], f["end"], f["start"]))
        filed = next((p for p in rows(latest)[0] if _near(p["end"], latest["end"])), None)
        original = old if compare_current else next((p for p in rows(first)[0]
                                                     if _near(p["end"], first["end"])), old)
        trigger = material_fields(original["values"], filed["values"]) if filed else []
        prior = old.get("extra", {}).get(BASIS) or {}
        later = (latest["filed"], latest["accn"]) > (first["filed"], first["accn"])
        if prior.get("filed"):
            later = (latest["filed"], latest["accn"]) > (prior["filed"], prior.get("accn") or "")
        if not later or not trigger:
            selected.append(copy.deepcopy(old))
            continue
        basis = {**latest, "restated": True, "switched": True, "triggerFields": trigger,
                 "originalAccn": first["accn"], "originalFiled": first["filed"],
                 "shareFactor": filed.get("extra", {}).get("secShareBasisFactor", 1.0)}
        selected.append(_merge_filed_row(old, filed, basis, revisions, "annual", original))
        same_filing = rows(latest)[1]
        basis["reportedFields"] = selected[-1]["extra"][BASIS]["reportedFields"]
        if not complete_quarters(basis, same_filing, quarterly):
            _quarterly_issue(selected[-1])
            same_filing = []
        years.append((basis, same_filing))

    quarters = []
    quarterly = list(quarterly)
    for basis, _ in years:
        if (not any(_near(p["end"], basis["end"]) for p in quarterly)
                and any(_day(basis["start"]) <= _day(p["end"]) < _day(basis["end"]) for p in quarterly)):
            quarterly.append({"end": basis["end"], "src": "sec", "values": {},
                              "extra": {"secKeepAsReported": True}})
    for old in sorted(quarterly, key=lambda p: p["end"]):
        year = next(((b, qs) for b, qs in years if
                     _day(b["start"]) <= _day(old["end"]) <= _day(b["end"]) + timedelta(days=10)), None)
        new = copy.deepcopy(old)
        if year:
            basis, same_filing = year
            matches = [p for p in same_filing if abs((_day(p["end"]) - _day(old["end"])).days) <= 25]
            replacement = min(matches, key=lambda p: abs((_day(p["end"]) - _day(old["end"])).days)) if matches else None
            if replacement is not None:
                replacement = copy.deepcopy(replacement)
                quarter_factor = replacement.get("extra", {}).get("secShareBasisFactor", 1.0)
                annual_factor = basis.get("shareFactor", 1.0)
                if quarter_factor != annual_factor:
                    from build_financials import clean_value
                    for field in EPS:
                        if replacement["values"].get(field) is not None:
                            replacement["values"][field] = clean_value(replacement["values"][field] * quarter_factor / annual_factor)
                    if replacement["values"].get("dilutedShares") is not None:
                        replacement["values"]["dilutedShares"] = clean_value(replacement["values"]["dilutedShares"] * annual_factor / quarter_factor)
                options = [f for f in quarter_candidates if _near(f["end"], old["end"])]
                original = old
                if options and not compare_current:
                    first = min(options, key=lambda f: (f["filed"], f["accn"], f["end"], f["start"]))
                    original = next((p for p in rows(first)[1] if _near(p["end"], old["end"])), old)
                    basis = {**basis, "originalAccn": first["accn"], "originalFiled": first["filed"]}
                new = _merge_filed_row(old, replacement, basis, revisions, "quarterly", original)
                new["extra"].update({BLOCKED: None, "secKeepAsReported": False})
            else:
                _quarterly_issue(new)
                new.setdefault("extra", {})[BASIS] = {"accn": None, "asReported": True,
                    "annualAccn": basis["accn"], "annualFiled": basis["filed"], "switchedYear": True}
            if abs((_day(old["end"]) - _day(basis["end"])).days) <= 25:
                for field in EPS:
                    if replacement is None or replacement["values"].get(field) is None:
                        _quarterly_issue(new)
        quarters.append(new)
    return selected, quarters, revisions


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
    return (bool(accn) and not any(field in basis.get("retainedFields", []) for field in EPS)
            and all(p.get("extra", {}).get(BASIS, {}).get("accn") == accn
                    and not any(field in p.get("extra", {}).get(BASIS, {}).get("retainedFields", []) for field in EPS)
                    for p in pieces))


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
    if any(r.get("kind") == "basis-unrestated" for r in revisions) and "mixed-basis" not in grade["issues"]:
        grade["issues"].append("mixed-basis")
    if revisions and "restated" not in grade["issues"]:
        grade["issues"].append("restated")

def overlay_document(previous, annual, quarterly, revisions, now, factor=None):
    """Apply only switched rows in memory, preserving dates, labels and every other cell.

    No regular merge, split adjustment, label assignment or deduplication runs here.
    Existing values are never cleared; incomplete restated quarters remain as reported.
    """
    import build_financials as fin
    document = copy.deepcopy(previous)
    current = fin.read_sections(previous)
    candidates = {"annual": copy.deepcopy(annual), "quarterly": copy.deepcopy(quarterly)}
    revisions = copy.deepcopy(revisions)
    if factor not in (None, 1):
        for items in candidates.values():
            for item in items:
                for field in EPS:
                    if item["values"].get(field) is not None:
                        item["values"][field] = fin.clean_value(item["values"][field] * factor)
                if item["values"].get("dilutedShares") is not None:
                    item["values"]["dilutedShares"] = fin.clean_value(item["values"]["dilutedShares"] / factor)
    if factor not in (None, 1):
        for revision in revisions:
            multiplier = factor if revision.get("field") in EPS else (1 / factor if revision.get("field") == "dilutedShares" else 1)
            for key in ("old", "new"):
                if revision.get(key) is not None:
                    revision[key] *= multiplier
    switched, audit, derived_fills = {}, [], []
    for item in candidates["annual"]:
        basis = item.get("extra", {}).get(BASIS) or {}
        if not basis.get("restated"):
            continue
        matches = [p for p in current["annual"] if abs((_day(p["end"]) - _day(item["end"])).days) <= 10]
        old = min(matches, key=lambda p: abs((_day(p["end"]) - _day(item["end"])).days)) if matches else None
        provided = {field: value for field, value in item["values"].items()
                    if value is not None and field not in basis.get("retainedFields", [])}
        trigger = material_fields(old["values"], provided) if old else []
        if not trigger:
            continue
        basis = {**basis, "switched": True, "triggerFields": trigger}
        changed = _merge_filed_row(old, {"values": provided}, basis, audit, "annual")
        switched[old["end"]] = changed
    if not switched:
        return document

    # Fiscal labels are read verbatim. Only internal fallbacks are inferred for old
    # documents lacking labels; their stored columns are never regenerated.
    for frequency in ("annual", "quarterly"):
        block = previous.get(frequency) or {}
        by_end = {p["end"]: p for p in current[frequency]}
        for index, end in enumerate(block.get("end", [])):
            item = by_end[end]
            item["fy"] = (block.get("fy") or [None] * len(by_end))[index]
            item["fq"] = (block.get("fq") or [None] * len(by_end))[index]
    if any(p.get("fy") is None for rows in current.values() if isinstance(rows, list)
           for p in rows if isinstance(p, dict) and "end" in p):
        fin.assign_fiscal_labels(current["annual"], current["quarterly"], fallback_end_month=previous.get("fyEndMonth") or 12)
    changed_quarters, restated_quarter_ends = {}, set()
    for end, year in switched.items():
        original_year = next(p for p in current["annual"] if p["end"] == end)
        year["fy"], year["fq"] = original_year["fy"], original_year.get("fq")
        basis = year["extra"][BASIS]
        basis.setdefault("end", end)
        references = [p for p in current["quarterly"] if p.get("fy") == year["fy"]]
        same_rows = [p for p in candidates["quarterly"]
                     if p.get("extra", {}).get(BASIS, {}).get("accn") == basis.get("accn")]
        if not complete_quarters(basis, same_rows, references):
            _quarterly_issue(year)
            for old in references:
                new = copy.deepcopy(old)
                _quarterly_issue(new)
                new["extra"][BASIS] = {"accn": None, "asReported": True,
                    "annualAccn": basis.get("accn"), "annualFiled": basis.get("filed"), "switchedYear": True}
                changed_quarters[old["end"]] = new
            continue
        for old in current["quarterly"]:
            if old.get("fy") != year["fy"]:
                continue
            candidate = next((p for p in candidates["quarterly"] if p["end"] == old["end"]), None)
            candidate_basis = (candidate or {}).get("extra", {}).get(BASIS) or {}
            same = candidate_basis.get("accn") == basis.get("accn") and bool(basis.get("accn"))
            if same:
                restated_quarter_ends.add(old["end"])
                provided = {field: value for field, value in candidate["values"].items()
                            if value is not None and field not in candidate_basis.get("retainedFields", [])}
                new = _merge_filed_row(old, {"values": provided, "extra": candidate.get("extra", {})}, candidate_basis, audit, "quarterly")
            else:
                new = copy.deepcopy(old)
                new["extra"][BASIS] = {"accn": None, "asReported": True,
                    "annualAccn": basis.get("accn"), "annualFiled": basis.get("filed"), "switchedYear": True}
            new["fy"], new["fq"] = old["fy"], old["fq"]
            changed_quarters[old["end"]] = new
        pieces = [p for p in changed_quarters.values() if p.get("fy") == year["fy"]]
        for q4 in [p for p in pieces if p.get("fq") == 4]:
            q4_basis = q4["extra"].get(BASIS) or {}
            original_q4 = next(p for p in current["quarterly"] if p["end"] == q4["end"])
            for field in EPS:
                if q4_basis.get("accn") == basis.get("accn") and field in q4_basis.get("reportedFields", []):
                    continue
                inputs = [[p for p in pieces if p.get("fq") == number] for number in (1, 2, 3)]
                coherent = all(len(found) == 1 and found[0]["extra"].get(BASIS, {}).get("accn") == basis.get("accn")
                               and field in found[0]["extra"].get(BASIS, {}).get("reportedFields", []) for found in inputs)
                if not coherent:
                    _quarterly_issue(q4)
                elif field in basis.get("reportedFields", []):
                    # Attempt the unchanged numerical gate only with a complete restated
                    # basis. If it rejects the derivation, retain the existing EPS.
                    before = q4["values"].pop(field, None)
                    q4["extra"][BASIS] = {**basis, "retainedFields": []}
                    derived_fills.extend(fin.fill_q4_eps([year], pieces))
                    if q4["values"].get(field) is None and before is not None:
                        q4["values"][field] = before
                if original_q4["values"].get(field) != q4["values"].get(field):
                    audit.append({"kind": "restated", "period": "quarterly", "end": q4["end"], "field": field,
                                  "old": original_q4["values"].get(field), "new": q4["values"].get(field),
                                  "accn": basis.get("accn"), "filed": basis.get("filed"),
                                  "originalAccn": basis.get("originalAccn"),
                                  "reason": (q4["extra"].get(BLOCKED) or REASON) if q4["values"].get(field) is None else "same-basis-Q4-derivation"})

    for frequency, changed in (("annual", switched), ("quarterly", changed_quarters)):
        block = document[frequency]
        for end, item in changed.items():
            index = block["end"].index(end)
            fields = set(item["values"]) | set(block.get("values", {}))
            for field in fields:
                value = item["values"].get(field)
                column = block.setdefault("values", {}).get(field)
                if column is None and value is not None:
                    column = block["values"][field] = [None] * len(block["end"])
                if column is not None:
                    if value is not None:
                        column[index] = value
            for name, value in item["extra"].items():
                if name not in {BASIS, BLOCKED, "secRestatementIssues", "derived"}:
                    continue
                column = block.setdefault(name, [None] * len(block["end"]))
                column[index] = copy.deepcopy(value)
    # Keep the earliest filed originals as well as the current pre-switch values.
    actual_ends = {"annual": set(switched), "quarterly": restated_quarter_ends}
    audit += [r for r in revisions if r.get("end") in actual_ends.get(r.get("period"), ())]
    preserve_quality(document, previous, audit, now.date().isoformat())
    if any("quarterly-as-reported" in item.get("extra", {}).get("secRestatementIssues", [])
           for item in switched.values()) and "quarterly-as-reported" not in document["quality"]["issues"]:
        document["quality"]["issues"].append("quarterly-as-reported")
    if derived_fills and "q4-derived" not in document["quality"]["issues"]:
        document["quality"]["issues"].append("q4-derived")
    return document
