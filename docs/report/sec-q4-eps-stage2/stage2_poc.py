#!/usr/bin/env python3
"""Deterministic, read-only SEC Q4 diluted EPS stage-two sample and extractor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = ROOT / "docs/report/sec-q4-eps-stage2"
sys.path.insert(0, str(ROOT / "scripts"))
import sec_q4_eps_poc as poc  # noqa: E402

SEED = "20261018"
MISSING_CLASS_ORDER = (
    "no-q4-net-income",
    "no-annual-diluted-eps",
    "q1-q3-incomplete",
    "derivation-rejected",
)
CLASS_NAMES = {
    "no-q4-net-income": "no_q4_net_income_missing",
    "no-annual-diluted-eps": "annual_diluted_eps_missing",
    "q1-q3-incomplete": "q1_q3_incomplete",
    "derivation-rejected": "derivation_rejected",
}
CONTROL_SHARDS = {4, 5, 6, 7}
MAX_REQUESTS = 12_000
ARCHIVE_ROOT = "https://www.sec.gov/Archives/edgar/data"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def canonical_sha256(payload, excluded=("freeze_sha256",)):
    body = {key: value for key, value in payload.items() if key not in excluded}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def assert_committed(relative_path):
    """Require the exact working file to exist in HEAD before gated next steps."""
    relative_path = str(relative_path)
    working = (ROOT / relative_path).read_bytes()
    committed = subprocess.check_output(["git", "show", f"HEAD:{relative_path}"], cwd=ROOT)
    if working != committed:
        raise RuntimeError(f"{relative_path} must be committed unchanged before this step")
    return git("rev-parse", "HEAD")


def seeded_rank(seed, scope, *parts):
    return sha256_bytes("|".join((str(seed), str(scope), *(str(part) for part in parts))).encode("utf-8"))


def issuer_index():
    progress_path = ROOT / "docs/report/fundamentals/us-market-progress-summary.json"
    snapshot_path = ROOT / "docs/report/fundamentals/us-market-snapshot.json"
    progress = load_json(progress_path)
    snapshot = {row["symbol"]: row for row in load_json(snapshot_path).get("eligibleSymbols", [])}
    mapped = poc.source_map(progress)
    out = {}
    for symbol, mappings in mapped.items():
        ciks = {str(item["cik"]).zfill(10) for item in mappings}
        if symbol not in snapshot or len(ciks) != 1:
            continue
        item = mappings[0]
        out[symbol] = {
            "symbol": symbol,
            "cik": next(iter(ciks)),
            "company_name": item.get("name") or snapshot[symbol].get("name"),
            "sic": item.get("sic"),
            "sic_description": item.get("sicDescription"),
            "exchange": snapshot[symbol].get("exchange"),
        }
    return out


def read_financial(symbol, cache):
    if symbol in cache:
        return cache[symbol]
    path = ROOT / "docs/report/financials" / f"{symbol}.json"
    try:
        payload = load_json(path)
    except (OSError, ValueError):
        cache[symbol] = None
        return None
    if payload.get("market") != "US" or payload.get("symbol", symbol) != symbol:
        cache[symbol] = None
        return None
    cache[symbol] = (path, payload)
    return cache[symbol]


def period_from_local(symbol, fy, end, issuer, cache):
    loaded = read_financial(symbol, cache)
    if not loaded:
        return None
    path, doc = loaded
    quarterly = doc.get("quarterly") or {}
    qends = quarterly.get("end") or []
    qfys = quarterly.get("fy") or []
    qfqs = quarterly.get("fq") or []
    qsources = quarterly.get("src") or ["yahoo"] * len(qends)
    qeps = (quarterly.get("values") or {}).get("epsDiluted") or [None] * len(qends)
    matches = [index for index, (qend, qfy, qfq, qsrc) in enumerate(zip(qends, qfys, qfqs, qsources))
               if qend == end and qfy == fy and qfq == 4 and qsrc == "sec"]
    if len(matches) != 1:
        return None
    q_index = matches[0]
    annual = doc.get("annual") or {}
    aends = annual.get("end") or []
    afys = annual.get("fy") or []
    asources = annual.get("src") or ["yahoo"] * len(aends)
    annual_matches = [aend for aend, afy, asrc in zip(aends, afys, asources)
                      if afy == fy and asrc == "sec"]
    unique_annual = sorted(set(annual_matches))
    if len(unique_annual) != 1:
        return None
    q3_ends = [qend for qend, qfy, qfq, qsrc in zip(qends, qfys, qfqs, qsources)
               if qfy == fy and qfq == 3 and qsrc == "sec"]
    q4_start = None
    if len(q3_ends) == 1:
        q4_start = (date.fromisoformat(q3_ends[0]) + timedelta(days=1)).isoformat()
    split = doc.get("split") if isinstance(doc.get("split"), dict) else {}
    split_events = split.get("events") if isinstance(split.get("events"), list) else []
    return {
        **issuer,
        "fy": int(fy),
        "q4_end": end,
        "period_end_year": int(end[:4]),
        "q4_start_estimate": q4_start,
        "local_annual_report_end": unique_annual[0],
        "local_quarter_eps_diluted": qeps[q_index] if q_index < len(qeps) else None,
        "financials_path": str(path.relative_to(ROOT)),
        "financials_sha256": sha256_file(path),
        "financials_split_adjusted": bool(split.get("adjusted")),
        "split_events": split_events,
    }


def missing_candidates(classification, issuers, finance_cache, seed):
    result = defaultdict(list)
    rejected = Counter()
    for row in classification.get("rows", []):
        klass = row.get("class")
        symbol = row.get("symbol")
        if klass not in CLASS_NAMES or not isinstance(row.get("fy"), int) or not 2012 <= row["fy"] <= 2025:
            rejected["out_of_scope"] += 1
            continue
        issuer = issuers.get(symbol)
        if not issuer:
            rejected["no_current_single_cik_mapping"] += 1
            continue
        record = period_from_local(symbol, row["fy"], row["end"], issuer, finance_cache)
        if not record:
            rejected["no_unique_sec_q4_and_annual_period"] += 1
            continue
        if record["local_quarter_eps_diluted"] is not None:
            rejected["local_q4_eps_not_missing"] += 1
            continue
        record.update({
            "missing_class": klass,
            "classification_detail": row.get("detail"),
            "period_selection_rank": seeded_rank(seed, "missing-period", klass, symbol, row["fy"], row["end"]),
        })
        result[klass].append(record)
    return result, rejected


def _pick_primary_rows(class_name, rows, used_ciks, seed, count=15):
    by_year = defaultdict(list)
    for row in rows:
        by_year[row["period_end_year"]].append(row)
    years = sorted(by_year)
    if not years:
        return [], {"target": {}, "selected": {}}
    year_order = sorted(years, key=lambda year: (seeded_rank(seed, "year-balance", class_name, year), year))
    base, extra = divmod(count, len(years))
    quota = {year: base + (1 if index < extra else 0) for index, year in enumerate(year_order)}
    selected = []
    per_year = Counter()

    def eligible_in(year):
        return sorted(
            (row for row in by_year[year] if row["cik"] not in used_ciks),
            key=lambda row: (seeded_rank(seed, "missing-primary", class_name,
                                         row["symbol"], row["fy"], row["q4_end"]), row["symbol"]),
        )

    for year in year_order:
        for row in eligible_in(year)[:quota[year]]:
            if row["cik"] in used_ciks:
                continue
            selected.append(row)
            used_ciks.add(row["cik"])
            per_year[year] += 1

    # Any cell deficit caused by overlapping issuer CIKs is redistributed to the least represented years.
    while len(selected) < count:
        available = []
        for year in years:
            for row in eligible_in(year):
                available.append((per_year[year], seeded_rank(seed, "missing-redistribute", class_name,
                                                               year, row["symbol"], row["q4_end"]), row))
        if not available:
            break
        _, _, chosen = min(available, key=lambda item: (item[0], item[1], item[2]["symbol"]))
        selected.append(chosen)
        used_ciks.add(chosen["cik"])
        per_year[chosen["period_end_year"]] += 1
    return selected, {"target": {str(k): v for k, v in quota.items()},
                      "selected": {str(k): per_year[k] for k in sorted(per_year)}}


def _control_candidates(answer_set, issuers, finance_cache, seed):
    # Project only the row identity fields needed for selection; EPS/reference fields are ignored here.
    source_rows = answer_set.get("rows") or []
    keys = sorted({row.get("key") for row in source_rows if row.get("key")})
    key_index = {key: index for index, key in enumerate(keys)}
    result, rejected = [], Counter()
    for answer in source_rows:
        symbol = answer.get("key")
        index = key_index.get(symbol, -1)
        if index % 8 not in CONTROL_SHARDS:
            continue
        issuer = issuers.get(symbol)
        if not issuer:
            rejected["no_current_single_cik_mapping"] += 1
            continue
        record = period_from_local(symbol, answer.get("fy"), answer.get("end"), issuer, finance_cache)
        if not record:
            rejected["no_unique_sec_q4_and_annual_period"] += 1
            continue
        record.update({
            "answer_key_index": index,
            "answer_key_mod_8": index % 8,
            "selection_rank": seeded_rank(seed, "control-period", symbol, answer.get("fy"), answer.get("end")),
        })
        result.append(record)
    return result, rejected, len(keys)


def make_sample(seed=SEED):
    classification_path = ROOT / "docs/report/q4-eps-missing-classification.json"
    answer_set_path = ROOT / "docs/report/q4-8k/answer-set.json"
    progress_path = ROOT / "docs/report/fundamentals/us-market-progress-summary.json"
    snapshot_path = ROOT / "docs/report/fundamentals/us-market-snapshot.json"
    classification = load_json(classification_path)
    # Only projected control identity fields are consulted. The answer EPS is not read or emitted by this stage.
    answer_set = load_json(answer_set_path)
    issuers = issuer_index()
    finance_cache = {}
    candidates, missing_rejected = missing_candidates(classification, issuers, finance_cache, seed)
    used_ciks = set()
    selected_companies = []
    class_strata = {}
    for klass in MISSING_CLASS_ORDER:
        selected, counts = _pick_primary_rows(klass, candidates.get(klass, []), used_ciks, seed, 15)
        if len(selected) != 15:
            raise RuntimeError(f"cannot freeze 15 unique companies for {klass}: got {len(selected)}")
        class_strata[klass] = counts
        for first in selected:
            ticker = first["symbol"]
            same_company = [row for row in candidates[klass]
                            if row["symbol"] == ticker and (row["fy"], row["q4_end"]) != (first["fy"], first["q4_end"])]
            same_company.sort(key=lambda row: (
                row["period_end_year"] == first["period_end_year"],
                seeded_rank(seed, "missing-secondary", klass, ticker, row["fy"], row["q4_end"]),
            ))
            periods = [{**first, "sample_period_role": "primary"}]
            if same_company:
                periods.append({**same_company[0], "sample_period_role": "secondary"})
            periods.sort(key=lambda row: (row["fy"], row["q4_end"]))
            selected_companies.append({
                "symbol": ticker,
                "cik": first["cik"],
                "company_name": first.get("company_name"),
                "sic": first.get("sic"),
                "sic_description": first.get("sic_description"),
                "class": klass,
                "company_selection_rule": "fixed-seed balanced period-end year, then SHA-256 rank; unique CIK across the missing-company sample",
                "periods": [{key: value for key, value in row.items() if key not in {
                    "company_name", "sic", "sic_description", "exchange", "symbol", "cik",
                    "local_quarter_eps_diluted",
                }} for row in periods],
            })

    control_rows, control_rejected, total_keys = _control_candidates(answer_set, issuers, finance_cache, seed)
    best_by_ticker = {}
    for row in control_rows:
        current = best_by_ticker.get(row["symbol"])
        rank = (row["selection_rank"], row["fy"], row["q4_end"])
        if current is None or rank < (current["selection_rank"], current["fy"], current["q4_end"]):
            best_by_ticker[row["symbol"]] = row
    ranked_controls = sorted(best_by_ticker.values(), key=lambda row: (
        seeded_rank(seed, "control-company", row["symbol"]), row["symbol"], row["selection_rank"],
    ))
    controls = ranked_controls[:60]
    if len(controls) < 60:
        raise RuntimeError(f"cannot freeze 60 unique control companies from eligible shard keys: got {len(controls)}")

    missing_period_count = sum(len(row["periods"]) for row in selected_companies)
    selected_financials = sorted({
        row["financials_path"] for company in selected_companies for row in company["periods"]
    } | {row["financials_path"] for row in controls})
    payload = {
        "schema_version": 1,
        "sample_id": "sec-q4-eps-stage2-60x60-20261018",
        "base_commit": git("rev-parse", "origin/main"),
        "selection_seed": seed,
        "selection_frozen_before_sec_requests": True,
        "selection_rules": {
            "missing_companies": "60 unique current US companies; exactly 15 per main classification class; FY 2012–2025; one primary missing period per company balanced over available period-end years, then SHA-256(seed|scope|...); add at most one additional missing period for that selected company",
            "period_end_year_scope": "calendar year of local Q4 end; available years are recorded in strata and may include 2026 where FY 2025 ends in 2026",
            "control_periods": "60 unique tickers with one Q4 period each; answer-set rows whose key index in sorted unique keys has mod 8 in {4,5,6,7}; deterministic SHA-256(seed|control-company|ticker) ordering",
            "control_value_firewall": "selection stores no answer-set EPS, stored value, net income, filing accession, or filing date; answer-set values are read only by the post-extraction comparison command",
            "local_eligibility": "period must match one SEC-sourced quarterly row with fq=4 and one SEC-sourced annual period for the same FY; no financials file is modified",
            "selection_seed": seed,
        },
        "input_hashes": {
            "classification": {"path": str(classification_path.relative_to(ROOT)), "sha256": sha256_file(classification_path)},
            "answer_set_identity_source": {"path": str(answer_set_path.relative_to(ROOT)), "sha256": sha256_file(answer_set_path),
                                           "value_fields_used_for_selection": False},
            "progress_summary": {"path": str(progress_path.relative_to(ROOT)), "sha256": sha256_file(progress_path)},
            "market_snapshot": {"path": str(snapshot_path.relative_to(ROOT)), "sha256": sha256_file(snapshot_path)},
            "financials_files": [{"path": path, "sha256": sha256_file(ROOT / path)} for path in selected_financials],
        },
        "selection_audit": {
            "main_classification_rows": len(classification.get("rows", [])),
            "missing_candidate_periods_by_class": {klass: len(candidates.get(klass, [])) for klass in MISSING_CLASS_ORDER},
            "missing_candidate_rows_rejected": dict(sorted(missing_rejected.items())),
            "class_period_end_year_primary_strata": class_strata,
            "missing_company_count": len(selected_companies),
            "missing_period_count": missing_period_count,
            "answer_set_sorted_unique_keys": total_keys,
            "control_key_indices_selected": sorted(CONTROL_SHARDS),
            "control_candidate_periods_after_local_validation": len(control_rows),
            "control_candidate_rows_rejected": dict(sorted(control_rejected.items())),
            "control_unique_companies_available": len(best_by_ticker),
            "control_period_count": len(controls),
        },
        "missing_companies": selected_companies,
        "controls": [{key: value for key, value in row.items() if key not in {
            "company_name", "sic", "sic_description", "exchange", "symbol", "cik",
            "local_quarter_eps_diluted",
        }} | {
            "symbol": row["symbol"], "cik": row["cik"], "company_name": row.get("company_name"),
            "sic": row.get("sic"), "sic_description": row.get("sic_description"),
        } for row in controls],
    }
    payload["freeze_sha256"] = canonical_sha256(payload)
    return payload


def write_frozen_sample(output=STAGE / "sample.json", seed=SEED):
    output = Path(output)
    if not output.is_absolute():
        output = ROOT / output
    if output.exists():
        raise FileExistsError(f"refusing to replace an existing frozen sample: {output}")
    sample = make_sample(seed)
    dump_json(output, sample)
    (output.parent / "sample.sha256").write_text(f"{sha256_file(output)}  sample.json\n", encoding="ascii")
    return sample


def check_sample(sample):
    if sample.get("freeze_sha256") != canonical_sha256(sample):
        raise ValueError("sample freeze hash mismatch")
    if len(sample.get("missing_companies", [])) != 60 or len(sample.get("controls", [])) != 60:
        raise ValueError("sample must contain 60 missing companies and 60 control periods")
    if len({row["cik"] for row in sample["missing_companies"]}) != 60:
        raise ValueError("missing company sample contains repeated CIKs")
    if any(len(row.get("periods", [])) not in (1, 2) for row in sample["missing_companies"]):
        raise ValueError("missing sample periods per company must be one or two")


class RequestCapReached(RuntimeError):
    pass


class SEC403(RuntimeError):
    pass


class GuardedOpener:
    """Counts every wire attempt and blocks before exceeding the task's hard cap."""
    def __init__(self, cap=MAX_REQUESTS, *, ledger_path=STAGE / "source-ledger.jsonl", sleeper=None):
        import time
        self.cap = cap
        self.ledger_path = Path(ledger_path)
        self.sleeper = sleeper or time.sleep
        self.run_start_attempts = 0
        self.attempts = 0
        self.last_request_at = None
        if self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("record_type") == "request_attempt":
                    self.attempts += 1
                    self.last_request_at = item.get("requested_at") or self.last_request_at
                elif item.get("record_type") is None:
                    self.attempts += int(item.get("network_attempts_in_this_fetch", 0) or 0)
            self.run_start_attempts = self.attempts

    def __call__(self, request, timeout=60):
        if self.attempts >= self.cap:
            raise RequestCapReached(f"SEC request cap reached ({self.cap})")
        now = datetime.now(timezone.utc)
        if self.last_request_at:
            try:
                elapsed = (now - datetime.fromisoformat(self.last_request_at.replace("Z", "+00:00"))).total_seconds()
            except (TypeError, ValueError):
                elapsed = 1.0
            wait = 0.25 - elapsed
            if wait > 0:
                self.sleeper(wait)
                now = datetime.now(timezone.utc)
        self.attempts += 1
        self.last_request_at = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "record_type": "request_attempt",
                "request_number_cumulative": self.attempts,
                "request_number_this_run": self.attempts - self.run_start_attempts,
                "requested_at": self.last_request_at,
                "url": request.full_url,
                "user_agent_identified": True,
            }, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
        return urllib.request.urlopen(request, timeout=timeout)


