"""Apply the reviewed #153 manifest offline, with an independent Git-tree verifier.

No SEC requests, ordinary builder merge, period alignment, split repair or Q4
acceptance runs here. Every candidate must match the reviewed old/new cells before
any permanent file is written. Fresh raw facts supply provenance, never permission
to expand the approved value changes.
"""
import argparse
import copy
import hashlib
import json
import subprocess
import tarfile
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import build_growth as growth
import report_sec_restatements as preview
import seed_financials_sec as sec

ROOT = Path(__file__).resolve().parents[2]
APPROVED_SHA256 = "13471c443f0b1d64431c065c5289412893c30b74206f562775f6b829f2a99997"
CELL_KEYS = ("symbol", "period", "end", "field", "old", "new")
FREQUENCIES = ("annual", "quarterly")
PROTECTED = ("q4-8k", "q4-eps-missing-classification.json", "q4-eps-missing-classification.md",
             "q4-eps-acceptance-ledger.json", "q4-eps-acceptance-ledger.md")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def encode(document):
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()


def atomic(path, raw):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cells(changes):
    result = [tuple(change.get(key) for key in CELL_KEYS) for change in changes]
    if len({row[:4] for row in result}) != len(result):
        raise ValueError("duplicate manifest cell")
    return sorted(result, key=lambda row: row[:4])


def approved_report(path):
    raw = Path(path).read_bytes()
    if digest(raw) != APPROVED_SHA256:
        raise ValueError("report is not the reviewed #153 manifest (SHA-256 mismatch)")
    report = json.loads(raw)
    if not report.get("dryRun") or not report.get("preferLatestFiling") or report.get("stopped"):
        raise ValueError("incomplete or non-restatement dry run")
    summary = preview.summarize(report["files"])
    expected = {"symbols": 580, "switchedPeriods": 1172, "fields": 2151,
                "totalValuesSetToNullIncludingQ4": 0, "q4EpsBecomeNull": 0, "q4EpsChangedNonNull": 2}
    if any(summary.get(key) != value or report["summary"].get(key) != value for key, value in expected.items()):
        raise ValueError("reviewed manifest counts changed")
    cells([c for entry in report["files"] for c in entry["changes"]])
    return report


def preserve_invariants(before, after):
    if before.get("symbol") != after.get("symbol"):
        raise ValueError("symbol changed")
    for key in set(before) | set(after):
        if key not in {*FREQUENCIES, "quality"} and before.get(key) != after.get(key):
            raise ValueError(f"unrelated document metadata changed: {key}")
    for frequency in FREQUENCIES:
        old, new = before.get(frequency) or {}, after.get(frequency) or {}
        for name in ("end", "fy", "fq", "epsQuality"):
            if old.get(name) != new.get(name):
                raise ValueError(f"period alignment / EPS grade changed: {frequency}.{name}")
        for field, column in (new.get("values") or {}).items():
            if not isinstance(column, list) or len(column) != len(new.get("end", [])):
                raise ValueError(f"unaligned financial value column: {frequency}.{field}")
        for name in set(old) | set(new):
            if name not in {"values", "secEnd", "secFilingBasis", "secQ4EpsBlockedReason",
                            "secRestatementIssues", "derived"} and old.get(name) != new.get(name):
                raise ValueError(f"unrelated period metadata changed: {frequency}.{name}")
        for index, flags in enumerate(old.get("secRestatementIssues") or []):
            new_flags = (new.get("secRestatementIssues") or [None] * len(old.get("end", [])))[index] or []
            if any(flag not in new_flags for flag in flags or []):
                raise ValueError("period quality flag removed")
    old_quality, new_quality = before.get("quality") or {}, after.get("quality") or {}
    old_flags = old_quality.get("issues") or []
    if (new_quality.get("issues") or [])[:len(old_flags)] != old_flags:
        raise ValueError("quality flag removed or reordered")
    old_revisions = old_quality.get("revisions") or []
    if (new_quality.get("revisions") or [])[:len(old_revisions)] != old_revisions:
        raise ValueError("original revision history changed")
    for key in set(old_quality) | set(new_quality):
        if key not in {"issues", "revisions"} and new_quality.get(key) != old_quality.get(key):
            raise ValueError(f"existing quality metadata changed: {key}")


