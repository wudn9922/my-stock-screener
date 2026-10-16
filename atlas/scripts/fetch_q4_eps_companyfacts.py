#!/usr/bin/env python3
"""Resumable, fair-access SEC cache for issuers with a missing Q4 EPS field.

This fetcher does not edit financials. It stores only the CompanyFacts concepts needed
for Q4 EPS evidence, plus a hash of the canonical full SEC response. The SEC client is
the repository's SecClient, including its User-Agent, rate limit, retry policy, and
immediate stop on the first 403. A completed per-company file is a checkpoint; rerun
the same command to resume after ``--budget`` or interruption.

Usage: python atlas/scripts/fetch_q4_eps_companyfacts.py --budget 600
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_financials as fin  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

DEFAULT_FINANCIALS_DIR = REPO_ROOT / "docs/report/financials"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache/q4-eps-evidence"
DEFAULT_Q4_STATE = REPO_ROOT / ".cache/q4-eps-sec-state.json"
STATE_VERSION = 1

# CompanyFacts intentionally excludes issuer extensions and dimensional facts. Keep
# all standard EPS concepts and likely numerator / weighted-share concepts so reviewers
# can identify the official facts available through this endpoint.
CONCEPT_HINTS = (
    "earningspershare", "netincomelossavailabletocommonstockholders",
    "netincomelossattributabletoparent", "netincomeloss", "profitloss",
    "weightedaveragenumberofsharesoutstandingbasic",
    "weightedaveragenumberofdilutedsharesoutstanding",
    "weightedaveragenumberofsharesoutstandingdiluted",
    "weightedaveragenumberofsharesoutstandingbasicanddiluted",
)


def _signature(document):
    q = document.get("quarterly") or {}
    fy, fq, ends = q.get("fy") or [], q.get("fq") or [], q.get("end") or []
    rows = [(end, fiscal_year) for fiscal_year, quarter, end in zip(fy, fq, ends) if quarter == 4]
    raw = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _missing_q4_eps(directory):
    targets = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = read_json(path)
            fin.read_sections(document)
        except (OSError, TypeError, ValueError):
            continue
        if document.get("market") != "US":
            continue
        q = document.get("quarterly") or {}
        fy, fq = q.get("fy") or [], q.get("fq") or []
        values = q.get("values") or {}
        eps_columns = [values.get("epsBasic") or [], values.get("epsDiluted") or []]
        if any(quarter == 4 and any(i >= len(column) or column[i] is None for column in eps_columns)
               for i, quarter in enumerate(fq[:len(fy)])):
            targets[path.stem] = {"path": path, "signature": _signature(document)}
    return targets


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _extract_payload(payload, symbol, cik):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    facts = ((payload.get("facts") or {}).get("us-gaap") or {})
    selected = {}
    for concept, concept_data in facts.items():
        name = str(concept).lower()
        if not any(hint in name for hint in CONCEPT_HINTS):
            continue
        units = concept_data.get("units") or {}
        records = []
        for unit, rows in units.items():
            for fact in rows if isinstance(rows, list) else []:
                if not isinstance(fact, dict) or _number(fact.get("val")) is None:
                    continue
                start, end = fact.get("start"), fact.get("end")
                # Keep duration facts across fiscal-year and quarter windows, including
                # filing versions and restatements. Instant contexts have no start.
                records.append({
                    "unit": str(unit), "value": fact.get("val"), "start": start, "end": end,
                    "form": fact.get("form"), "filed": fact.get("filed"),
                    "accession": fact.get("accn"), "frame": fact.get("frame"),
                })
        if records:
            selected[concept] = records
    return {
        "schema": "sec-companyfacts-q4-evidence-v1",
        "symbol": symbol,
        "cik": sec.cik10(cik),
        "endpoint": sec.FACTS_URL.format(cik=sec.cik10(cik)),
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
        "canonicalPayloadSha256": hashlib.sha256(encoded).hexdigest(),
        "concepts": selected,
    }


def _state_read(path):
    try:
        state = read_json(path)
    except (OSError, ValueError):
        state = None
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "tickerMap": None, "companies": {}, "requests": 0}
    state.setdefault("companies", {})
    return state


def run(*, financials_dir=DEFAULT_FINANCIALS_DIR, cache_dir=DEFAULT_CACHE_DIR,
        q4_state_path=DEFAULT_Q4_STATE, budget=None, client=None, environ=None,
        clock=time.monotonic, reporter=print):
    environ = os.environ if environ is None else environ
    cache_dir = Path(cache_dir)
    facts_dir = cache_dir / "facts"
    facts_dir.mkdir(parents=True, exist_ok=True)
    state_path = cache_dir / "state.json"
    state = _state_read(state_path)
    targets = _missing_q4_eps(Path(financials_dir))
    pending = []
    for symbol, item in targets.items():
        saved = (state["companies"].get(symbol) or {})
        fact_path = facts_dir / f"{symbol}.json"
        if (saved.get("signature") == item["signature"] and saved.get("status") == "ok"
                and fact_path.exists()):
            continue
        pending.append((symbol, item))

    summary = {"status": "done", "targetCompanies": len(targets), "pendingAtStart": len(pending),
               "companiesFetched": 0, "noCik": 0, "noFacts": 0, "requests": 0,
               "stopped403": False, "cacheDirectory": str(cache_dir)}
    if not pending:
        summary["cachedCompanies"] = len(targets)
        return summary
    reason = sec.refusal_reason(environ)
    if reason:
        summary.update(status="refused", reason=reason)
        return summary
    try:
        user_agent = sec.resolve_user_agent(environ)
    except sec.UserAgentError as error:
        summary.update(status="refused", reason=str(error))
        return summary
    client = client or sec.SecClient(user_agent, stop_on_forbidden=True)
    started = clock()

    if not isinstance(state.get("tickerMap"), dict):
        old_state = _state_read(q4_state_path) if Path(q4_state_path).exists() else {}
        ticker_map = old_state.get("tickerMap") if isinstance(old_state.get("tickerMap"), dict) else None
        if ticker_map is None:
            if budget is not None and clock() - started >= budget:
                summary["status"] = "paused"
                return summary
            before = client.requests
            try:
                ticker_map = client.ticker_map()
            except sec.SecForbidden as error:
                state["requests"] = int(state.get("requests", 0)) + client.requests - before
                write_atomic(str(state_path), state)
                summary.update(status="stopped-403", stopped403=True, reason=str(error))
                return summary
            except (sec.FetchError, sec.TooManyFailures, OSError) as error:
                state["requests"] = int(state.get("requests", 0)) + client.requests - before
                write_atomic(str(state_path), state)
                summary.update(status="stopped-fetch-error", reason=str(error))
                return summary
        state["tickerMap"] = ticker_map
        state["requests"] = int(state.get("requests", 0)) + client.requests
        write_atomic(str(state_path), state)

    for symbol, item in pending:
        if budget is not None and clock() - started >= budget:
            summary["status"] = "paused"
            break
        cik = state["tickerMap"].get(symbol)
        if not cik:
            state["companies"][symbol] = {"signature": item["signature"], "status": "no-cik"}
            summary["noCik"] += 1
            write_atomic(str(state_path), state)
            continue
        before = client.requests
        try:
            payload = client.company_facts(cik)
        except sec.SecForbidden as error:
            state["requests"] = int(state.get("requests", 0)) + client.requests - before
            write_atomic(str(state_path), state)
            summary.update(status="stopped-403", stopped403=True, reason=str(error))
            return summary
        except (sec.FetchError, sec.TooManyFailures, OSError) as error:
            state["requests"] = int(state.get("requests", 0)) + client.requests - before
            write_atomic(str(state_path), state)
            summary.update(status="stopped-fetch-error", reason=str(error))
            return summary
        state["requests"] = int(state.get("requests", 0)) + client.requests - before
        if payload is None:
            state["companies"][symbol] = {"signature": item["signature"], "status": "no-facts", "cik": cik}
            summary["noFacts"] += 1
        else:
            if str(payload.get("cik")).zfill(10) != sec.cik10(cik):
                state["companies"][symbol] = {"signature": item["signature"], "status": "cik-mismatch", "cik": cik}
                write_atomic(str(state_path), state)
                summary.update(status="stopped-invalid-response", reason=f"CIK mismatch for {symbol}")
                return summary
            compact = _extract_payload(payload, symbol, cik)
            write_atomic(str(facts_dir / f"{symbol}.json"), compact)
            state["companies"][symbol] = {
                "signature": item["signature"], "status": "ok", "cik": sec.cik10(cik),
                "payloadSha256": compact["canonicalPayloadSha256"],
                "retrievedAt": compact["retrievedAt"],
                "concepts": len(compact["concepts"]),
            }
            summary["companiesFetched"] += 1
        write_atomic(str(state_path), state)
        if (summary["companiesFetched"] + summary["noFacts"] + summary["noCik"]) % 50 == 0:
            reporter(f"SEC CompanyFacts evidence cached: {summary['companiesFetched']} fetched; "
                     f"{len(pending)} pending at start")

    summary["requests"] = client.requests
    summary["cachedCompanies"] = sum(
        1 for symbol, item in targets.items()
        if (state["companies"].get(symbol) or {}).get("signature") == item["signature"]
    )
    if summary["status"] == "paused":
        reporter(f"PAUSED at {summary['cachedCompanies']}/{summary['targetCompanies']} cached companies; resume with the same command.")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_FINANCIALS_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--q4-state", type=Path, default=DEFAULT_Q4_STATE)
    parser.add_argument("--budget", type=float, help="seconds before checkpointing; rerun to resume")
    args = parser.parse_args(argv)
    if args.budget is not None and args.budget <= 0:
        parser.error("--budget must be positive")
    result = run(financials_dir=args.dir, cache_dir=args.cache_dir,
                 q4_state_path=args.q4_state, budget=args.budget)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["status"] not in {"done", "paused"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
