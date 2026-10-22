"""Read-only SEC comparative preview. Only JSON/Markdown reports and a raw cache are written.

python atlas/scripts/report_sec_restatements.py --prefer-latest-filing
Use --offline to replay a populated cache without any network requests.
"""
import argparse
import copy
import hashlib
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import build_financials as fin
import sec_ifrs
import seed_financials_sec as sec
from sec_restatements import BASIS, BLOCKED, EPS, preserve_quality

ROOT = Path(__file__).resolve().parents[2]


class RequestBudgetExceeded(RuntimeError):
    pass


class ReportClient(sec.SecClient):
    """Serialized requests, >=250ms after each attempt, hard budget, first 403 stops the run."""
    def __init__(self, user_agent, **kwargs):
        kwargs["stop_on_forbidden"] = True
        super().__init__(user_agent, **kwargs)
        self.forbidden = False

    def _request(self, url):
        if self.forbidden:
            raise sec.SecForbidden(url)
        if self.requests >= 12_000:
            raise RequestBudgetExceeded("12000 SEC request budget exhausted")
        sleeper = self.sleeper
        self.sleeper = lambda seconds: sleeper(max(0.25, seconds))
        try:
            status, body = super()._request(url)
        finally:
            self.sleeper = sleeper
        if status == 403:
            self.forbidden = True
        return status, body


def preview_document(previous, payload, now, prefer_latest_filing=False):
    """Simulate in memory; preserve existing quality flags in opt-in mode."""
    extracted = sec.extract_periods(payload, sec.reference_from_document(previous), prefer_latest_filing,
                                    current=previous if prefer_latest_filing else None)
    if extracted.skip == "revenue-scope-mismatch":
        return None, extracted.skip
    if not extracted.skip and not previous.get("foreignReporter") and "foreign-filer" not in extracted.issues:
        return sec.merge_into_document(previous, extracted, previous["symbol"], now)[0], None
    if prefer_latest_filing:
        from sec_restatements import overlay_document
        basis = sec_ifrs.IFRS_BASIS if payload.get("facts", {}).get("ifrs-full") else sec_ifrs.US_GAAP_FOREIGN_BASIS
        result = sec_ifrs.extract(payload, basis, True, current=previous)
        if result.skip:
            return None, result.skip
        if result.currency != previous.get("currency"):
            return None, "currency-mismatch: no cross-currency comparison"
        return overlay_document(previous, result.annual, result.quarterly, result.restatement_revisions,
                                now, factor=fin.adr_eps_factor(previous)), None
    foreign, reason = sec_ifrs.build_foreign_document(payload, previous["symbol"], now, prefer_latest_filing)
    if foreign is None:
        return None, extracted.skip or reason
    if foreign.get("currency") != previous.get("currency"):
        return None, "currency-mismatch: no cross-currency comparison"
    # Foreign builders normally create a new document. For this preview, retain unmatched
    # stored history but replace every matched row in full; never Yahoo-fill its missing fields.
    old, new = fin.read_sections(previous), fin.read_sections(foreign)
    factor = fin.adr_eps_factor(previous)
    if factor not in (None, 1):
        for frequency in ("annual", "quarterly"):
            for item in new[frequency]:
                sec.scale_sec_to_adr(item["values"], factor)
    document = copy.deepcopy(previous)
    for frequency in ("annual", "quarterly"):
        items, _, _, _, _ = sec._merge_section(old[frequency], new[frequency], frequency, now.date().isoformat())
        fin.assign_fiscal_labels(items if frequency == "annual" else [],
                                 items if frequency == "quarterly" else [],
                                 fallback_end_month=previous.get("fyEndMonth") or 12)
        document[frequency] = fin.write_section(items, old["extras"][frequency], frequency == "quarterly")
    if prefer_latest_filing:
        preserve_quality(document, previous, foreign.get("quality", {}).get("revisions", []), now.date().isoformat())
    return document, None