def validate_candidate(before, after, entry):
    preserve_invariants(before, after)
    actual, q4 = preview.document_changes(before, after)
    if cells(actual) != cells(entry["changes"]) or cells(q4) != cells(entry.get("q4EpsChanges", [])):
        raise ValueError(f"{entry['symbol']}: candidate differs from reviewed old/new cells")
    if any(change["new"] is None for change in actual):
        raise ValueError("a financial value would be cleared")
    revisions = after.get("quality", {}).get("revisions", [])
    audit = {tuple(r.get(k) for k in ("period", "end", "field", "old", "new"))
             for r in revisions if r.get("kind") == "restated"}
    if any(tuple(c.get(k) for k in ("period", "end", "field", "old", "new")) not in audit for c in actual):
        raise ValueError("changed original is missing from quality.revisions(kind=restated)")
    flags = after.get("quality", {}).get("issues", [])
    required = (["restated"] if actual else []) + (["mixed-basis"] if entry.get("basisUnrestated") else [])
    if entry.get("quarterlyAsReportedYears"):
        required.append("quarterly-as-reported")
    if any(flag not in flags for flag in required):
        raise ValueError("restatement quality flag missing")
    for switched in entry.get("switchedPeriods", []):
        block = after["annual"]
        index = block["end"].index(switched["end"])
        basis = block["secFilingBasis"][index]
        if any(basis.get(key) != switched.get(key) for key in ("accn", "filed", "triggerFields", "retainedFields")):
            raise ValueError("filing basis differs from reviewed manifest")
        if block.get("secEnd", [None] * len(block["end"]))[index] != basis.get("end"):
            raise ValueError("actual SEC period end missing")
    return actual


def annotate_metadata_only(previous, payload):
    """Expose NVDA's raw SEC dates without running any merge or changing alignment."""
    document = copy.deepcopy(previous)
    extracted = sec.extract_periods(payload, sec.reference_from_document(previous))
    if extracted.skip:
        raise ValueError(f"NVDA source dates unavailable: {extracted.skip}")
    for frequency in FREQUENCIES:
        block = document.get(frequency) or {}
        column = list(block.get("secEnd") or [None] * len(block.get("end", [])))
        raw_ends = {row["end"] for row in getattr(extracted, frequency)}
        for index, end in enumerate(block.get("end", [])):
            options = [day for day in raw_ends if abs(sec.days_between(day, end)) <= (10 if frequency == "annual" else 25)]
            if len(options) > 1:
                raise ValueError(f"ambiguous SEC date: {frequency} {end}")
            if options:
                column[index] = options[0]
        block["secEnd"] = column
    return document


def prepare_one(job):
    report_dir, cache_dir, entry, now = job
    path = report_dir / "financials" / f"{entry['symbol']}.json"
    if path.resolve().parent != (report_dir / "financials").resolve():
        raise ValueError("unsafe symbol path")
    original = path.read_bytes()
    if digest(original) != entry["sha256"]:
        raise ValueError(f"{entry['symbol']}: stored file changed since reviewed dry run")
    before = json.loads(original)
    cache = cache_dir / f"{entry['cik']}.json"
    payload = read(cache)
    if int(payload.get("cik", -1)) != int(entry["cik"]):
        raise ValueError("source CIK mismatch")
    source = read(cache.with_suffix(".receipt.json"))
    if source.get("sha256") != digest(cache.read_bytes()):
        raise ValueError("raw SEC cache receipt mismatch")
    if entry["changes"]:
        proposed, reason = preview.preview_document(before, payload, now, True)
        if proposed is None:
            raise ValueError(f"{entry['symbol']}: source no longer supports approved preview: {reason}")
    else:
        proposed = annotate_metadata_only(before, payload)
    validate_candidate(before, proposed, entry)
    return entry["symbol"], original, proposed, source


def hashes(directory):
    return {p.relative_to(directory).as_posix(): digest(p.read_bytes())
            for p in sorted(directory.rglob("*")) if p.is_file()}


def protected_hashes(report_dir):
    result = {}
    for name in PROTECTED:
        path = report_dir / name
        if path.is_file():
            result[name] = digest(path.read_bytes())
        elif path.is_dir():
            result.update({f"{name}/{key}": value for key, value in hashes(path).items()})
    return result


def tree_digest(entries):
    return digest(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode())