class Stage2SecClient(poc.SecClient):
    def __init__(self, *, cache_dir, opener, sleeper=None, user_agent=None):
        import time
        super().__init__(cache_dir=cache_dir, opener=opener, sleeper=sleeper or time.sleep,
                         user_agent=user_agent or poc.SEC_DEFAULT_USER_AGENT)
        self.guard = opener
        self.run_id = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.ledger = []
        self.responses = {}
        self.first_403 = None

    def get(self, url, *, context):
        if url in self.responses:
            return self.responses[url]
        before = self.guard.attempts
        response = self.fetch(url)
        record = {
            "record_type": "response",
            "run_id": self.run_id,
            "url": url,
            "context": context,
            "http_status": response.get("status"),
            "retrieved_at": response.get("retrieved_at"),
            "cache_hit": bool(response.get("cache_hit")),
            "attempts": response.get("attempts", 0),
            "network_attempts_in_this_fetch": self.guard.attempts - before,
            "decoded_bytes": len(response.get("body", b"")),
            "wire_bytes": response.get("wire_bytes", 0),
            "sha256": sha256_bytes(response.get("body", b"")),
            "error": response.get("error"),
        }
        self.ledger.append(record)
        self.responses[url] = response
        if response.get("status") == 403:
            self.first_403 = {"url": url, "retrieved_at": response.get("retrieved_at")}
            raise SEC403(f"stopped on first SEC 403 at {url}")
        return response

    def metrics(self):
        return {
            "network_request_attempts": self.guard.attempts - self.guard.run_start_attempts,
            "cumulative_network_request_attempts": self.guard.attempts,
            "decoded_bytes_received": self.bytes_received,
            "wire_bytes_received": self.wire_bytes_received,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
            "rate_limit_max_requests_per_second": 4,
            "request_cap": self.guard.cap,
            "user_agent": self.user_agent,
        }