def document_changes(previous, proposed):
    """All changed cells, including new/null/deleted cells, with candidate filing provenance."""
    changes, q4 = [], []
    for frequency in ("annual", "quarterly"):
        old, new = previous.get(frequency) or {}, proposed.get(frequency) or {}
        old_ends, new_ends = old.get("end", []), new.get("end", [])
        old_index, new_index = {e: i for i, e in enumerate(old_ends)}, {e: i for i, e in enumerate(new_ends)}
        for end in sorted(set(old_ends) | set(new_ends)):
            oi, ni = old_index.get(end), new_index.get(end)
            metadata = ((new.get(BASIS) or [None] * len(new_ends))[ni] or {}) if ni is not None else {}
            reason = "material-income-restatement" if metadata.get("accn") else "as-reported-or-existing-builder-normalization"
            blocked = ((new.get(BLOCKED) or [None] * len(new_ends))[ni]) if ni is not None else None
            for field in sorted(set(old.get("values", {})) | set(new.get("values", {}))):
                before = old.get("values", {}).get(field, [None] * len(old_ends))[oi] if oi is not None else None
                after = new.get("values", {}).get(field, [None] * len(new_ends))[ni] if ni is not None else None
                if before == after:
                    continue
                entry = {"symbol": previous["symbol"], "period": frequency, "end": end,
                         "field": field, "old": before, "new": after,
                         "accn": metadata.get("accn") or metadata.get("annualAccn"),
                         "filed": metadata.get("filed") or metadata.get("annualFiled"),
                         "reason": blocked if field in EPS and blocked and after is None else reason}
                if ni is None:
                    entry["reason"] = "existing-builder-period-alignment-or-deduplication"
                changes.append(entry)
                if frequency == "quarterly" and field in EPS:
                    fq = (new.get("fq") or [None] * len(new_ends))[ni] if ni is not None else None
                    old_fq = (old.get("fq") or [None] * len(old_ends))[oi] if oi is not None else None
                    if fq == 4 or old_fq == 4 or blocked:
                        q4.append(entry.copy())
    return changes, q4


def summarize(entries):
    changes = [change for entry in entries for change in entry["changes"]]
    q4 = [change for entry in entries for change in entry["q4EpsChanges"]]
    switched = [(entry["symbol"], row) for entry in entries for row in entry.get("switchedPeriods", [])]
    primary = [(symbol, row) for symbol, row in switched if set(row["triggerFields"]) & {"revenue", "netIncome"}]
    return {"files": len(entries), "reviewed": sum(e["status"] == "reviewed" for e in entries),
            "statuses": dict(sorted(Counter(e["status"] for e in entries).items())),
            "symbols": len({c["symbol"] for c in changes}),
            "periods": len({(c["symbol"], c["period"], c["end"]) for c in changes}),
            "fields": len(changes), "bySymbol": dict(sorted(Counter(c["symbol"] for c in changes).items())),
            "switchedPeriods": len(switched), "switchedSymbols": len({symbol for symbol, _ in switched}),
            "revenueOrNetIncomeSwitchedPeriods": len(primary),
            "revenueOrNetIncomeSwitchedSymbols": len({symbol for symbol, _ in primary}),
            "switchedByTriggerField": dict(sorted(Counter(field for _, row in switched for field in row["triggerFields"]).items())),
            "valuesSetToNull": sum(c["new"] is None for c in changes) - sum(c["new"] is None for c in q4),
            "totalValuesSetToNullIncludingQ4": sum(c["new"] is None for c in changes),
            "q4EpsChangedNonNull": sum(c["new"] is not None for c in q4),
            "byField": dict(sorted(Counter(c["field"] for c in changes).items())),
            "q4EpsAffected": len(q4), "q4EpsBecomeNull": sum(c["new"] is None for c in q4),
            "q4EpsPeriods": len({(c["symbol"], c["end"]) for c in q4})}