def rebuild_growth(report_dir, proposals, now):
    """Same official-source policy and observation clock on both sides of the change."""
    files, bad = growth.read_documents(str(report_dir / "financials"))
    if bad:
        raise ValueError(f"unreadable financial files: {bad}")
    extra, _ = growth.official_candidates(str(report_dir / "fundamentals"), read(report_dir / "universe.json"),
                                          now.date(), [key for key, _ in files if growth.market_of(key) == "TW"])
    before = growth.build_document(files, now, extra)
    after = growth.build_document([(key, proposals.get(key, document)) for key, document in files], now, extra)
    # Preserve unrelated stored rows: only replace rows whose calculation changed
    # when these approved statements changed. No data refresh or staleness rollover.
    old_output = read(report_dir / "growth.json")
    if old_output.get("fields") != growth.FIELDS:
        raise ValueError("growth schema differs")
    old_rows = {row[0]: row for row in before["rows"]}
    new_rows = {row[0]: row for row in after["rows"]}
    impacted = {key for key in set(old_rows) | set(new_rows) if old_rows.get(key) != new_rows.get(key)}
    if impacted - set(proposals):
        raise ValueError("unrelated growth calculation changed")
    stored_rows = {row[0]: row for row in old_output["rows"]}
    changes = []
    for key in sorted(impacted):
        old, new = stored_rows.get(key), new_rows.get(key)
        if new is None:
            raise ValueError("restatement would remove a growth row")
        if old != new:
            changes.append({"symbol": key, "old": old, "new": new})
            stored_rows[key] = new
    output = copy.deepcopy(old_output)
    output["rows"] = [stored_rows[key] for key in sorted(stored_rows)]
    if changes:
        output["generatedAt"] = now.isoformat().replace("+00:00", "Z")
    return output, changes


def apply_batch(report, report_dir, cache_dir, now, workers=1, write=False, baseline_ref=None):
    selected = [entry for entry in report["files"] if entry["changes"] or entry["symbol"] == "NVDA"]
    before_hashes = hashes(report_dir / "financials")
    protected = protected_hashes(report_dir)
    jobs = [(report_dir, cache_dir, entry, now) for entry in selected]
    if workers == 1:
        prepared = list(map(prepare_one, jobs))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            prepared = list(pool.map(prepare_one, jobs))
    originals = {key: original for key, original, _, _ in prepared}
    proposals = {key: document for key, _, document, _ in prepared}
    output, growth_changes = rebuild_growth(report_dir, proposals, now)
    actual = [c for key, raw, document, _ in prepared for c in preview.document_changes(json.loads(raw), document)[0]]
    expected = [c for entry in report["files"] for c in entry["changes"]]
    if cells(actual) != cells(expected):
        raise ValueError("batch differs from reviewed manifest")
    if hashes(report_dir / "financials") != before_hashes or protected_hashes(report_dir) != protected:
        raise ValueError("inputs changed during preflight")
    receipt = {"version": 1, "appliedAt": now.isoformat(), "dryRunReportSha256": APPROVED_SHA256,
               "baselineRef": baseline_ref, "written": write, "financialFiles": len(before_hashes),
               "numericSymbols": len({c["symbol"] for c in actual}), "changedValues": len(actual),
               "annualPeriods": len({(c["symbol"], c["end"]) for c in actual if c["period"] == "annual"}),
               "valuesSetToNull": sum(c["new"] is None for c in actual),
               "q4EpsSetToNull": sum(c["new"] is None for e in report["files"] for c in e.get("q4EpsChanges", [])),
               "q4EpsChanges": [c for e in report["files"] for c in e.get("q4EpsChanges", [])],
               "metadataOnlySymbols": [e["symbol"] for e in selected if not e["changes"]],
               "writtenFiles": [{"symbol": key, "beforeSha256": digest(raw), "afterSha256": digest(encode(document))}
                                for key, raw, document, _ in prepared],
               "financialTreeBeforeSha256": tree_digest(before_hashes),
               "protectedFiles": len(protected), "protectedTreeSha256": tree_digest(protected),
               "qualityFlagsRemoved": 0, "periodAlignmentChanged": False,
               "secRequestsDuringApply": 0,
               "growthChanges": growth_changes, "changes": actual,
               "sources": sorted({source["cik"]: source for _, _, _, source in prepared}.values(), key=lambda r: r["cik"])}
    if not write:
        return receipt
    written = []
    old_growth = (report_dir / "growth.json").read_bytes()
    try:
        for key, raw, document, _ in prepared:
            path = report_dir / "financials" / f"{key}.json"
            if path.read_bytes() != raw:
                raise ValueError(f"{key}: concurrent file change")
            atomic(path, encode(document))
            written.append((path, raw))
        if growth_changes:
            atomic(report_dir / "growth.json", encode(output))
            written.append((report_dir / "growth.json", old_growth))
        actual_hashes = hashes(report_dir / "financials")
        expected_hashes = dict(before_hashes)
        expected_hashes.update({f"{key}.json": digest(encode(document)) for key, document in proposals.items()})
        if actual_hashes != expected_hashes or protected_hashes(report_dir) != protected:
            raise ValueError("unexpected permanent/protected file mutation")
        receipt["financialTreeAfterSha256"] = tree_digest(actual_hashes)
    except BaseException:
        for path, raw in reversed(written):
            atomic(path, raw)
        raise
    return receipt


