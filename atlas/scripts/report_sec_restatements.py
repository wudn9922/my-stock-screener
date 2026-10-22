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
    extracted = sec.extract_periods(payload, sec.reference_from_document(previous), prefer_latest_filing)
    if extracted.skip == "revenue-scope-mismatch":
        return None, extracted.skip
    if not extracted.skip and not previous.get("foreignReporter") and "foreign-filer" not in extracted.issues:
        return sec.merge_into_document(previous, extracted, previous["symbol"], now)[0], None
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
            reason = "latest-filing-whole-row" if metadata.get("accn") else "as-reported-or-existing-builder-normalization"
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
                         "reason": blocked if field in EPS and blocked else reason}
                if ni is None:
                    entry["reason"] = "existing-builder-period-alignment-or-deduplication"
                changes.append(entry)
                if frequency == "quarterly" and field in EPS:
                    fq = (new.get("fq") or [None] * len(new_ends))[ni] if ni is not None else None
                    old_fq = (old.get("fq") or [None] * len(old_ends))[oi] if oi is not None else None
                    if fq == 4 or old_fq == 4:
                        q4.append(entry.copy())
    return changes, q4


def summarize(entries):
    changes = [change for entry in entries for change in entry["changes"]]
    q4 = [change for entry in entries for change in entry["q4EpsChanges"]]
    return {"files": len(entries), "reviewed": sum(e["status"] == "reviewed" for e in entries),
            "statuses": dict(sorted(Counter(e["status"] for e in entries).items())),
            "symbols": len({c["symbol"] for c in changes}),
            "periods": len({(c["symbol"], c["period"], c["end"]) for c in changes}),
            "fields": len(changes), "bySymbol": dict(sorted(Counter(c["symbol"] for c in changes).items())),
            "byField": dict(sorted(Counter(c["field"] for c in changes).items())),
            "q4EpsAffected": len(q4), "q4EpsBecomeNull": sum(c["new"] is None for c in q4),
            "q4EpsPeriods": len({(c["symbol"], c["end"]) for c in q4})}


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
            entry["changes"], entry["q4EpsChanges"] = document_changes(previous, proposed)
            entry["status"] = "reviewed"
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
            entry["changes"], entry["q4EpsChanges"] = document_changes(previous, proposed)
            entry["status"] = "reviewed"
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
    report = {"generatedAt": now.isoformat(), "preferLatestFiling": args.prefer_latest_filing,
              "dryRun": True, "financialFilesWritten": 0, "summary": counts, "stopped": stopped,
              "secAccess": {"requests": client.requests if client else 0, "cacheHits": cache_hits,
                            "maxRequests": 12000, "maxRequestsPerSecond": 4,
                            "stopOnFirst403": True, "userAgent": client.user_agent if client else None},
              "files": entries}
    receipt = args.cache_dir / "acquisition.json"
    if receipt.exists():
        report["cacheAcquisition"] = json.loads(receipt.read_text())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Full-market cell-level reports are large; compact JSON keeps the auditable
    # artifact below hosting limits. The adjacent Markdown is the reading view.
    args.report.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
    nulls = Counter(c["field"] for entry in entries for c in entry["changes"] if c["new"] is None)
    unsupported = [(e["symbol"], e.get("reason", e["status"])) for e in entries if e["status"] != "reviewed"]
    args.report.with_suffix(".md").write_text(
        "# SEC 重編比較期試跑\n\n"
        f"掃描 {counts['files']:,} 份美股檔案，已審查 {counts['reviewed']:,} 份。"
        f"預計變動 {counts['symbols']:,} 個股票、{counts['periods']:,} 個期間、{counts['fields']:,} 個欄位值。\n\n"
        f"Q4 EPS 影響 {counts['q4EpsAffected']:,} 個值（{counts['q4EpsPeriods']:,} 個期間），"
        f"其中 {counts['q4EpsBecomeNull']:,} 個會變為 null。\n\n"
        f"所有欄位變動中，{sum(nulls.values()):,} 個值會轉為 null。"
        "最新申報未重列的舊年度欄位（尤其資產負債表）保持 null，不從其他 accession 借值。\n\n"
        "只產生試跑報告，未寫入 financials/；逐筆變更、accession、filed、原因與檔案雜湊見同名 JSON。"
        "不同 accession 的季度不能證明已重編；欠缺同基準季度時保留季度原值，停止跨基準 Q4 EPS 推導。\n\n"
        f"狀態：`{json.dumps(counts['statuses'], ensure_ascii=False)}`；停止原因：{stopped or '無'}。\n"
        + ("\n無法評估（不代表沒有差異）：" + "；".join(f"{key}: {reason}" for key, reason in unsupported) + "。\n" if unsupported else "")
        + "\n| 欄位 | 變動值數 | 其中轉為 null |\n| --- | ---: | ---: |\n"
        + "".join(f"| {field} | {count:,} | {nulls[field]:,} |\n" for field, count in counts["byField"].items())
        + "\nQ4 被阻擋時的 accession／filed 指觸發年度基準的申報，並不代表它提供了保留的原季數值。"
          "變動計數包含既有 builder 的期間對齊、去重及一般正規化，逐筆原因在 JSON 中。\n"
    )
    print(json.dumps(counts, ensure_ascii=False, default=str)[:1000], flush=True)
    return 1 if stopped or any(e["status"] in {"error", "offline-cache-missing", "cik-mismatch"} for e in entries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