def record_comparison(entry, previous, proposed):
    entry["changes"], entry["q4EpsChanges"] = document_changes(previous, proposed)
    changed_ends = {c["end"] for c in entry["changes"] if c["period"] == "annual"}
    block = proposed.get("annual") or {}
    entry["switchedPeriods"] = []
    for index, end in enumerate(block.get("end", [])):
        if end in changed_ends:
            basis = (block.get(BASIS) or [None] * len(block["end"]))[index] or {}
            if basis.get("switched"):
                entry["switchedPeriods"].append({"end": end, "accn": basis.get("accn"), "filed": basis.get("filed"),
                    "triggerFields": basis.get("triggerFields", []), "retainedFields": basis.get("retainedFields", [])})
    entry["basisUnrestated"] = [r for r in proposed.get("quality", {}).get("revisions", [])
                               if r.get("kind") == "basis-unrestated"]
    entry["status"] = "reviewed"


def _safe_output(path, financials):
    resolved = path.resolve()
    if (financials.resolve() in resolved.parents or "financials" in resolved.parts or "q4-8k" in resolved.parts
            or resolved.name in {"q4-eps-missing-classification.json", "q4-eps-acceptance-ledger.json"}):
        raise ValueError("report/cache output must not be under financials/")
    return path


def _offline_file(job):
    """Independent, read-only CPU work; workers never instantiate an HTTP client."""
    path, cache_dir, mapping, now, prefer_latest = job
    previous = json.loads(path.read_text())
    if previous.get("market") != "US":
        return None
    key = previous["symbol"]
    cik = mapping.get(key)
    entry = {"symbol": key, "file": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "cik": cik,
             "status": "not-reviewed", "changes": [], "q4EpsChanges": [], "cacheReads": 0}
    if cik is None:
        entry["status"] = "unmapped-cik"
        return entry
    try:
        cache = cache_dir / f"{cik}.json"
        if not cache.exists():
            entry["status"] = "offline-cache-missing"
            return entry
        payload = json.loads(cache.read_text())
        entry["cacheReads"] += 1
        if payload is None:
            entry["status"] = "no-companyfacts"
            return entry
        if int(payload.get("cik", -1)) != int(cik):
            entry["status"] = "cik-mismatch"
            return entry
        predecessor = sec.PREDECESSOR_CIKS.get(key)
        if predecessor and predecessor != cik and sec.extract_periods(payload).skip == "short-history":
            entry["mappedCik"], entry["cik"] = cik, predecessor
            cache = cache_dir / f"{predecessor}.json"
            if not cache.exists():
                entry.update(status="offline-cache-missing", reason="known predecessor history not cached")
                return entry
            payload = json.loads(cache.read_text())
            entry["cacheReads"] += 1
            if payload is None or int(payload.get("cik", -1)) != int(predecessor):
                entry.update(status="cik-mismatch", reason="predecessor companyfacts unavailable or mismatched")
                return entry
        proposed, reason = preview_document(previous, payload, now, prefer_latest)
        if proposed is None:
            entry.update(status="unsupported", reason=reason)
        else:
            record_comparison(entry, previous, proposed)
    except Exception as error:
        entry.update(status="error", reason=f"{type(error).__name__}: {error}")
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefer-latest-filing", action="store_true", help="default off")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--workers", type=int, default=1, help="offline comparison processes (1-8; no parallel HTTP)")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs/report")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/report/sec-restated-comparatives.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache/sec-restatements")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 8 or (args.workers != 1 and not args.offline):
        parser.error("--workers must be 1-8; parallel workers require --offline")
    financials = args.report_dir / "financials"
    for output in (args.report, args.report.with_suffix(".md"), args.cache_dir):
        _safe_output(output, financials)
    progress = json.loads((args.report_dir / "fundamentals/us-market-progress.json").read_text())
    mapping = sec_ifrs.progress_cik_map(progress)
    client = None if args.offline else ReportClient(sec.resolve_user_agent(os.environ))
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    entries, stopped, cache_hits = [], None, 0
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.offline:
        jobs = [(path, args.cache_dir, mapping, now, args.prefer_latest_filing)
                for path in sorted(financials.glob("*.json"))]
        def collect(results):
            nonlocal cache_hits
            for entry in results:
                if entry is not None:
                    cache_hits += entry.pop("cacheReads")
                    entries.append(entry)
                    if len(entries) % 100 == 0:
                        print(f"Reviewed {len(entries)} US files; SEC requests 0", flush=True)
        if args.workers == 1:
            collect(map(_offline_file, jobs))
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                collect(executor.map(_offline_file, jobs, chunksize=4))
        return finish_report(args, entries, now, None, cache_hits, None)
    for path in sorted(financials.glob("*.json")):
        previous = json.loads(path.read_text())
        if previous.get("market") != "US":
            continue
        key = previous["symbol"]
        cik = mapping.get(key)
        entry = {"symbol": key, "file": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "cik": cik,
                 "status": "not-reviewed", "changes": [], "q4EpsChanges": []}
        entries.append(entry)
        if cik is None:
            entry.update(status="unmapped-cik")
            continue
        cache = args.cache_dir / f"{cik}.json"
        try:
            if cache.exists():
                payload = json.loads(cache.read_text())
                cache_hits += 1
            elif stopped:
                entry.update(status="not-requested-after-stop", reason=stopped)
                continue
            elif client is None:
                entry.update(status="offline-cache-missing")
                continue
            else:
                payload = client.company_facts(cik)
                cache.write_text(json.dumps(payload, separators=(",", ":")))
            if payload is None:
                entry.update(status="no-companyfacts")
                continue
            if int(payload.get("cik", -1)) != int(cik):
                entry.update(status="cik-mismatch")
                continue
            predecessor = sec.PREDECESSOR_CIKS.get(key)
            if predecessor and predecessor != cik and sec.extract_periods(payload).skip == "short-history":
                entry["mappedCik"], entry["cik"] = cik, predecessor
                older_cache = args.cache_dir / f"{predecessor}.json"
                if older_cache.exists():
                    payload = json.loads(older_cache.read_text())
                    cache_hits += 1
                elif client is not None and not stopped:
                    payload = client.company_facts(predecessor)
                    older_cache.write_text(json.dumps(payload, separators=(",", ":")))
                else:
                    entry.update(status="offline-cache-missing", reason="known predecessor history not cached")
                    continue
                if payload is None or int(payload.get("cik", -1)) != int(predecessor):
                    entry.update(status="cik-mismatch", reason="predecessor companyfacts unavailable or mismatched")
                    continue
            proposed, reason = preview_document(previous, payload, now, args.prefer_latest_filing)
            if proposed is None:
                entry.update(status="unsupported", reason=reason)
                continue
            record_comparison(entry, previous, proposed)
        except (sec.SecForbidden, RequestBudgetExceeded) as error:
            stopped = f"{type(error).__name__}: {error}"
            entry.update(status="stopped", reason=stopped)
        except Exception as error:  # A per-file diagnostic must not erase the coverage manifest.
            entry.update(status="error", reason=f"{type(error).__name__}: {error}")
        if len(entries) % 100 == 0:
            print(f"Reviewed {len(entries)} US files; SEC requests {client.requests if client else 0}", flush=True)
    return finish_report(args, entries, now, client, cache_hits, stopped)