def verify_tree(before_dir, after_dir, report):
    """Independent on-disk verification, including every unchanged financial file."""
    before, after = hashes(before_dir / "financials"), hashes(after_dir / "financials")
    if set(before) != set(after):
        raise ValueError("financial files added/deleted")
    actual, q4, metadata_only, proposals = [], [], [], {}
    entry_map = {entry["symbol"]: entry for entry in report["files"]}
    permitted = {f"{e['symbol']}.json" for e in report["files"] if e["changes"] or e["symbol"] == "NVDA"}
    for name in before:
        if before[name] == after[name]:
            continue
        if name not in permitted:
            raise ValueError(f"unrelated financial file changed: {name}")
        old, new = read(before_dir / "financials" / name), read(after_dir / "financials" / name)
        changes, q4_changes = preview.document_changes(old, new)
        validate_candidate(old, new, entry_map[old["symbol"]])
        proposals[old["symbol"]] = new
        actual += changes
        q4 += q4_changes
        if not changes:
            metadata_only.append(old["symbol"])
    expected = [c for entry in report["files"] for c in entry["changes"]]
    if cells(actual) != cells(expected):
        raise ValueError("written value diff does not equal reviewed dry run")
    protected_before, protected_after = protected_hashes(before_dir), protected_hashes(after_dir)
    if protected_before != protected_after:
        raise ValueError("Q4 answer set / classification / acceptance ledger changed")
    if any(c["new"] is None for c in actual):
        raise ValueError("a financial value was cleared")
    growth_written = read(after_dir / "growth.json")
    clock = datetime.fromisoformat(growth_written["generatedAt"].replace("Z", "+00:00"))
    expected_growth, growth_changes = rebuild_growth(before_dir, proposals, clock)
    if not growth.same_content(growth_written, expected_growth):
        raise ValueError("written growth differs from independent recomputation")
    return {"financialFilesCompared": len(before), "changedValues": len(actual),
            "numericSymbols": len({c["symbol"] for c in actual}),
            "annualPeriods": len({(c["symbol"], c["end"]) for c in actual if c["period"] == "annual"}),
            "valueDiffEqualsDryRun": True, "otherValueChanges": 0, "valuesSetToNull": 0,
            "q4EpsSetToNull": sum(c["new"] is None for c in q4),
            "qualityFlagsRemoved": 0, "periodAlignmentChanged": False,
            "metadataOnlySymbols": sorted(metadata_only), "q4EpsChanges": q4,
            "growthRowsRecomputed": len(growth_changes), "growthEqualsIndependentRebuild": True,
            "protectedFilesCompared": len(protected_before), "protectedFilesUnchanged": True,
            "protectedHashes": protected_after,
            "protectedTreeSha256": tree_digest(protected_after),
            "financialTreeBeforeSha256": tree_digest(before), "financialTreeAfterSha256": tree_digest(after),
            "changes": sorted(actual, key=lambda c: tuple(c[k] for k in CELL_KEYS[:4]))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs/report")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/report/sec-restated-comparatives.json")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-against", metavar="GIT_REF")
    args = parser.parse_args(argv)
    report = approved_report(args.report)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.verify_against:
        base = subprocess.check_output(["git", "rev-parse", "--verify", "--end-of-options",
                                        args.verify_against + "^{commit}"], cwd=ROOT, text=True).strip()
        protected_paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", base, "--",
            *[f"docs/report/{name}" for name in PROTECTED]], cwd=ROOT, text=True).splitlines()
        with tempfile.TemporaryDirectory(prefix="sec-restatement-baseline-") as temporary:
            archive = Path(temporary) / "baseline.tar"
            with archive.open("wb") as stream:
                subprocess.run(["git", "archive", base, "docs/report/financials",
                                "docs/report/growth.json", "docs/report/fundamentals", "docs/report/universe.json",
                                *protected_paths], cwd=ROOT, stdout=stream, check=True)
            with tarfile.open(archive) as source:
                source.extractall(temporary, filter="data")
            receipt = verify_tree(Path(temporary) / "docs/report", args.report_dir, report)
        receipt.update(version=1, verifiedAt=now.isoformat(), baselineRef=args.verify_against,
                       dryRunReportSha256=APPROVED_SHA256)
        output = args.report_dir / "sec-restated-comparatives-verification.json"
        atomic(output, (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    else:
        if not args.cache_dir:
            parser.error("--cache-dir is required for offline preflight/apply")
        baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.report_dir, text=True).strip()
        receipt = apply_batch(report, args.report_dir, args.cache_dir, now, args.workers, args.apply, baseline)
        if args.apply:
            atomic(args.report_dir / "sec-restated-comparatives-apply.json",
                   (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({k: v for k, v in receipt.items() if k not in {"changes", "sources", "growthChanges", "writtenFiles"}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
