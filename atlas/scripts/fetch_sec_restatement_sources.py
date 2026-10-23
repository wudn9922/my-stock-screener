"""Fetch only #153's 570 unique source CIKs (580 symbols plus NVDA metadata).

Writes raw caches and receipts only. The offline apply independently rejects any
numeric result outside the reviewed manifest. Existing identified User-Agent,
4 requests/second, 12,000-request cap and first-403-stop rules remain in force.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from apply_sec_restatements import ROOT, approved_report, atomic, digest, encode, read
from report_sec_restatements import ReportClient
from seed_financials_sec import FACTS_URL, resolve_user_agent


def fetch_sources(report, cache_dir, client):
    cache_dir.mkdir(parents=True, exist_ok=True)
    ciks = sorted({e["cik"] for e in report["files"] if e["changes"] or e["symbol"] == "NVDA"})
    sources, hits = [], 0
    for index, cik in enumerate(ciks, 1):
        path = cache_dir / f"{cik}.json"
        receipt = path.with_suffix(".receipt.json")
        if path.exists() and receipt.exists():
            payload, source = read(path), read(receipt)
            if int(payload.get("cik", -1)) != int(cik) or source.get("sha256") != digest(path.read_bytes()):
                raise ValueError(f"corrupt SEC cache or receipt: {cik}")
            hits += 1
        else:
            payload = client.company_facts(cik)
            if not payload or int(payload.get("cik", -1)) != int(cik):
                raise ValueError(f"SEC source CIK mismatch: {cik}")
            raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
            source = {"cik": cik, "sourceUrl": FACTS_URL.format(cik=cik),
                      "fetchedAt": datetime.now(timezone.utc).isoformat(), "sha256": digest(raw), "bytes": len(raw)}
            atomic(path, raw)
            atomic(receipt, encode(source))
        sources.append(source)
        if index % 20 == 0 or index == len(ciks):
            print(f"{index}/{len(ciks)} source CIKs cached", flush=True)
    summary = {"sources": sources, "cacheHits": hits, "requests": client.requests,
               "maxRequestsPerSecond": 4, "maxRequests": 12000, "stopOnFirst403": True}
    atomic(cache_dir / "receipts.json", encode(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/report/sec-restated-comparatives.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache/task-b-sec")
    args = parser.parse_args(argv)
    resolved = args.cache_dir.resolve()
    if (ROOT / "docs").resolve() == resolved or (ROOT / "docs").resolve() in resolved.parents:
        parser.error("raw SEC caches must be outside docs/")
    fetch_sources(approved_report(args.report), args.cache_dir, ReportClient(resolve_user_agent(os.environ)))


if __name__ == "__main__":
    main()