def append_ledger(client, path=STAGE / "source-ledger.jsonl"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for entry in client.ledger:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    client.ledger.clear()


def write_extraction_checkpoint(payload, client, path=STAGE / "extraction.json"):
    payload["metrics"] = client.metrics()
    dump_json(path, payload)
    append_ledger(client)


def _filing_rows(payload):
    filings = []
    sections = [((payload.get("filings") or {}).get("recent") or {}, None)]
    for old in ((payload.get("filings") or {}).get("files") or []):
        sections.append((old, old.get("name")))
    for section, filename in sections:
        forms = section.get("form") or []
        for index, form in enumerate(forms):
            if form not in {"10-K", "10-K/A", "8-K", "8-K/A"}:
                continue
            def at(field):
                values = section.get(field) or []
                return values[index] if index < len(values) else None
            filings.append({
                "form": form,
                "filing_date": at("filingDate"),
                "report_date": at("reportDate"),
                "accession": at("accessionNumber"),
                "primary_document": at("primaryDocument"),
                "is_xbrl": at("isXBRL"),
                "is_inline_xbrl": at("isInlineXBRL"),
                "submissions_file": filename or "recent",
            })
    unique = {}
    for filing in filings:
        if filing.get("accession"):
            unique[filing["accession"]] = filing
    return list(unique.values())


def _filing_base(cik, accession):
    accession_path = str(accession).replace("-", "")
    return f"{ARCHIVE_ROOT}/{int(cik)}/{accession_path}/"


def _within_120_days(filing_date, end):
    try:
        delta = (date.fromisoformat(filing_date) - date.fromisoformat(end)).days
    except (TypeError, ValueError):
        return False
    return 0 <= delta <= 120


def resolve_issuer_filings(client, issuer, periods, payload):
    cik = issuer["cik"]
    filings = _filing_rows(payload)
    target_ends = {row["local_annual_report_end"] for row in periods}
    target_end_dates = [date.fromisoformat(value) for value in target_ends if value]
    known = {row["accession"] for row in filings}
    older_files = (payload.get("filings") or {}).get("files") or []
    for older in older_files:
        start, finish = older.get("filingFrom"), older.get("filingTo")
        if not start or not finish:
            continue
        try:
            start_day, finish_day = date.fromisoformat(start), date.fromisoformat(finish)
        except ValueError:
            continue
        if not any(start_day <= end + timedelta(days=150) and finish_day >= end for end in target_end_dates):
            continue
        if older.get("name") in {None, "recent"}:
            continue
        url = f"https://data.sec.gov/submissions/{urllib.parse.quote(older['name'])}"
        response = client.get(url, context={"kind": "historical_submissions", "cik": cik,
                                            "submissions_file": older.get("name")})
        if response.get("status") != 200 or response.get("error"):
            continue
        try:
            old_payload = json.loads(response["body"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        for filing in _filing_rows({"filings": {"recent": old_payload}}):
            if filing["accession"] not in known:
                filings.append(filing)
                known.add(filing["accession"])
    return filings


def _month_day_years(text):
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    found = []
    for month_name, month in months.items():
        pattern = re.compile(rf"\b{month_name}\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(\d{{2,4}})\b", re.I)
        for match in pattern.finditer(text):
            year = int(match.group(2))
            if year < 100:
                year += 2000 if year < 70 else 1900
            found.append((year, month, int(match.group(1))))
    for match in re.finditer(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text):
        found.append((int(match.group(1)), int(match.group(2)), int(match.group(3))))
    return found


def _grid_rows(table):
    """Expand simple colspan/rowspan financial table cells into logical columns."""
    from lxml import html as lxml_html
    rows = []
    active = {}
    for tr in table.xpath(".//tr"):
        row = []
        col = 0
        while col in active:
            text, remain = active.pop(col)
            row.append(text)
            if remain > 1:
                active[col] = (text, remain - 1)
            col += 1
        cells = [cell for cell in tr.xpath("./th|./td")]
        for cell in cells:
            while col in active:
                text, remain = active.pop(col)
                row.append(text)
                if remain > 1:
                    active[col] = (text, remain - 1)
                col += 1
            text = poc.html_plain_text(cell)
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except ValueError:
                colspan, rowspan = 1, 1
            for offset in range(colspan):
                row.append(text)
                if rowspan > 1:
                    active[col + offset] = (text, rowspan - 1)
            col += colspan
        if row:
            rows.append(row)
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def _parse_money(value):
    value = (value or "").strip().replace("\u00a0", " ")
    if value in {"", "—", "–", "-", "N/A"}:
        return None
    value = value.replace("$", "").replace(",", "").strip()
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1].strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
        return None
    try:
        result = Decimal(value)
        return float(-result if negative else result)
    except InvalidOperation:
        return None


def table_eps_candidates(body, target, *, source_url, filing, split_events):
    """Extract only direct table rows with explicit diluted EPS and a single, dated three-month column."""
    from lxml import html as lxml_html
    try:
        root = lxml_html.fromstring(body)
    except (ValueError, TypeError):
        return []
    candidates = []
    for table in root.xpath("//table"):
        table_text = poc.html_plain_text(table)
        if not re.search(r"per\s+(?:(?:basic|diluted|fully)\s+)*(?:and\s+(?:basic|diluted)\s+)?(?:common\s+)?share", table_text, re.I):
            continue
        adjusted = bool(re.search(r"adjusted|non.?gaap", table_text, re.I))
        grid = _grid_rows(table)
        for row_index, row in enumerate(grid):
            label = row[0] if row else ""
            if not re.search(r"diluted", label, re.I) or not re.search(
                    r"per\s+(?:(?:basic|diluted|fully)\s+)*(?:and\s+(?:basic|diluted)\s+)?(?:common\s+)?share", label, re.I):
                continue
            if re.search(r"adjusted|non.?gaap|basic\s+earnings?\s+per\s+share\s+only", label, re.I):
                continue
            # Continuing/discontinued operations EPS is not the total GAAP EPS requested.
            # Require an explicit total net income/loss or earnings row label.
            if re.search(r"continuing\s+operations|discontinued\s+operations|segment", label, re.I):
                continue
            if not re.search(r"\b(?:net\s+income|net\s+loss|net\s+earnings|earnings)\b", label, re.I):
                continue
            headers = [" ".join(grid[h][col].strip() for h in range(row_index) if col < len(grid[h]) and grid[h][col].strip())
                       for col in range(len(row))]
            matches = []
            for col, heading in enumerate(headers):
                heading_lower = heading.lower()
                dates = _month_day_years(heading)
                if ((target["q4_end"] in heading or tuple(int(part) for part in target["q4_end"].split("-")) in dates)
                        and any(item[0] == int(target["q4_end"][:4]) and
                                f"{item[1]:02d}-{item[2]:02d}" == target["q4_end"][5:] for item in dates)
                        and re.search(r"three\s+months|three-month|quarter", heading_lower)
                        and not re.search(r"twelve\s+months|year\s+ended|fiscal\s+year", heading_lower)):
                    matches.append(col)
            if len(matches) != 1:
                continue
            column = matches[0]
            value = _parse_money(row[column]) if column < len(row) else None
            if value is None:
                continue
            gaap_context = bool(re.search(r"\bGAAP\b|consolidated\s+(?:statements?|statement)\s+of\s+(?:operations|income|earnings)", table_text, re.I))
            common_scope = bool(re.search(r"common\s+share", label, re.I))
            consolidated = bool(re.search(r"consolidated", table_text, re.I))
            class_unclear = bool(re.search(r"class\s+[A-Z]|class of common", table_text, re.I))
            issues = []
            if adjusted:
                issues.append("non_gaap_context_possible")
            if not gaap_context:
                issues.append("gaap_statement_basis_unverified")
            if not common_scope:
                issues.append("share_class_unresolved")
            if not consolidated:
                issues.append("consolidated_scope_unverified")
            if class_unclear:
                issues.append("share_class_dimension_unresolved")
            if not source_url.startswith("https://www.sec.gov/Archives/"):
                issues.append("source_identity_unverified")
            row_xpath = table.getroottree().getpath(table.xpath(".//tr")[row_index])
            candidates.append({
                "classification": "A_verified_direct_q4_eps" if not issues else "B_candidate_needs_verification",
                "verified": not issues,
                "value": value,
                "concept": None,
                "row_text": " | ".join(row),
                "table_text": table_text[:6000],
                "context": {"start": target.get("q4_start_estimate"), "end": target["q4_end"],
                            "duration_basis": "explicit three-month fiscal-Q4 column"},
                "locator": {"table_xpath": table.getroottree().getpath(table),
                            "row_xpath": row_xpath, "column_index": column, "column_header": headers[column]},
                "gaap_evidence": "GAAP or consolidated statement label in this source table" if gaap_context else None,
                "consolidated_scope_evidence": "consolidated statement/table heading" if consolidated else None,
                "share_class_evidence": "common share stated in the EPS row" if common_scope else None,
                "diluted": True,
                "split_basis": split_basis(split_events, filing.get("filing_date"), target["q4_end"]),
                "issues": issues,
                "filing": filing,
                "source_url": source_url,
                "source_location": row_xpath,
            })
    return candidates


def split_basis(events, filing_date, q4_end):
    factor = 1.0
    relevant = []
    for event in events or []:
        try:
            event_date, ratio = str(event[0]), float(event[1])
        except (TypeError, ValueError, IndexError):
            continue
        if ratio <= 0:
            continue
        if event_date > q4_end:
            relevant.append({"date": event_date, "ratio": ratio,
                             "after_filing": bool(filing_date and event_date > filing_date)})
        if filing_date and event_date > filing_date:
            factor *= ratio
    return {
        "value_basis": "as-reported in the cited SEC filing",
        "future_split_factor_after_filing": factor,
        "events_after_period_end": relevant,
        "reviewed": True,
    }


def fact_eps_candidates(parsed, target, *, source_url, filing, split_events):
    candidates = []
    for fact in parsed.get("eps_facts", []):
        concept = re.sub(r"[^a-z0-9]", "", (fact.get("concept") or "").lower())
        if not re.search(r"eps|earning.*pershare|pershare|dilutedshare|basicanddilutedshare", concept):
            continue
        row_text = fact.get("row_text") or ""
        table_text = fact.get("table_text") or ""
        text = f"{row_text} {table_text}"
        # Total diluted EPS concepts are distinct from continuing/discontinued
        # operations EPS. Custom tags need explicit total-EPS wording in the row.
        if re.search(r"continuingoperations|discontinuedoperations|segment", concept, re.I):
            total_eps_basis = False
        else:
            total_eps_basis = bool(
                re.fullmatch(r"usgaapearningspersharediluted|usgaapearningspersharebasicanddiluted", concept)
                or re.search(r"\b(?:net\s+income|net\s+loss|net\s+earnings|earnings)\b", row_text, re.I)
            )
        label_diluted = bool(re.search(r"diluted", row_text, re.I) and
                             re.search(r"per\s+(?:(?:basic|diluted|fully)\s+)*(?:and\s+(?:basic|diluted)\s+)?(?:common\s+)?share", row_text, re.I))
        basic_only_concept = "basic" in concept and "diluted" not in concept
        assessed_fact = fact
        if label_diluted and "diluted" not in concept and not basic_only_concept:
            assessed_fact = {**assessed_fact, "concept": f"{fact.get('concept') or ''}:DilutedLabelEvidence"}
        if not concept.startswith("usgaap") and re.search(
                r"consolidated\s+(?:statements?|statement)\s+of\s+(?:operations|income|earnings)|\bGAAP\b", text, re.I):
            assessed_fact = {**assessed_fact, "gaap_evidence": "SEC filing's consolidated statement table"}
        assessment = poc.assess_inline_eps_fact(assessed_fact, target)
        dimensions = (fact.get("context") or {}).get("dimensions") or []
        consolidated_scope = bool(re.search(r"consolidated", text, re.I) or
                                  (filing.get("form") in {"10-K", "10-K/A"} and not dimensions))
        share_class_evidence = ("explicit common-share row label" if re.search(r"common\s+share", row_text, re.I)
                                else "US-GAAP EPS concept without a stock-class dimension"
                                if concept.startswith("usgaap") and not dimensions else None)
        extra_issues = list(assessment["issues"])
        if not consolidated_scope:
            extra_issues.append("consolidated_scope_unverified")
        if not share_class_evidence and not any(re.search(r"class|stock|share", str(item.get("axis", "")) + " " + str(item.get("member", "")), re.I)
                                               for item in dimensions):
            extra_issues.append("share_class_unresolved")
        if not total_eps_basis:
            extra_issues.append("total_net_income_eps_basis_unverified")
        # Any XBRL dimension can restrict a fact to a segment or share class.
        # The sample does not carry a company-specific class/segment map, so do
        # not accept a dimensioned fact automatically.
        if dimensions:
            extra_issues.append("dimensioned_eps_scope_unresolved")
        assessment["issues"] = sorted(set(extra_issues))
        assessment["verified"] = not assessment["issues"]
        assessment["classification"] = "A_verified_direct_q4_eps" if assessment["verified"] else "B_candidate_needs_verification"
        candidates.append({
            **assessment,
            "concept": fact.get("concept"),
            "context": fact.get("context"),
            "unit_measures": fact.get("unit_measures"),
            "row_text": fact.get("row_text"),
            "table_text": fact.get("table_text"),
            "locator": {"fact_xpath": fact.get("fact_xpath"), "row_xpath": fact.get("row_xpath")},
            "gaap_evidence": assessed_fact.get("gaap_evidence") or ("us-gaap taxonomy concept" if concept.startswith("usgaap") else None),
            "consolidated_scope_evidence": "consolidated filing/table basis" if consolidated_scope else None,
            "share_class_evidence": share_class_evidence,
            "diluted_evidence": "diluted taxonomy concept" if "diluted" in concept else "explicit diluted row label",
            "split_basis": split_basis(split_events, filing.get("filing_date"), target["q4_end"]),
            "filing": filing,
            "source_url": source_url,
            "source_location": fact.get("fact_xpath"),
        })
    return candidates


def parse_source_document(body, target, filing, source_url, split_events):
    parsed = poc.parse_inline_filing(body)
    facts = fact_eps_candidates(parsed, target, source_url=source_url, filing=filing, split_events=split_events)
    tables = table_eps_candidates(body, target, source_url=source_url, filing=filing, split_events=split_events)
    return facts + tables


def _classify_candidates(candidates):
    direct = [row for row in candidates if row.get("verified") and row.get("value") is not None]
    if direct:
        by_version = []
        for row in direct:
            factor = (row.get("split_basis") or {}).get("future_split_factor_after_filing", 1.0)
            current = float(row["value"]) / factor if factor else None
            by_version.append((row, current))
        values = [value for _, value in by_version if value is not None]
        if values and max(values) - min(values) > 0.005:
            return "B_candidate_needs_verification", None, "direct_filing_version_conflict", direct
        chosen, _ = max(by_version, key=lambda pair: (
            pair[0].get("filing", {}).get("filing_date") or "",
            pair[0].get("filing", {}).get("form", ""),
            pair[0].get("source_url", ""),
        ))
        return "A_verified_direct_q4_eps", chosen, None, direct
    if candidates:
        annual = [row for row in candidates if "not_single_quarter_duration" in row.get("issues", [])]
        if annual:
            return "C_derived_only", None, "only_annual_or_nonquarter_eps_candidate", candidates
        return "B_candidate_needs_verification", None, "direct_candidate_needs_review", candidates
    return None, None, None, []


def _url_doc(client, url, *, context, target, filing, split_events, response_records):
    response = client.get(url, context=context)
    response_records.append({
        "url": url,
        "http_status": response.get("status"),
        "cache_hit": bool(response.get("cache_hit")),
        "bytes": len(response.get("body", b"")),
        "sha256": sha256_bytes(response.get("body", b"")),
        "error": response.get("error"),
    })
    if response.get("status") != 200 or response.get("error"):
        return response, [], "technical_failure"
    if poc.is_nontext_payload(url, response["body"], response.get("headers", {}).get("content-type")):
        return response, [], "unsupported_nontext"
    try:
        parsed = parse_source_document(response["body"], target, filing, url, split_events)
    except Exception as error:  # retain real body hash and distinguish parser failure from no disclosure
        return response, [], f"parser_failure: {type(error).__name__}: {error}"
    return response, parsed, "parsed"


def inspect_eight_k(client, filing, target, issuer, response_records):
    accession = filing["accession"]
    base = _filing_base(issuer["cik"], accession)
    index_url = base + "index.json"
    index_response = client.get(index_url, context={"kind": "eight_k_index", "symbol": issuer["symbol"],
                                                    "accession": accession})
    response_records.append({"url": index_url, "http_status": index_response.get("status"),
                             "cache_hit": bool(index_response.get("cache_hit")),
                             "bytes": len(index_response.get("body", b"")),
                             "sha256": sha256_bytes(index_response.get("body", b"")),
                             "error": index_response.get("error")})
    if index_response.get("status") != 200 or index_response.get("error"):
        return [], "technical_failure", []
    try:
        payload = json.loads(index_response["body"].decode("utf-8"))
        items = (payload.get("directory") or {}).get("item") or []
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return [], "parser_failure", []
    primary = filing.get("primary_document")
    exhibits = []
    for item in items:
        name = item.get("name")
        if not name or name == primary:
            continue
        if poc.likely_results_exhibit(name, item.get("type"), item.get("description")):
            exhibits.append(item)
    exhibits.sort(key=lambda item: (
        0 if str(item.get("type") or "").upper().startswith("EX-99.1") or
        re.search(r"ex(?:hibit)?99[._-]?1", item.get("name", ""), re.I) else 1,
        str(item.get("name", "")),
    ))
    docs = []
    seen = set()
    candidates = []
    failure = None
    for item in exhibits:
        name = item["name"]
        if name in seen:
            continue
        seen.add(name)
        url = base + urllib.parse.quote(name)
        response, parsed, status = _url_doc(client, url, context={"kind": "eight_k_exhibit", "symbol": issuer["symbol"],
                                                                  "accession": accession, "document": name},
                                            target=target, filing=filing,
                                            split_events=target.get("split_events") or [], response_records=response_records)
        docs.append({"url": url, "name": name, "status": status,
                     "http_status": response.get("status"), "sha256": sha256_bytes(response.get("body", b""))})
        candidates.extend(parsed)
        if status not in {"parsed"}:
            failure = status
    if primary and primary not in seen:
        url = base + urllib.parse.quote(primary)
        response, parsed, status = _url_doc(client, url, context={"kind": "eight_k_primary", "symbol": issuer["symbol"],
                                                                  "accession": accession, "document": primary},
                                            target=target, filing=filing,
                                            split_events=target.get("split_events") or [], response_records=response_records)
        docs.append({"url": url, "name": primary, "status": status,
                     "http_status": response.get("status"), "sha256": sha256_bytes(response.get("body", b""))})
        candidates.extend(parsed)
        if status != "parsed":
            failure = status
        if status == "parsed":
            links = poc.linked_results_exhibits(response["body"], items)
            for item in links:
                name = item["name"]
                if name in seen:
                    continue
                seen.add(name)
                linked_url = base + urllib.parse.quote(name)
                linked_response, linked_parsed, linked_status = _url_doc(
                    client, linked_url, context={"kind": "eight_k_linked_exhibit", "symbol": issuer["symbol"],
                                                  "accession": accession, "document": name},
                    target=target, filing=filing, split_events=target.get("split_events") or [],
                    response_records=response_records,
                )
                docs.append({"url": linked_url, "name": name, "status": linked_status,
                             "http_status": linked_response.get("status"),
                             "sha256": sha256_bytes(linked_response.get("body", b""))})
                candidates.extend(linked_parsed)
                if linked_status != "parsed":
                    failure = linked_status
    return candidates, failure, docs


def target_rows(sample):
    rows = []
    for company in sample["missing_companies"]:
        for period in company["periods"]:
            rows.append({
                **period,
                "symbol": company["symbol"], "cik": company["cik"], "company_name": company["company_name"],
                "sic": company.get("sic"), "sic_description": company.get("sic_description"),
                "sample_role": "missing", "classification_class": company["class"],
            })
    for control in sample["controls"]:
        rows.append({
            **control,
            "sample_role": "control", "classification_class": None,
        })
    return rows


def _empty_period_result(target):
    item = {key: target.get(key) for key in (
        "symbol", "cik", "company_name", "fy", "q4_end", "period_end_year", "q4_start_estimate",
        "local_annual_report_end", "classification_class", "sample_role", "classification_detail",
    )}
    item.update({"status": "pending", "source_d": {"status": "not_started", "filings": [], "candidates": []},
                 "source_c": {"status": "not_started", "filings": [], "candidates": []},
                 "accepted": None, "candidates": []})
    return item


def _mark_pending_as_technical_failure(payload, reason):
    for item in payload.get("period_results", []):
        if item.get("status") == "pending":
            item.update({"status": "E_technical_failure", "technical_reason": reason})


def run_extraction(sample_path=STAGE / "sample.json", output_path=STAGE / "extraction.json"):
    sample_path = Path(sample_path)
    if not sample_path.is_absolute():
        sample_path = ROOT / sample_path
    commit = assert_committed(sample_path.relative_to(ROOT))
    sample = load_json(sample_path)
    check_sample(sample)
    sample_sidecar = sample_path.parent / "sample.sha256"
    if not sample_sidecar.exists() or sample_sidecar.read_text(encoding="ascii").split()[0] != sha256_file(sample_path):
        raise RuntimeError("sample.sha256 does not match the committed sample.json")
    assert_committed(sample_sidecar.relative_to(ROOT))
    targets = target_rows(sample)
    periods_by_cik = defaultdict(list)
    for row in targets:
        periods_by_cik[row["cik"]].append(row)
    payload = {
        "schema_version": 1,
        "sample_sha256": sha256_file(sample_path),
        "sample_freeze_sha256": sample["freeze_sha256"],
        "sample_commit": commit,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sources": ["8-K/8-K-A results exhibits (EX-99.1 first)", "10-K/10-K-A original filings"],
        "fsds_zip_datasets_downloaded": False,
        "complete": False,
        "stopped_reason": None,
        "period_results": [],
        "skipped_fiscal_period_mismatches": [],
        "unresolved_period_mappings": [],
        "metrics": {},
        # Pre-populate every frozen period before the first SEC request so a
        # transport stop still reports the complete sample denominator.
        "period_results": [_empty_period_result(target) for target in targets],
    }
    output_path = Path(output_path)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    cache_dir = Path("/tmp/sec-q4-eps-stage2-cache")
    opener = GuardedOpener(MAX_REQUESTS)
    client = Stage2SecClient(cache_dir=cache_dir, opener=opener)
    state_path = Path("/tmp/sec-q4-eps-stage2-run-state.json")
    try:
        # Resolve filings for every sampled issuer first; this call is metadata-only, not EPS extraction.
        issuer_filings = {}
        for cik, rows in sorted(periods_by_cik.items()):
            issuer = rows[0]
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = client.get(url, context={"kind": "submissions", "cik": cik,
                                                "symbols": sorted({row['symbol'] for row in rows})})
            append_ledger(client)
            if response.get("status") != 200 or response.get("error"):
                issuer_filings[cik] = {"filings": [], "status": "technical_failure",
                                       "error": response.get("error"), "http_status": response.get("status")}
                continue
            try:
                submission = json.loads(response["body"].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                issuer_filings[cik] = {"filings": [], "status": "parser_failure", "error": str(error)}
                continue
            filings = resolve_issuer_filings(client, issuer, rows, submission)
            append_ledger(client)
            issuer_filings[cik] = {"filings": filings, "status": "resolved", "name": submission.get("name")}

        for target, item in zip(targets, payload["period_results"]):
            write_extraction_checkpoint(payload, client, output_path)
            issuer_result = issuer_filings.get(target["cik"], {})
            if issuer_result.get("status") != "resolved":
                item["status"] = "E_technical_failure"
                item["technical_reason"] = "SEC submissions metadata unavailable or unparsable"
                continue
            filings = issuer_result["filings"]
            annual_filing = [row for row in filings if row.get("form") in {"10-K", "10-K/A"}
                             and row.get("report_date") == target.get("local_annual_report_end")]
            if not annual_filing:
                local_end = date.fromisoformat(target["local_annual_report_end"])
                near = []
                for row in filings:
                    if row.get("form") not in {"10-K", "10-K/A"} or not row.get("report_date"):
                        continue
                    try:
                        delta = abs((date.fromisoformat(row["report_date"]) - local_end).days)
                    except ValueError:
                        continue
                    if delta <= 7:
                        near.append(row)
                if len({row["report_date"] for row in near}) == 1:
                    annual_filing = near
            if not annual_filing:
                item["status"] = "E_technical_failure"
                item["technical_reason"] = "same-FY SEC 10-K report end could not be mapped from submissions"
                payload["unresolved_period_mappings"].append({"symbol": target["symbol"], "fy": target["fy"],
                                                               "q4_end": target["q4_end"],
                                                               "expected_local_annual_report_end": target.get("local_annual_report_end")})
                continue
            report_end = annual_filing[0]["report_date"]
            item["same_fy_sec_10k_report_end"] = report_end
            if target["q4_end"] != report_end:
                mismatch = {
                    "symbol": target["symbol"], "cik": target["cik"], "sample_role": target["sample_role"],
                    "class": target.get("classification_class"), "fy": target["fy"],
                    "local_q4_end": target["q4_end"], "same_fy_sec_10k_report_end": report_end,
                    "reason": "local Q4 end differs from same-FY SEC 10-K report end; excluded before EPS filings were fetched",
                }
                payload["skipped_fiscal_period_mismatches"].append(mismatch)
                item.update({"status": "skipped_fiscal_period_mismatch", "skip_reason": mismatch["reason"]})
                continue
            target_assessment = {
                "q4_end": target["q4_end"],
                "q4_start_estimate": target.get("q4_start_estimate"),
                "q4_end_matches_local_annual_end": target["q4_end"] == report_end,
                "split_events": target.get("split_events") or [],
            }
            scoped_8ks = [row for row in filings if row.get("form") in {"8-K", "8-K/A"}
                          and _within_120_days(row.get("filing_date"), report_end)]
            scoped_8ks.sort(key=lambda row: (row.get("filing_date") or "", row.get("form", ""), row.get("accession", "")))
            eightk_candidates, eightk_failures = [], []
            for filing in scoped_8ks:
                evidence, failure, docs = inspect_eight_k(client, filing, target_assessment, target, [])
                # The document URLs and hashes are sufficient provenance; response hashes are in source-ledger.jsonl.
                item["source_d"]["filings"].append({"filing": filing, "documents": docs, "status": failure or "parsed"})
                eightk_candidates.extend(evidence)
                if failure:
                    eightk_failures.append(failure)
                write_extraction_checkpoint(payload, client, output_path)
            item["source_d"]["candidates"] = eightk_candidates
            d_status, d_chosen, d_reason, d_candidates = _classify_candidates(eightk_candidates)
            item["source_d"]["status"] = d_status or ("searched" if not eightk_failures else "E_technical_failure")
            item["source_d"]["technical_failures"] = eightk_failures
            item["candidates"].extend(eightk_candidates)
            accepted = None
            if d_status == "A_verified_direct_q4_eps":
                accepted = d_chosen
                item["status"] = d_status
                item["accepted_source"] = "8-K/8-K-A"
            else:
                annual_filing.sort(key=lambda row: (row.get("filing_date") or "", row.get("form") == "10-K/A"))
                c_candidates, c_failures = [], []
                for filing in annual_filing:
                    if not filing.get("primary_document"):
                        c_failures.append("missing_primary_document")
                        continue
                    url = _filing_base(target["cik"], filing["accession"]) + urllib.parse.quote(filing["primary_document"])
                    response_records = []
                    response, evidence, status = _url_doc(
                        client, url, context={"kind": "ten_k", "symbol": target["symbol"],
                                              "accession": filing["accession"], "form": filing["form"]},
                        target=target_assessment, filing=filing, split_events=target.get("split_events") or [],
                        response_records=response_records,
                    )
                    item["source_c"]["filings"].append({"filing": filing, "url": url,
                                                        "http_status": response.get("status"), "status": status,
                                                        "sha256": sha256_bytes(response.get("body", b""))})
                    c_candidates.extend(evidence)
                    if status != "parsed":
                        c_failures.append(status)
                    write_extraction_checkpoint(payload, client, output_path)
                item["source_c"]["candidates"] = c_candidates
                c_status, c_chosen, c_reason, _ = _classify_candidates(c_candidates)
                item["source_c"]["status"] = c_status or ("searched" if not c_failures else "E_technical_failure")
                item["source_c"]["technical_failures"] = c_failures
                item["candidates"].extend(c_candidates)
                if c_status == "A_verified_direct_q4_eps":
                    accepted = c_chosen
                    item["status"] = c_status
                    item["accepted_source"] = "10-K/10-K-A"
                elif d_status == "B_candidate_needs_verification" or c_status == "B_candidate_needs_verification":
                    item["status"] = "B_candidate_needs_verification"
                    item["technical_reason"] = d_reason or c_reason
                elif d_status == "C_derived_only" or c_status == "C_derived_only":
                    item["status"] = "C_derived_only"
                    item["technical_reason"] = "only annual/non-quarter EPS facts were available; no subtraction was performed"
                elif eightk_failures or c_failures:
                    item["status"] = "E_technical_failure"
                    item["technical_reason"] = "one or more SEC documents could not be completely inspected"
                else:
                    item["status"] = "D_no_direct_disclosure_found"
                    item["technical_reason"] = "all scoped 8-K results exhibits and same-FY 10-K versions were fetched and parsed; no direct Q4 EPS found"
            if accepted:
                item["accepted"] = {
                    "source": item.get("accepted_source"),
                    "accession": (accepted.get("filing") or {}).get("accession"),
                    "form": (accepted.get("filing") or {}).get("form"),
                    "filing_date": (accepted.get("filing") or {}).get("filing_date"),
                    "fiscal_quarter": "Q4 single-quarter duration",
                    "fiscal_period_end": target["q4_end"],
                    "value_as_filed": accepted.get("value"),
                    "concept": accepted.get("concept"),
                    "unit_measures": accepted.get("unit_measures"),
                    "row_text": accepted.get("row_text"),
                    "table_text": accepted.get("table_text"),
                    "source_url": accepted.get("source_url"),
                    "source_location": accepted.get("source_location"),
                    "gaap_evidence": accepted.get("gaap_evidence"),
                    "consolidated_scope_evidence": accepted.get("consolidated_scope_evidence"),
                    "share_class_evidence": accepted.get("share_class_evidence"),
                    "diluted": accepted.get("diluted", True),
                    "diluted_evidence": accepted.get("diluted_evidence"),
                    "split_basis": accepted.get("split_basis"),
                }
            write_extraction_checkpoint(payload, client, output_path)

        payload["complete"] = True
        payload["stopped_reason"] = "completed"
    except SEC403 as error:
        payload["complete"] = False
        payload["stopped_reason"] = "first_http_403"
        payload["stop_detail"] = str(error)
        _mark_pending_as_technical_failure(
            payload, "SEC returned its first 403 before this period completed its search")
    except RequestCapReached as error:
        payload["complete"] = False
        payload["stopped_reason"] = "request_cap_reached"
        payload["stop_detail"] = str(error)
        _mark_pending_as_technical_failure(
            payload, "SEC request cap reached before this period completed its search")
    except Exception as error:
        payload["complete"] = False
        payload["stopped_reason"] = f"{type(error).__name__}: {error}"
        _mark_pending_as_technical_failure(
            payload, "extraction stopped before this period completed its search")
        raise
    finally:
        append_ledger(client)
        payload["metrics"] = client.metrics()
        payload["metrics"]["unique_sec_urls"] = len(client.responses)
        payload["source_ledger_entries"] = sum(1 for _ in (STAGE / "source-ledger.jsonl").open(encoding="utf-8")) \
            if (STAGE / "source-ledger.jsonl").exists() else 0
        dump_json(output_path, payload)
        dump_json(state_path, {"sample_sha256": sample.get("freeze_sha256"),
                               "complete": payload.get("complete"), "stopped_reason": payload.get("stopped_reason"),
                               "metrics": payload.get("metrics")})
    if not payload.get("complete"):
        print(json.dumps({"complete": False, "stopped_reason": payload.get("stopped_reason"),
                          "metrics": payload.get("metrics")}, ensure_ascii=False, indent=2))
    return payload


def render_report():
    sample_path = STAGE / "sample.json"
    extraction_path = STAGE / "extraction.json"
    comparison_path = STAGE / "controls-comparison.json"
    sample = load_json(sample_path)
    extraction = load_json(extraction_path)
    comparison = load_json(comparison_path)
    results = extraction.get("period_results", [])
    missing = [row for row in results if row.get("sample_role") == "missing"]
    controls = [row for row in results if row.get("sample_role") == "control"]
    classes = sorted({row.get("classification_class") for row in missing if row.get("classification_class")})
    years = sorted({row.get("period_end_year") for row in missing if row.get("period_end_year")})
    class_rows = []
    for klass in classes:
        group = [row for row in missing if row.get("classification_class") == klass]
        verified = sum(row.get("status") == "A_verified_direct_q4_eps" for row in group)
        excluded = sum(row.get("status") == "skipped_fiscal_period_mismatch" for row in group)
        class_rows.append(f"| `{klass}` | {len(group)} | {len(group)-excluded} | {excluded} | {verified} |")
    year_rows = []
    for year in years:
        group = [row for row in missing if row.get("period_end_year") == year]
        verified = sum(row.get("status") == "A_verified_direct_q4_eps" for row in group)
        excluded = sum(row.get("status") == "skipped_fiscal_period_mismatch" for row in group)
        year_rows.append(f"| {year} | {len(group)} | {len(group)-excluded} | {excluded} | {verified} |")
    mismatch_rows = [
        f"| {row.get('sample_role')} | {row.get('symbol')} | {row.get('class') or 'control'} | FY{row.get('fy')} | {row.get('local_q4_end')} | {row.get('same_fy_sec_10k_report_end')} |"
        for row in extraction.get("skipped_fiscal_period_mismatches", [])
    ]
    source_counts = {}
    for source in ("8-K/8-K-A", "10-K/10-K-A"):
        accepted = [row for row in results if row.get("accepted_source") == source]
        source_counts[source] = len(accepted)
    missing_den = len(missing)
    verified_total = sum(row.get("status") == "A_verified_direct_q4_eps" for row in missing)
    eligible_den = sum(row.get("status") != "skipped_fiscal_period_mismatch" for row in missing)
    metrics = extraction.get("metrics") or {}
    cmp = comparison.get("metrics") or {}
    content = [
        "# SEC Q4 diluted EPS stage-two evidence test",
        "",
        f"Sample: {sample['sample_id']} (seed `{sample['selection_seed']}`); sample freeze SHA-256 `{sample['freeze_sha256']}`.",
        f"Extraction SHA-256 `{comparison.get('extraction_sha256')}`; extraction commit `{comparison.get('extraction_commit')}`.",
        f"Run complete: **{extraction.get('complete')}**; stop reason: `{extraction.get('stopped_reason')}`.",
        "",
        "## Missing-period yield by class",
        "",
        "| Main class | Sampled periods | Eligible after fiscal-end check | Skipped mismatch | Verified direct Q4 EPS |",
        "|---|---:|---:|---:|---:|",
        *class_rows,
        "",
        "## Missing-period yield by period-end year",
        "",
        "| Period-end year | Sampled periods | Eligible after fiscal-end check | Skipped mismatch | Verified direct Q4 EPS |",
        "|---:|---:|---:|---:|---:|",
        *year_rows,
        "",
        f"Verified direct values: **{verified_total}/{missing_den} sampled missing periods ({verified_total/missing_den:.1%})**; excluding {missing_den-eligible_den} fiscal-end mismatches, **{verified_total}/{eligible_den} eligible periods ({verified_total/eligible_den:.1%})**." if missing_den and eligible_den else "Verified direct values: denominator unavailable.",
        "",
        "## Control-set comparison",
        "",
        f"Controls selected: {len(controls)}. SEC extraction accepted {cmp.get('accepted', 0)} direct values; {cmp.get('correct', 0)} matched the answer set within ±0.005; {cmp.get('sign_errors', 0)} sign errors; {cmp.get('unverifiable', 0)} not comparable because the extraction was not verified direct.",
        f"Accuracy among accepted controls: {cmp.get('accuracy_among_accepted', 'n/a')}.",
        "",
        "## Source contribution",
        "",
        "| SEC source | Unique missing periods with a verified direct value |",
        "|---|---:|",
        f"| 8-K/8-K-A results exhibits (EX-99.1 preferred) | {source_counts['8-K/8-K-A']} |",
        f"| 10-K/10-K-A fallback | {source_counts['10-K/10-K-A']} |",
        "| Cumulative unique periods | " + str(verified_total) + " |",
        "",
        "## Fiscal-period mismatches skipped",
        "",
        f"Skipped **{len(mismatch_rows)}** sampled periods before fetching EPS filing bodies because local Q4 end differs from the same-FY SEC 10-K report end.",
        "",
        "| Sample | Ticker | Class | FY | Local Q4 end | SEC 10-K report end |",
        "|---|---|---|---:|---|---|",
        *(mismatch_rows or ["| — | — | — | — | — | — |"]),
        "",
        "## Requests and bytes",
        "",
        f"SEC wire request attempts: **{metrics.get('network_request_attempts', 0)} / 12,000**; decoded bytes: **{metrics.get('decoded_bytes_received', 0)}**; wire bytes: **{metrics.get('wire_bytes_received', 0)}**; cache hits: **{metrics.get('cache_hits', 0)}**; retries: **{metrics.get('retries', 0)}**; configured limit: **4 requests/second**.",
        "SEC Financial Statement Data Sets and Financial Statement and Notes ZIP datasets were not downloaded. No request was sent to S3 shards 4–7.",
        "",
        "## Outcome definitions and limits",
        "",
        "A means a direct, as-filed, single-quarter Q4 value passed the EPS tag/label, diluted, GAAP, period-end, share-class/scope, and split-basis checks. B means a candidate remains unresolved. C means only an annual or non-quarter EPS fact was found; no subtraction was performed. D means the scoped filings were successfully searched with no direct Q4 disclosure. E means a technical failure or incomplete search. Fiscal-end mismatches are separately excluded before source documents are fetched.",
        "",
        "This is a fixed, stratified sample: 60 companies split 15 per missing class, with up to two periods per company, plus 60 answer-set controls from key-index shards 4–7. It is not a random sample of all 7,755 missing rows. Its yield must not be extrapolated to those rows or to the whole market.",
        "",
        "The extraction does not read answer-set values. The control comparison was generated only after the extraction file and SHA-256 were committed.",
        "",
    ]
    (STAGE / "report.md").write_text("\n".join(content), encoding="utf-8")
    return STAGE / "report.md"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-sample")
    freeze.add_argument("--seed", default=SEED)
    freeze.add_argument("--out", default=str(STAGE / "sample.json"))
    extract = sub.add_parser("extract")
    extract.add_argument("--sample", default=str(STAGE / "sample.json"))
    extract.add_argument("--out", default=str(STAGE / "extraction.json"))
    sub.add_parser("render-report")
    args = parser.parse_args(argv)
    if args.command == "freeze-sample":
        sample = write_frozen_sample(args.out, args.seed)
        print(json.dumps({"sample_sha256": sha256_file(args.out), "freeze_sha256": sample["freeze_sha256"],
                          "missing_companies": len(sample["missing_companies"]),
                          "missing_periods": sample["selection_audit"]["missing_period_count"],
                          "controls": len(sample["controls"])}, indent=2))
    elif args.command == "extract":
        payload = run_extraction(args.sample, args.out)
        print(json.dumps({"complete": payload["complete"], "stopped_reason": payload["stopped_reason"],
                          "period_results": len(payload["period_results"]), "metrics": payload["metrics"]}, indent=2))
    else:
        print(render_report())


if __name__ == "__main__":
    main()