def finish_report(args, entries, now, client, cache_hits, stopped):
    counts = summarize(entries)
    # Drift or missing-field erasures are failures, not eligible option changes.
    forbidden = {"as-reported-or-existing-builder-normalization", "existing-builder-period-alignment-or-deduplication"}
    drift = [c for entry in entries for c in entry["changes"] if c["reason"] in forbidden]
    if args.prefer_latest_filing and (drift or counts["valuesSetToNull"]):
        raise ValueError("restatement-only invariant violated: normalization drift or non-Q4 null")
    report = {"generatedAt": now.isoformat(), "preferLatestFiling": args.prefer_latest_filing,
              "selectionRule": "later-filing; income-statement difference strictly >2% against current values; retain unreported fields",
              "dryRun": True, "financialFilesWritten": 0, "summary": counts, "stopped": stopped,
              "secAccess": {"requests": client.requests if client else 0, "cacheHits": cache_hits,
                            "maxRequests": 12000, "maxRequestsPerSecond": 4,
                            "stopOnFirst403": True, "userAgent": client.user_agent if client else None},
              "files": entries}
    receipt = args.cache_dir / "acquisition.json"
    if receipt.exists():
        report["cacheAcquisition"] = json.loads(receipt.read_text())
    examples = {}
    for entry in entries:
        if entry["symbol"] != "AD":
            continue
        stored = json.loads(Path(entry["file"]).read_text())
        for end in ("2023-12-31", "2024-12-31"):
            block = stored["annual"]
            if end not in block["end"]:
                continue
            index = block["end"].index(end)
            before = {field: column[index] for field, column in block["values"].items()}
            after = dict(before)
            changes = [c for c in entry["changes"] if c["period"] == "annual" and c["end"] == end]
            after.update({c["field"]: c["new"] for c in changes})
            examples[end] = {"before": before, "after": after, "changes": changes,
                             "q4EpsChanges": [c for c in entry["q4EpsChanges"] if c["end"] == end]}
    report["examples"] = {"AD": examples}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
    unsupported = [(e["symbol"], e.get("reason", e["status"])) for e in entries if e["status"] != "reviewed"]
    lines = ["# SEC 重大重編比較期試跑", "",
             "僅當較晚申報的營收、淨利或其他損益欄位，相對目前值有嚴格超過 2% 的差異時才切換。未申報欄位保留原值，記錄 basis-unrestated，並對該期間標記 mixed-basis。日期、期間、未切換列及其他 Q4 EPS 原封不動。", "",
             "同期間須有較晚的年度申報；只有首次年報與現存值不同不視為重編。其他損益欄位含成本、毛利、營業利益、稅前利益、稅費、利息及 EPS；候選值先對齊該列既有拆股／ADR 基準。", "",
             f"掃描 {counts['files']:,} 份 US 財報；可評估 {counts['reviewed']:,} 份。切換 {counts['switchedPeriods']:,} 個年度期間、{counts['switchedSymbols']:,} 個股票。",
             f"其中營收／淨利觸發：{counts['revenueOrNetIncomeSwitchedPeriods']:,} 個期間、{counts['revenueOrNetIncomeSwitchedSymbols']:,} 個股票；其餘由其他損益欄位觸發。", "",
             f"變動欄位值共 {counts['fields']:,} 個；一般欄位設為 null：{counts['valuesSetToNull']:,}。",
             f"Q4 EPS 另計：設為 null {counts['q4EpsBecomeNull']:,} 個值、改為非 null 值 {counts['q4EpsChangedNonNull']:,} 個，共影響 {counts['q4EpsAffected']:,} 個值。只有已切換年度欠缺同基準季度輸入時才能清空 Q4 EPS。", "",
             "以上是試算，未寫入 docs/report/financials/，未執行合併。原 builder 正規化、日期對齊與去重差異完全排除。逐筆原值、新值、申報來源、切換觸發欄位與保留原值稽核均在同名 JSON。", "",
             "| AD 年度 | 營收原值 | 營收重編值 | 淨利原值 | 淨利重編值 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for end, example in examples.items():
        a, b = example["before"], example["after"]
        lines.append(f"| {end} | {a.get('revenue'):,} | {b.get('revenue'):,} | {a.get('netIncome'):,} | {b.get('netIncome'):,} |")
    lines += ["", "| 欄位 | 變動值數 |", "| --- | ---: |"]
    lines += [f"| {field} | {count:,} |" for field, count in counts["byField"].items()]
    lines += ["", "無法評估（不代表沒有差異）：" + "；".join(f"{key}: {reason}" for key, reason in unsupported) + "。",
              "Q4 被阻擋時的 accession／filed 是觸發年度基準的申報。一般欄位 null 數排除上面單獨列出的 Q4 EPS 例外；全部 null 均屬該例外。"]
    args.report.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(json.dumps({key: value for key, value in counts.items() if key != "bySymbol"}, ensure_ascii=False), flush=True)
    return 1 if stopped or any(e["status"] in {"error", "offline-cache-missing", "cik-mismatch"} for e in entries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
