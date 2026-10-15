"""Build and resume an evidence-backed SEC financial-history queue for current US listings.

The NasdaqTrader files define the frozen listing cohort. SEC company_tickers and submissions
establish issuer associations and the narrow SIC 6770 blank-check exclusion. SEC companyfacts
continues through seed_financials_sec.seed_ticker so the normal metric, split, lineage and merge
rules remain in force.

From the repository root:
  python atlas/scripts/backfill_financials_us_market.py --batch-size 500
  python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500
  python atlas/scripts/backfill_financials_us_market.py --resume --retry --batch-size 500

The SEC raw-response cache defaults to /tmp/us-15y-sec-raw. Losing it only requires respectful
refetches; the immutable listing snapshot, issuer decisions and per-issuer checkpoints are stored
under docs/report/fundamentals/.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import shutil
import urllib.error
import urllib.request
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
for path in (REPO_ROOT, REPO_ROOT / "scripts", SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import universe_sources as us  # noqa: E402
import build_financials as financials  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
import restore_yahoo_nulls_from_git as yahoo_restore  # noqa: E402

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
DEFAULT_SNAPSHOT = REPO_ROOT / "docs/report/fundamentals/us-market-snapshot.json"
DEFAULT_PROGRESS = REPO_ROOT / "docs/report/fundamentals/us-market-progress.json"
DEFAULT_MARKET_REPORT = REPO_ROOT / "docs/report/fundamentals/us-market-progress-summary.json"
DEFAULT_RAW_CACHE = Path("/tmp/us-15y-sec-raw")
DEFAULT_YAHOO_BASELINE_REVISION = "c1c9b4e1e"
FINANCIALS_DIR = REPO_ROOT / "docs/report/financials"
TICKER_MAP_URL = sec.TICKERS_URL
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_CACHE_TTL = timedelta(hours=24)


class SECForbidden(RuntimeError):
    """A hard stop carrying the complete response to an SEC HTTP 403."""

    def __init__(self, url: str, headers: dict[str, str], body: bytes):
        super().__init__(f"SEC HTTP 403 for {url}")
        self.response = {
            "url": url,
            "status": 403,
            "headers": headers,
            "bodyBase64": base64.b64encode(body).decode("ascii"),
            "bodyUtf8": body.decode("utf-8", errors="replace"),
            "bodyBytes": len(body),
        }


class SECIdentityError(ValueError):
    """An SEC response did not identify the CIK requested."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def identified_get(url: str, timeout: int = 30) -> tuple[int, dict[str, str], bytes]:
    """One direct request for a public NasdaqTrader directory; no stale-source fallback."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "US financial-history research backfill/1.0",
            "Accept": "text/plain, */*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        try:
            body = error.read()
        finally:
            headers = dict(error.headers.items()) if error.headers else {}
            error.close()
        raise RuntimeError(
            f"directory source HTTP {error.code} {url}; headers={headers!r}; body={body.decode('utf-8', errors='replace')!r}"
        ) from error


def classify_directory_rows(nasdaq_text: str, other_text: str) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Mirror parse_us_listed decisions while retaining a reason for every directory row."""
    symbols: dict[str, dict[str, str]] = {}
    audit: list[dict[str, Any]] = []

    def record_row(source: str, row_number: int, raw_symbol: str, name: str, exchange: str,
                   reason: str | None, reason_basis: str | None) -> None:
        symbol = us.normalize_us_symbol(raw_symbol)
        row: dict[str, Any] = {
            "source": source,
            "rowNumber": row_number,
            "rawSymbol": raw_symbol,
            "symbol": symbol,
            "name": name,
            "exchange": exchange or None,
        }
        if reason:
            row.update(decision="excluded", reason=reason, reasonBasis=reason_basis)
            audit.append(row)
            return
        if not symbol:
            row.update(decision="excluded", reason="symbol", reasonBasis="existing symbol normalization rule")
            audit.append(row)
            return
        if symbol in symbols:
            row.update(decision="duplicate", reason="duplicate-normalized-symbol",
                       reasonBasis="a prior directory row already supplied this normalized listing symbol")
            audit.append(row)
            return
        symbols[symbol] = {"name": us.clean_us_name(name), "exchange": exchange}
        row.update(decision="eligible", reason=None, reasonBasis=None)
        audit.append(row)

    nasdaq_rows = us.parse_pipe_table(nasdaq_text)
    other_rows = us.parse_pipe_table(other_text)
    for index, item in enumerate(nasdaq_rows, start=2):
        name = item.get("Security Name", "")
        if item.get("Test Issue") == "Y":
            record_row("nasdaqlisted.txt", index, item.get("Symbol", ""), name, "NASDAQ", "test", "NasdaqTrader Test Issue flag")
            continue
        if item.get("ETF") == "Y" or item.get("NextShares") == "Y":
            record_row("nasdaqlisted.txt", index, item.get("Symbol", ""), name, "NASDAQ", "etf", "NasdaqTrader ETF/NextShares flag")
            continue
        reason = us.us_exclusion_reason(name)
        record_row("nasdaqlisted.txt", index, item.get("Symbol", ""), name, "NASDAQ", reason,
                   "issuer-name heuristic in universe_sources.py" if reason else None)

    for index, item in enumerate(other_rows, start=2):
        name = item.get("Security Name", "")
        exchange_code = item.get("Exchange", "")
        exchange = us.US_OTHER_EXCHANGES.get(exchange_code)
        raw_symbol = item.get("ACT Symbol") or item.get("CQS Symbol") or ""
        if item.get("Test Issue") == "Y":
            record_row("otherlisted.txt", index, raw_symbol, name, exchange or exchange_code, "test", "NasdaqTrader Test Issue flag")
            continue
        if item.get("ETF") == "Y":
            record_row("otherlisted.txt", index, raw_symbol, name, exchange or exchange_code, "etf", "NasdaqTrader ETF flag")
            continue
        if not exchange:
            record_row("otherlisted.txt", index, raw_symbol, name, exchange_code, "exchange", "exchange code not in existing supported exchange map")
            continue
        reason = us.us_exclusion_reason(name)
        record_row("otherlisted.txt", index, raw_symbol, name, exchange, reason,
                   "issuer-name heuristic in universe_sources.py" if reason else None)

    parsed = us.parse_us_listed(nasdaq_text, other_text)
    if symbols != parsed:
        raise ValueError("row-level directory audit disagrees with universe_sources.parse_us_listed")
    return symbols, audit


def fetch_directory_snapshot() -> dict[str, Any]:
    retrieved_at = None
    evidence: dict[str, Any] = {}
    texts: dict[str, str] = {}
    for filename, url in (("nasdaqlisted.txt", NASDAQ_LISTED_URL), ("otherlisted.txt", OTHER_LISTED_URL)):
        status, headers, body = identified_get(url)
        if status != 200:
            raise RuntimeError(f"directory source HTTP {status} {url}; headers={headers!r}; body={body[:4096]!r}")
        try:
            decoded = body.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(f"{filename} is not UTF-8: {error}") from error
        if not decoded.strip() or "|" not in decoded or "File Creation Time" not in decoded:
            raise ValueError(f"{filename} failed the NasdaqTrader flat-file schema check")
        texts[filename] = decoded
        evidence[filename] = {
            "url": url,
            "status": status,
            "retrievedAt": utc_now(),
            "bytes": len(body),
            "sha256": sha256(body),
            "contentType": headers.get("Content-Type"),
        }
    symbols, audit = classify_directory_rows(texts["nasdaqlisted.txt"], texts["otherlisted.txt"])
    if len(symbols) < 3000:
        raise ValueError(f"directory candidate count {len(symbols)} is below the 3000-symbol sanity threshold")
    exclusion_counts = Counter(row["reason"] for row in audit if row["decision"] == "excluded")
    duplicate_rows = sum(row["decision"] == "duplicate" for row in audit)
    listing_rows = sum(row["decision"] == "eligible" for row in audit)
    candidate_rows = [
        {"symbol": symbol, **symbols[symbol]}
        for symbol in sorted(symbols)
    ]
    cohort_hash = hashlib.sha256("\n".join(sorted(symbols)).encode()).hexdigest()
    source_hash = hashlib.sha256("\n".join(evidence[name]["sha256"] for name in sorted(evidence)).encode()).hexdigest()
    retrieved_at = utc_now()
    snapshot = {
        "version": 1,
        "scope": "symbols accepted by the existing common-equity directory filters; not shell-screened",
        "snapshotAt": retrieved_at,
        "sources": evidence,
        "sourcePairSha256": source_hash,
        "cohortSymbolsSha256": cohort_hash,
        "directoryCounts": {
            "distinctCandidateSymbols": len(symbols),
            "acceptedListingRows": listing_rows,
            "excludedRows": sum(exclusion_counts.values()),
            "duplicateNormalizedRows": duplicate_rows,
            "excludedByReason": dict(sorted(exclusion_counts.items())),
            "spacNameHeuristicRows": exclusion_counts.get("spac", 0),
        },
        "eligibleSymbols": candidate_rows,
        "directoryRowAudit": audit,
        "screeningLimitations": [
            "NasdaqTrader membership establishes current directory inclusion at snapshot time, not legal issuer status or 15-year financial coverage.",
            "The existing SPAC name filter is a string heuristic and is recorded separately from a SEC SIC 6770 blank-check classification.",
            "A symbol absent from a later directory validation is labeled not_current_directory; absence alone does not prove legal delisting.",
        ],
    }
    return snapshot


class RawResponseCache:
    """Integrity-checked SEC raw bodies; old entries without retrieval time remain undated."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = directory / "manifest.json"
        if self.manifest_path.exists():
            manifest = read_json(self.manifest_path)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("responses"), dict):
                raise ValueError(f"invalid SEC raw-cache manifest: {self.manifest_path}")
            self.manifest = manifest
        else:
            self.manifest = {"version": 1, "responses": {}}
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_invalid: list[dict[str, str]] = []

    def _entry_body(self, url: str) -> tuple[dict[str, Any], bytes] | None:
        meta = self.manifest["responses"].get(url)
        if not isinstance(meta, dict):
            return None
        filename = meta.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            self.cache_invalid.append({"url": url, "reason": "invalid cache body path"})
            return None
        path = self.directory / filename
        try:
            body = path.read_bytes()
        except OSError as error:
            self.cache_invalid.append({"url": url, "reason": f"cache body unavailable: {error}"})
            return None
        if len(body) != meta.get("bytes") or sha256(body) != meta.get("sha256"):
            self.cache_invalid.append({"url": url, "reason": "cache body byte count or SHA-256 mismatch"})
            return None
        return meta, body

    def cached_json(self, url: str, validator: Callable[[Any], bool] | None = None) -> tuple[bool, Any]:
        found = self._entry_body(url)
        if found is None:
            self.cache_misses += 1
            return False, None
        meta, body = found
        if meta.get("status") == 404:
            self.cache_hits += 1
            return True, None
        if meta.get("status") != 200:
            self.cache_invalid.append({"url": url, "reason": f"non-cacheable HTTP status {meta.get('status')}"})
            self.cache_misses += 1
            return False, None
        try:
            payload = sec.decode_json(body)
        except (UnicodeDecodeError, ValueError) as error:
            self.cache_invalid.append({"url": url, "reason": f"cached JSON invalid: {error}"})
            self.cache_misses += 1
            return False, None
        if validator is not None and not validator(payload):
            self.cache_invalid.append({"url": url, "reason": "cached JSON failed CIK/schema identity check"})
            self.cache_misses += 1
            return False, None
        self.cache_hits += 1
        return True, payload

    def store(self, url: str, status: int, headers: dict[str, str], body: bytes) -> None:
        if status not in (200, 404):
            return
        digest = sha256(body)
        filename = digest + ".body"
        body_path = self.directory / filename
        if not body_path.exists() or sha256(body_path.read_bytes()) != digest:
            temporary = body_path.with_suffix(".body.tmp")
            temporary.write_bytes(body)
            os.replace(temporary, body_path)
        meta = {
            "url": url,
            "status": status,
            "bytes": len(body),
            "sha256": digest,
            "file": filename,
            "contentEncoding": headers.get("Content-Encoding"),
            "contentType": headers.get("Content-Type"),
            "retrievedAt": utc_now(),
        }
        cik_match = re.search(r"CIK(\d{10})", url, re.I)
        if cik_match:
            meta["expectedCIK"] = cik_match.group(1)
        self.manifest["responses"][url] = meta
        atomic_json(self.manifest_path, self.manifest)

    def record_identity(self, url: str, kind: str, expected_cik: str | None, returned_cik: Any) -> None:
        entry = self.manifest.get("responses", {}).get(url)
        if not isinstance(entry, dict):
            return
        entry["identityCheck"] = {
            "kind": kind,
            "expectedCIK": expected_cik,
            "returnedCIK": _cik_text(returned_cik),
            "passed": expected_cik is None or _cik_text(returned_cik) == expected_cik,
            "validatedAt": utc_now(),
            "retrievalTimeKnown": isinstance(entry.get("retrievedAt"), str),
        }
        atomic_json(self.manifest_path, self.manifest)


class CachedSecClient:
    """Existing SecClient pacing/retries with raw cache and immediate, complete 403 stop."""

    def __init__(self, user_agent: str, raw_cache: RawResponseCache, sleeper: Callable[[float], None] = time.sleep):
        self.raw_cache = raw_cache
        self.last_forbidden: dict[str, Any] | None = None
        self.sleeper = sleeper
        self.wire = sec.SecClient(user_agent, opener=self._open, sleeper=sleeper)
        self._facts_by_cik: dict[str, Any] = {}

    def _open(self, request: urllib.request.Request) -> tuple[int, bytes]:
        url = request.full_url
        try:
            with urllib.request.urlopen(request, timeout=sec.REQUEST_TIMEOUT) as response:
                status, headers, body = response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            try:
                status = error.code
                headers = dict(error.headers.items()) if error.headers else {}
                body = error.read()
            finally:
                error.close()
        if status == 403:
            self.last_forbidden = SECForbidden(url, headers, body).response
            raise SECForbidden(url, headers, body)
        self.raw_cache.store(url, status, headers, body)
        return status, body

    @property
    def requests(self) -> int:
        return self.wire.requests

    @property
    def bytes_received(self) -> int:
        return self.wire.bytes_received

    @property
    def cache_hits(self) -> int:
        return self.raw_cache.cache_hits

    @property
    def cache_misses(self) -> int:
        return self.raw_cache.cache_misses

    def get_json(self, url: str, *, validator: Callable[[Any], bool] | None = None,
                 force_network: bool = False, allow_404: bool = False,
                 identity_kind: str | None = None, expected_cik: str | None = None) -> Any:
        if not force_network:
            found, payload = self.raw_cache.cached_json(url, validator)
            if found:
                return payload
        payload = self.wire.get_json(url, allow_404=allow_404)
        if payload is not None and validator is not None and not validator(payload):
            raise SECIdentityError(f"fresh SEC response failed identity/schema validation: {url}")
        if payload is not None and identity_kind:
            self.raw_cache.record_identity(url, identity_kind, expected_cik,
                                           payload.get("cik") if isinstance(payload, dict) else None)
        return payload

    def ticker_map(self) -> dict[str, str]:
        payload = self.get_json(TICKER_MAP_URL, force_network=True,
                                validator=lambda value: isinstance(value, (dict, list)))
        mapping = sec.parse_ticker_map(payload)
        if len(mapping) < 3000:
            raise ValueError(f"SEC ticker map has only {len(mapping)} normalized US symbols")
        self.raw_cache.record_identity(TICKER_MAP_URL, "company_tickers_schema", None, None)
        return mapping

    def submissions(self, cik: str, *, force_network: bool = False) -> Any:
        expected = sec.cik10(cik)
        url = SUBMISSIONS_URL.format(cik=expected)
        cached_meta = self.raw_cache.manifest.get("responses", {}).get(url)
        retrieved_at = cached_meta.get("retrievedAt") if isinstance(cached_meta, dict) else None
        fresh = False
        if isinstance(retrieved_at, str):
            try:
                fetched = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
                age = datetime.now(timezone.utc) - fetched
                fresh = fetched.tzinfo is not None and timedelta(0) <= age <= SUBMISSIONS_CACHE_TTL
            except ValueError:
                fresh = False
        force_network = force_network or not fresh
        return self.get_json(url,
                             validator=lambda value: isinstance(value, dict)
                             and _cik_text(value.get("cik")) == expected,
                             force_network=force_network,
                             identity_kind="submissions", expected_cik=expected)

    def company_facts(self, cik: str) -> Any:
        expected = sec.cik10(cik)
        if expected in self._facts_by_cik:
            value = self._facts_by_cik[expected]
            if isinstance(value, BaseException):
                raise value
            return value
        try:
            payload = self.get_json(sec.FACTS_URL.format(cik=expected),
                                    validator=lambda value: isinstance(value, dict)
                                    and _cik_text(value.get("cik")) == expected,
                                    allow_404=True, identity_kind="companyfacts", expected_cik=expected)
        except Exception as error:
            self._facts_by_cik[expected] = error
            raise
        self._facts_by_cik[expected] = payload
        return payload


def _cik_text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text.zfill(10) if text.isdigit() else None


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(body)


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def current_snapshot_symbols(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("eligibleSymbols")
    if not isinstance(rows, list):
        raise ValueError("snapshot lacks eligibleSymbols")
    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
            raise ValueError("snapshot has malformed eligible symbol row")
        symbol = financials.financials_key(row["symbol"])
        if symbol != row["symbol"] or financials.market_of(symbol) != "US" or symbol in mapping:
            raise ValueError(f"snapshot symbol is invalid or duplicated: {row.get('symbol')!r}")
        mapping[symbol] = row
    if len(mapping) < 3000:
        raise ValueError(f"snapshot has only {len(mapping)} symbols")
    if hashlib.sha256("\n".join(sorted(mapping)).encode()).hexdigest() != snapshot.get("cohortSymbolsSha256"):
        raise ValueError("snapshot cohort hash mismatch")
    return mapping


def validate_current_membership(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Freshly fetch both files on resume; return evidence without changing the frozen cohort."""
    current = fetch_directory_snapshot()
    frozen = set(current_snapshot_symbols(snapshot))
    active = {row["symbol"] for row in current["eligibleSymbols"]}
    return {
        "validatedAt": current["snapshotAt"],
        "sourcePairSha256": current["sourcePairSha256"],
        "sources": current["sources"],
        "currentDirectoryCandidateCount": len(active),
        "currentlyPresentFrozenSymbols": len(frozen & active),
        "notCurrentDirectorySymbols": sorted(frozen - active),
        "addedSinceFrozenSnapshotSymbols": sorted(active - frozen),
        "currentDirectoryScope": "same existing NasdaqTrader common-equity filters; absence is not a legal delisting finding",
        "currentSymbols": sorted(active),
    }


def document_has_sec(path: Path, symbol: str) -> tuple[bool, str]:
    state, document = financials.load_document(str(path), symbol)
    if state == "corrupt":
        return False, "corrupt"
    if state == "missing":
        return False, "missing"
    return financials.has_sec(document), "ok"


def seed_symbol_preserving_yahoo(symbol: str, cik: str, client: CachedSecClient,
                                 split_checker: Any, now: datetime,
                                 baseline_revision: str) -> dict[str, Any]:
    """Seed in a private staging directory, restore safe Yahoo nulls, then publish atomically."""
    target = FINANCIALS_DIR / f"{symbol}.json"
    pre_seed_document = None
    pre_seed_hash = None
    with tempfile.TemporaryDirectory(prefix="us-market-seed-") as staging_name:
        staging = Path(staging_name)
        staged = staging / target.name
        if target.is_file():
            original_bytes = target.read_bytes()
            pre_seed_hash = sha256(original_bytes)
            staged.write_bytes(original_bytes)
            prior_state, prior_document = financials.load_document(str(target), symbol)
            if prior_state == "ok":
                pre_seed_document = prior_document
        outcome = sec.seed_ticker(symbol, {symbol: cik}, client, str(staging), False, now, splits=split_checker)
        if outcome.get("result") != "written":
            return outcome
        try:
            document = read_json(staged)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"staged SEC financial document for {symbol} is unreadable: {error}") from error
        if not isinstance(document, dict) or document.get("symbol") != symbol:
            raise ValueError(f"staged SEC financial document identity mismatch for {symbol}")

        restoration_audit: dict[str, Any] = {
            "baselineRevision": baseline_revision,
            "referenceSource": "current pre-seed financial file" if pre_seed_document is not None else "none",
            "preSeedFileSha256": pre_seed_hash,
            "restoredRows": {},
            "excludedRows": {},
        }
        if (pre_seed_document is not None and document.get("market") == "US"
                and document.get("currency") == "USD" and pre_seed_document.get("currency") == "USD"):
            same_split_metadata = yahoo_restore.split_metadata_matches(document, pre_seed_document)
            for kind in ("annual", "quarterly"):
                restored_block, restored_rows, excluded_rows = yahoo_restore.restore_section(
                    document.get(kind), pre_seed_document.get(kind), kind, now.date().isoformat(),
                    allow_matching_share_basis=same_split_metadata,
                )
                document[kind] = restored_block
                restoration_audit["restoredRows"][kind] = restored_rows
                restoration_audit["excludedRows"][kind] = excluded_rows
            restoration_audit["splitMetadataMatched"] = same_split_metadata
        else:
            restoration_audit["skipped"] = "no-compatible-current-preseed-document"

        outcome["size"] = financials.write_atomic(str(target), document)
        outcome["document"] = document
        outcome["restoration"] = restoration_audit
        return outcome


def reconcile_financial_index(symbols: set[str]) -> dict[str, Any]:
    """Repair missing/stale index rows from files, including files written before a crash checkpoint."""
    index_path = FINANCIALS_DIR / "index.json"
    index_exists = index_path.exists()
    if index_exists:
        try:
            index = read_json(index_path)
        except (OSError, json.JSONDecodeError) as error:
            return {"state": "unreadable", "detail": f"{type(error).__name__}: {error}", "symbols": []}
        if not isinstance(index, dict) or index.get("version") != financials.VERSION or not isinstance(index.get("items"), dict):
            return {"state": "unreadable", "detail": "existing financial index has an invalid schema", "symbols": []}
        items = index["items"]
    else:
        # A missing index is rebuilt from extant files only; this preserves the Taiwan entries too.
        items = {}

    documents: dict[str, Any] = {}
    validated: list[str] = []
    if not index_exists:
        paths = sorted(FINANCIALS_DIR.glob("*.json"))
        selected = {path.stem for path in paths if path.name != "index.json"}
    else:
        selected = symbols
    for symbol in sorted(selected):
        path = FINANCIALS_DIR / f"{symbol}.json"
        if not path.is_file():
            continue
        state, document = financials.load_document(str(path), symbol)
        if state != "ok" or not isinstance(document, dict):
            continue
        validated.append(symbol)
        known = items.get(symbol) if isinstance(items.get(symbol), dict) else {}
        listed = known.get("lastListed") if financials.parse_day(known.get("lastListed")) else None
        expected = financials.index_item(document, known.get("checkedAt"), listed)
        if expected != known:
            documents[symbol] = document
    if index_exists:
        state, count, byte_count = sec.update_index(str(FINANCIALS_DIR), documents, date.today().isoformat())
        return {"state": state, "symbols": sorted(documents), "validatedSymbols": validated,
                "entries": count, "bytesWritten": byte_count}
    for symbol, document in documents.items():
        items[symbol] = financials.index_item(document, last_listed=date.today().isoformat())
    index = {"version": financials.VERSION, "generatedAt": utc_now(), "items": dict(sorted(items.items()))}
    financials.write_atomic(str(index_path), index)
    return {"state": "created", "symbols": sorted(documents), "validatedSymbols": validated,
            "entries": len(documents)}


def build_progress(snapshot: dict[str, Any], snapshot_file: Path, ticker_map: dict[str, str]) -> dict[str, Any]:
    symbols = current_snapshot_symbols(snapshot)
    groups: dict[str, list[str]] = defaultdict(list)
    symbol_rows: dict[str, Any] = {}
    unmapped: list[dict[str, str]] = []
    for symbol in sorted(symbols):
        cik = ticker_map.get(symbol)
        if not cik:
            symbol_rows[symbol] = {"status": "unknown_cik_mapping", "detail": "no exact current SEC company_tickers mapping"}
            unmapped.append({"symbol": symbol, "status": "unknown_cik_mapping"})
            continue
        groups[sec.cik10(cik)].append(symbol)
    issuers: dict[str, dict[str, Any]] = {}
    queue: list[str] = []
    metadata_queue: list[str] = []
    for cik, share_classes in sorted(groups.items(), key=lambda pair: (min(pair[1]), pair[0])):
        seeded, unseeded, corrupt = [], [], []
        for symbol in sorted(share_classes):
            has_sec, file_state = document_has_sec(FINANCIALS_DIR / f"{symbol}.json", symbol)
            if has_sec:
                seeded.append(symbol)
                symbol_rows[symbol] = {"cik": cik, "status": "seeded_existing"}
            else:
                unseeded.append(symbol)
                symbol_rows[symbol] = {"cik": cik, "status": "pendingmetadata"}
                if file_state == "corrupt":
                    corrupt.append(symbol)
        if not unseeded:
            issuers[cik] = {
                "cik": cik,
                "symbols": sorted(share_classes),
                "seededSymbols": seeded,
                "unseededSymbols": [],
                "status": "seeded_existing_shell_screen_pending",
                "screening": "pending_metadata",
                "basis": "existing SEC-backed financial file; no new submissions screen was requested",
            }
            for symbol in seeded:
                symbol_rows[symbol]["screeningStatus"] = "pending_metadata"
            metadata_queue.append(cik)
            continue
        issuer = {
            "cik": cik,
            "symbols": sorted(share_classes),
            "seededSymbols": seeded,
            "unseededSymbols": unseeded,
            "corruptFinancialSymbols": corrupt,
            "status": "pendingmetadata",
        }
        issuers[cik] = issuer
        queue.append(cik)
    return {
        "version": 1,
        "createdAt": utc_now(),
        "snapshotPath": portable_path(snapshot_file),
        "snapshotSha256": snapshot_hash(snapshot),
        "snapshotAt": snapshot.get("snapshotAt"),
        "snapshotSourcePairSha256": snapshot.get("sourcePairSha256"),
        "tickerMapRetrievedAt": None,
        "tickerMapSource": TICKER_MAP_URL,
        "tickerMapSymbolCount": len(ticker_map),
        "tickerMap": ticker_map,
        "cohortSymbolCount": len(symbols),
        "mappedSymbolCount": len(symbols) - len(unmapped),
        "unmappedSymbols": unmapped,
        "issuerCount": len(groups),
        "existingSeededIssuerCount": sum(item["status"] == "seeded_existing_shell_screen_pending" for item in issuers.values()),
        "unseededIssuerQueue": queue,
        "metadataOnlyIssuerQueue": metadata_queue,
        "issuers": issuers,
        "symbols": symbol_rows,
        "batches": [],
        "currentDirectoryValidations": [],
        "status": "ready",
    }


def submissions_classification(payload: dict[str, Any], cik: str, symbols: list[str]) -> tuple[str, dict[str, Any]]:
    if _cik_text(payload.get("cik")) != cik:
        return "unknownmetadata", {"reason": "submission CIK does not match the requested issuer", "returnedCIK": payload.get("cik")}
    raw_tickers = payload.get("tickers")
    associated: list[str] | None = None
    if "tickers" in payload:
        if not isinstance(raw_tickers, list) or not raw_tickers:
            return "unknownmetadata", {"reason": "submissions ticker association is present but empty or malformed"}
        associated = sorted({financials.financials_key(value) for value in raw_tickers
                             if isinstance(value, str) and financials.financials_key(value)})
        missing = sorted(set(symbols) - set(associated))
        if missing:
            return "unknownmetadata", {
                "reason": "one or more mapped active share classes do not match submissions.tickers",
                "missingTickerAssociations": missing,
                "submissionsTickers": associated,
            }
    raw_sic = payload.get("sic")
    sic_text = str(raw_sic or "").strip()
    if not sic_text.isdigit():
        return "unknownmetadata", {"reason": "SEC submissions SIC is missing or ambiguous", "sic": raw_sic}
    sic_code = int(sic_text)
    detail = {
        "sic": sic_code,
        "sicDescription": payload.get("sicDescription"),
        "entityName": payload.get("name"),
        "cik": cik,
        "submissionsTickers": associated,
    }
    if sic_code == 6770:
        detail.update(
            reason="confirmed blank-check SIC 6770",
            basis="SEC submissions.sic == 6770; entityType and issuer-name text are not used as shell proof",
        )
        return "excluded_sic_6770", detail
    detail.update(
        basis="current NasdaqTrader common-equity directory membership + fresh SEC company_tickers CIK mapping + SEC submissions CIK and available ticker-class association + SIC other than 6770",
        limitation="operating candidate for this queue; SIC != 6770 does not certify that an issuer is legally non-shell",
    )
    return "operating_candidate", detail


def status_for_seed_outcome(outcome: dict[str, Any]) -> str:
    result = outcome.get("result")
    if result == "written":
        return "new"
    if result in ("unchanged", "already-sec"):
        return "unchanged" if result == "unchanged" else "seeded_existing"
    if result == "no-facts":
        return "no-facts"
    detail = str(outcome.get("detail") or "")
    if result == "skipped":
        aliases = {
            "foreign-filer": "foreign-filer",
            "no-us-gaap": "no-us-gaap",
            "non-usd": "non-usd",
            "short-history": "short-history",
            "revenue-scope-mismatch": "revenue-scope-mismatch",
        }
        return aliases.get(detail, "unsupported")
    return "failure"


def summary_counts(progress: dict[str, Any]) -> dict[str, int]:
    counts = Counter(item.get("status", "unknown") for item in progress.get("issuers", {}).values())
    return dict(sorted(counts.items()))


def write_summary(progress: dict[str, Any], path: Path) -> None:
    summary = {
        "version": 1,
        "generatedAt": utc_now(),
        "snapshotAt": progress.get("snapshotAt"),
        "snapshotSha256": progress.get("snapshotSha256"),
        "snapshotSourcePairSha256": progress.get("snapshotSourcePairSha256"),
        "cohortSymbolCount": progress.get("cohortSymbolCount"),
        "mappedSymbolCount": progress.get("mappedSymbolCount"),
        "issuerCount": progress.get("issuerCount"),
        "existingSeededIssuerCount": progress.get("existingSeededIssuerCount"),
        "issuerStatusCounts": summary_counts(progress),
        "unmappedSymbols": progress.get("unmappedSymbols", []),
        "currentDirectoryValidations": progress.get("currentDirectoryValidations", []),
        "batches": progress.get("batches", []),
        "issuers": progress.get("issuers", {}),
        "symbols": progress.get("symbols", {}),
        "sourceScope": "NasdaqTrader directory cohort and SEC-mapped issuers; statuses and per-symbol data are incomplete until queued batches finish",
    }
    atomic_json(path, summary)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_MARKET_REPORT)
    parser.add_argument("--raw-cache-dir", type=Path, default=DEFAULT_RAW_CACHE)
    parser.add_argument("--yahoo-baseline-revision", default=DEFAULT_YAHOO_BASELINE_REVISION,
                        help="pre-backfill Git revision used to preserve safe Yahoo-only null fills")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--resume", action="store_true", help="resume the immutable snapshot and checkpoint")
    parser.add_argument("--retry", action="store_true", help="explicitly retry prior failure or unknown-metadata decisions")
    parser.add_argument("--no-splits", action="store_true", help="disable Yahoo split checks; enabled by default")
    return parser.parse_args(argv)


def initialize(args: argparse.Namespace, client: CachedSecClient, progress_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if progress_path.exists():
        raise ValueError(f"refusing to replace existing progress queue {progress_path}; use --resume")
    if args.snapshot.exists():
        raise ValueError(f"refusing to replace immutable snapshot {args.snapshot}; use --resume to recover or continue it")
    snapshot = fetch_directory_snapshot()
    atomic_json(args.snapshot, snapshot)
    try:
        ticker_map = client.ticker_map()
    except SECForbidden as error:
        progress = {
            "version": 1,
            "createdAt": utc_now(),
            "snapshotPath": portable_path(args.snapshot),
            "snapshotSha256": snapshot_hash(snapshot),
            "snapshotAt": snapshot.get("snapshotAt"),
            "snapshotSourcePairSha256": snapshot.get("sourcePairSha256"),
            "cohortSymbolCount": len(snapshot["eligibleSymbols"]),
            "status": "stopped_http_403",
            "fatalSECResponse": error.response,
            "issuerCount": 0,
            "existingSeededIssuerCount": 0,
            "unmappedSymbols": [],
            "issuers": {},
            "symbols": {},
            "unseededIssuerQueue": [],
            "metadataOnlyIssuerQueue": [],
            "currentDirectoryValidations": [],
            "batches": [],
        }
        atomic_json(progress_path, progress)
        write_summary(progress, args.summary)
        raise
    except Exception as error:
        progress = {
            "version": 1,
            "createdAt": utc_now(),
            "snapshotPath": portable_path(args.snapshot),
            "snapshotSha256": snapshot_hash(snapshot),
            "snapshotAt": snapshot.get("snapshotAt"),
            "snapshotSourcePairSha256": snapshot.get("sourcePairSha256"),
            "cohortSymbolCount": len(snapshot["eligibleSymbols"]),
            "status": "needs_ticker_map",
            "tickerMapError": f"{type(error).__name__}: {error}",
            "issuerCount": 0,
            "existingSeededIssuerCount": 0,
            "unmappedSymbols": [],
            "issuers": {},
            "symbols": {},
            "unseededIssuerQueue": [],
            "metadataOnlyIssuerQueue": [],
            "currentDirectoryValidations": [],
            "batches": [],
        }
        atomic_json(progress_path, progress)
        write_summary(progress, args.summary)
        raise
    progress = build_progress(snapshot, args.snapshot, ticker_map)
    progress["yahooNullBaselineRevision"] = args.resolved_yahoo_baseline_revision
    progress["tickerMapRetrievedAt"] = raw_cache_entry(client.raw_cache, TICKER_MAP_URL).get("retrievedAt")
    progress["tickerMapCache"] = {
        "url": TICKER_MAP_URL,
        "retrieval": "fresh SEC request for this immutable NasdaqTrader snapshot",
        "requestCount": client.requests,
    }
    atomic_json(progress_path, progress)
    return snapshot, progress


def eligible_queue(progress: dict[str, Any], current_symbols: set[str], retry: bool) -> list[str]:
    pending: list[str] = []
    retryable = {"failure", "unknownmetadata"}
    for cik in progress.get("unseededIssuerQueue", []):
        issuer = progress["issuers"][cik]
        status = issuer.get("status")
        current_classes = sorted(set(issuer.get("unseededSymbols", [])) & current_symbols)
        missing_classes = sorted(set(issuer.get("unseededSymbols", [])) - current_symbols)
        if missing_classes:
            issuer["notCurrentDirectorySymbols"] = missing_classes
        if not current_classes:
            if status.startswith("pending") or status in retryable:
                issuer["status"] = "not_current_directory"
                issuer["screening"] = "not_run"
                issuer["detail"] = "all queued symbols are absent from the latest NasdaqTrader directory validation"
            continue
        issuer["currentSymbols"] = current_classes
        if status in ("pendingmetadata", "pendingfinancial") or (retry and status in retryable):
            pending.append(cik)
    return pending


def eligible_metadata_queue(progress: dict[str, Any], current_symbols: set[str], retry: bool) -> list[str]:
    pending: list[str] = []
    for cik in progress.get("metadataOnlyIssuerQueue", []):
        issuer = progress["issuers"][cik]
        status = issuer.get("status")
        current_classes = sorted(set(issuer.get("symbols", [])) & current_symbols)
        missing_classes = sorted(set(issuer.get("symbols", [])) - current_symbols)
        if missing_classes:
            issuer["notCurrentDirectorySymbols"] = missing_classes
        if not current_classes:
            if status == "seeded_existing_shell_screen_pending" or (retry and status in ("failure", "unknownmetadata")):
                issuer["status"] = "not_current_directory"
                issuer["screening"] = "not_run"
                issuer["detail"] = "existing issuer symbols are absent from the latest NasdaqTrader directory validation"
            continue
        issuer["currentSymbols"] = current_classes
        if status == "seeded_existing_shell_screen_pending" or (retry and status in ("failure", "unknownmetadata")):
            pending.append(cik)
    return pending


def finish_batch(progress: dict[str, Any], path: Path, batch: dict[str, Any]) -> None:
    batch["finishedAt"] = utc_now()
    progress.setdefault("batches", []).append(batch)
    progress["status"] = "stopped_http_403" if batch.get("http403") else "ready"
    atomic_json(path, progress)


def raw_cache_entry(cache: RawResponseCache, url: str) -> dict[str, Any]:
    entry = cache.manifest.get("responses", {}).get(url)
    return entry if isinstance(entry, dict) else {}


def run_batch(args: argparse.Namespace) -> int:
    if args.batch_size < 1 or args.batch_size > 500:
        raise ValueError("--batch-size must be 1..500")
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise ValueError("market SEC backfill is local/manual only and refuses CI")
    user_agent = sec.resolve_user_agent(os.environ)
    args.resolved_yahoo_baseline_revision = yahoo_restore.resolve_revision(args.yahoo_baseline_revision)
    raw_cache = RawResponseCache(args.raw_cache_dir)
    client = CachedSecClient(user_agent, raw_cache)

    if args.resume:
        if not args.snapshot.exists():
            raise ValueError("--resume requires the immutable listing snapshot")
        snapshot = read_json(args.snapshot)
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot JSON has the wrong shape")
        if args.progress.exists():
            progress = read_json(args.progress)
            if not isinstance(progress, dict):
                raise ValueError("progress JSON has the wrong shape")
            if progress.get("snapshotSha256") != snapshot_hash(snapshot):
                raise ValueError("immutable snapshot changed since the progress queue was created")
            saved_baseline = progress.get("yahooNullBaselineRevision")
            if saved_baseline and saved_baseline != args.resolved_yahoo_baseline_revision:
                raise ValueError("Yahoo null-restoration baseline differs from the immutable queue")
        else:
            # Recover the narrow crash window after the immutable directory snapshot was saved but
            # before its SEC ticker map / progress queue could be checkpointed.
            progress = {
                "version": 1,
                "createdAt": utc_now(),
                "snapshotPath": portable_path(args.snapshot),
                "snapshotSha256": snapshot_hash(snapshot),
                "snapshotAt": snapshot.get("snapshotAt"),
                "snapshotSourcePairSha256": snapshot.get("sourcePairSha256"),
                "yahooNullBaselineRevision": args.resolved_yahoo_baseline_revision,
                "cohortSymbolCount": len(snapshot.get("eligibleSymbols", [])),
                "status": "needs_ticker_map",
                "tickerMapError": "recovered snapshot without its progress checkpoint",
                "issuerCount": 0,
                "existingSeededIssuerCount": 0,
                "unmappedSymbols": [],
                "issuers": {},
                "symbols": {},
                "unseededIssuerQueue": [],
                "metadataOnlyIssuerQueue": [],
                "currentDirectoryValidations": [],
                "batches": [],
            }
            atomic_json(args.progress, progress)
        if progress.get("status") == "stopped_http_403":
            raise ValueError("this queue stopped on SEC HTTP 403; no SEC requests will be made from it again")
        validation = validate_current_membership(snapshot)
        active_symbols = set(validation["currentSymbols"])
        if progress.get("status") == "needs_ticker_map":
            try:
                ticker_map = client.ticker_map()
            except SECForbidden as error:
                progress.update(status="stopped_http_403", fatalSECResponse=error.response)
                progress["tickerMapError"] = "SEC returned HTTP 403 while fetching company_tickers"
                atomic_json(args.progress, progress)
                write_summary(progress, args.summary)
                raise
            except Exception as error:
                progress["tickerMapError"] = f"{type(error).__name__}: {error}"
                progress["status"] = "needs_ticker_map"
                atomic_json(args.progress, progress)
                write_summary(progress, args.summary)
                raise
            recovered = build_progress(snapshot, args.snapshot, ticker_map)
            recovered["yahooNullBaselineRevision"] = args.resolved_yahoo_baseline_revision
            recovered["createdAt"] = progress.get("createdAt", utc_now())
            recovered["tickerMapRetrievedAt"] = raw_cache_entry(raw_cache, TICKER_MAP_URL).get("retrievedAt")
            recovered["tickerMapCache"] = {
                "url": TICKER_MAP_URL,
                "retrieval": "fresh SEC request after recovering the immutable directory snapshot",
                "requestCount": client.requests,
            }
            recovered["currentDirectoryValidations"] = progress.get("currentDirectoryValidations", [])
            progress = recovered
        progress.setdefault("currentDirectoryValidations", []).append({k: v for k, v in validation.items() if k != "currentSymbols"})
        atomic_json(args.progress, progress)
    else:
        snapshot, progress = initialize(args, client, args.progress)
        active_symbols = set(current_snapshot_symbols(snapshot))
        progress.setdefault("currentDirectoryValidations", []).append({
            "validatedAt": snapshot["snapshotAt"],
            "sourcePairSha256": snapshot["sourcePairSha256"],
            "sources": snapshot["sources"],
            "currentDirectoryCandidateCount": len(active_symbols),
            "currentlyPresentFrozenSymbols": len(active_symbols),
            "notCurrentDirectorySymbols": [],
            "addedSinceFrozenSnapshotSymbols": [],
            "currentDirectoryScope": "initial fresh files used to create immutable cohort",
        })
        atomic_json(args.progress, progress)

    index_reconciliation = reconcile_financial_index(set(current_snapshot_symbols(snapshot)))
    progress["lastIndexReconciliation"] = index_reconciliation
    if index_reconciliation.get("state") in ("updated", "unchanged", "created"):
        pending_index = set(progress.get("pendingIndexSymbols", []))
        pending_index.difference_update(index_reconciliation.get("validatedSymbols", []))
        progress["pendingIndexSymbols"] = sorted(pending_index)
    atomic_json(args.progress, progress)

    batch = {
        "batchId": len(progress.get("batches", [])) + 1,
        "startedAt": utc_now(),
        "requestedAttemptLimit": args.batch_size,
        "resume": args.resume,
        "explicitRetry": args.retry,
        "attemptedCIKs": [],
        "decisions": {},
        "secRequestsBefore": client.requests,
        "secBytesBefore": client.bytes_received,
        "rawCacheHitsBefore": raw_cache.cache_hits,
        "rawCacheMissesBefore": raw_cache.cache_misses,
    }
    issued_documents: dict[str, dict[str, Any]] = {}
    split_checker = None if args.no_splits else sec.SplitChecker()
    current_queue = eligible_queue(progress, active_symbols, args.retry)
    phase = "financial" if current_queue else "metadata_only"
    if phase == "financial":
        selected = current_queue[:args.batch_size]
    else:
        current_queue = eligible_metadata_queue(progress, active_symbols, args.retry)
        selected = current_queue[:args.batch_size]
    batch["phase"] = phase
    progress["activeBatch"] = {"batchId": batch["batchId"], "selectedCIKs": selected, "startedAt": batch["startedAt"]}
    atomic_json(args.progress, progress)

    if not selected:
        batch["remainingPendingIssuers"] = 0
        batch["status"] = "no_pending_issuers"
        finish_batch(progress, args.progress, batch)
        write_summary(progress, args.summary)
        print("No issuers are pending in the current validated directory cohort.")
        return 0

    exit_code = 0
    try:
        for position, cik in enumerate(selected, start=1):
            issuer = progress["issuers"][cik]
            symbols = issuer.get("currentSymbols") or issuer["unseededSymbols"]
            previous_issuer_status = issuer.get("status")
            batch["attemptedCIKs"].append(cik)
            issuer["lastAttemptAt"] = utc_now()
            issuer["status"] = "pendingmetadata"
            issuer["currentSymbols"] = symbols
            issuer["screening"] = "pending"
            atomic_json(args.progress, progress)
            try:
                submissions = client.submissions(
                    cik,
                    force_network=args.retry and previous_issuer_status in ("failure", "unknownmetadata"),
                )
                screen_status, screen_detail = submissions_classification(submissions, cik, symbols)
                submission_url = SUBMISSIONS_URL.format(cik=sec.cik10(cik))
                submission_meta = raw_cache_entry(raw_cache, submission_url)
                screen_detail["submissionsSource"] = {
                    "url": submission_url,
                    "retrievedAt": submission_meta.get("retrievedAt"),
                    "retrievalTimeKnown": isinstance(submission_meta.get("retrievedAt"), str),
                    "cacheMaxAgeHours": SUBMISSIONS_CACHE_TTL.total_seconds() / 3600,
                }
                issuer["screening"] = screen_status
                issuer["screeningEvidence"] = screen_detail
                if screen_status != "operating_candidate":
                    issuer["status"] = (
                        f"seeded_existing_{screen_status}"
                        if phase == "metadata_only" and screen_status != "unknownmetadata"
                        else screen_status
                    )
                    issuer["detail"] = screen_detail
                    for symbol in symbols:
                        progress["symbols"][symbol]["screeningStatus"] = screen_status
                        progress["symbols"][symbol]["screeningEvidence"] = screen_detail
                    batch["decisions"][cik] = issuer["status"]
                    atomic_json(args.progress, progress)
                    print(f"[{position}/{len(selected)}] {cik} {','.join(symbols)} {issuer['status']}", flush=True)
                    continue

                if phase == "metadata_only":
                    issuer["status"] = "seeded_existing_screened_operating_candidate"
                    issuer["detail"] = screen_detail
                    for symbol in symbols:
                        progress["symbols"][symbol]["screeningStatus"] = "operating_candidate"
                        progress["symbols"][symbol]["screeningEvidence"] = screen_detail
                    batch["decisions"][cik] = issuer["status"]
                    atomic_json(args.progress, progress)
                    print(f"[{position}/{len(selected)}] {cik} {','.join(symbols)} metadata-only operating candidate", flush=True)
                    continue

                issuer["status"] = "pendingfinancial"
                atomic_json(args.progress, progress)
                issuer_outcomes: dict[str, Any] = {}
                for symbol in symbols:
                    status, _ = document_has_sec(FINANCIALS_DIR / f"{symbol}.json", symbol)
                    if status:
                        outcome = {"result": "already-sec"}
                    else:
                        outcome = seed_symbol_preserving_yahoo(
                            symbol, cik, client, split_checker,
                            datetime.now(timezone.utc).replace(microsecond=0),
                            args.resolved_yahoo_baseline_revision,
                        )
                    index_update = None
                    if outcome.get("result") == "written" and isinstance(outcome.get("document"), dict):
                        index_update = sec.update_index(
                            str(FINANCIALS_DIR), {symbol: outcome["document"]},
                            datetime.now(timezone.utc).date().isoformat(),
                        )
                        if index_update[0] in ("absent", "unreadable"):
                            pending = set(progress.get("pendingIndexSymbols", []))
                            pending.add(symbol)
                            progress["pendingIndexSymbols"] = sorted(pending)
                        else:
                            pending = set(progress.get("pendingIndexSymbols", []))
                            pending.discard(symbol)
                            progress["pendingIndexSymbols"] = sorted(pending)
                    outcome_status = status_for_seed_outcome(outcome)
                    issuer_outcomes[symbol] = {
                        "status": outcome_status,
                        "result": outcome.get("result"),
                        "detail": outcome.get("detail"),
                        "counts": outcome.get("counts"),
                        "qualityLevel": outcome.get("level"),
                        "split": outcome.get("split"),
                        "yahooRestoration": outcome.get("restoration"),
                        "indexUpdate": ({"state": index_update[0], "entries": index_update[1],
                                         "bytesWritten": index_update[2]} if index_update else None),
                    }
                    if isinstance(outcome.get("document"), dict):
                        issued_documents[symbol] = outcome["document"]
                    progress["symbols"][symbol] = {
                        "cik": cik,
                        "screeningStatus": "operating_candidate",
                        "screeningEvidence": screen_detail,
                        **issuer_outcomes[symbol],
                    }
                    issuer["symbolOutcomes"] = issuer_outcomes
                    atomic_json(args.progress, progress)
                statuses = {item["status"] for item in issuer_outcomes.values()}
                if "failure" in statuses:
                    issuer["status"] = "failure"
                elif "new" in statuses:
                    issuer["status"] = "new"
                elif "unchanged" in statuses or "seeded_existing" in statuses:
                    issuer["status"] = "unchanged"
                elif statuses:
                    issuer["status"] = sorted(statuses)[0]
                else:
                    issuer["status"] = "failure"
                batch["decisions"][cik] = issuer["status"]
                print(f"[{position}/{len(selected)}] {cik} {','.join(symbols)} {issuer['status']}", flush=True)
                atomic_json(args.progress, progress)
            except SECForbidden as error:
                issuer.update(status="failure", detail="stopped after SEC HTTP 403", screening="failure")
                batch["decisions"][cik] = "failure"
                batch["http403"] = error.response
                batch["status"] = "stopped_http_403"
                progress["fatalSECResponse"] = error.response
                progress["status"] = "stopped_http_403"
                for rest in selected[position:]:
                    if progress["issuers"][rest].get("status") in ("pendingmetadata", "pendingfinancial"):
                        progress["issuers"][rest]["status"] = "pendingmetadata"
                atomic_json(args.progress, progress)
                exit_code = 2
                print("STOPPED: SEC returned HTTP 403. No further SEC request will be made in this run.", flush=True)
                break
            except sec.TooManyFailures as error:
                issuer.update(status="failure", detail=f"SEC client stopped after repeated failures: {error}")
                batch["decisions"][cik] = "failure"
                batch["status"] = "stopped_repeated_SEC_failures"
                progress["status"] = batch["status"]
                atomic_json(args.progress, progress)
                exit_code = 2
                break
            except (sec.FetchError, SECIdentityError, OSError, ValueError, RuntimeError) as error:
                issuer.update(status="failure", detail=f"{type(error).__name__}: {error}")
                batch["decisions"][cik] = "failure"
                progress["status"] = "ready"
                atomic_json(args.progress, progress)
                print(f"[{position}/{len(selected)}] {cik} {','.join(symbols)} failure ({type(error).__name__}: {error})", flush=True)
        if issued_documents:
            index_state, index_count, index_bytes = sec.update_index(str(FINANCIALS_DIR), issued_documents,
                                                                      datetime.now(timezone.utc).date().isoformat())
            batch["financialIndex"] = {"state": index_state, "entries": index_count, "bytesWritten": index_bytes}
        batch.setdefault("status", "completed")
    except KeyboardInterrupt:
        batch["status"] = "interrupted"
        progress["status"] = "ready"
        exit_code = 130
    finally:
        batch["secRequestsAfter"] = client.requests
        batch["secBytesAfter"] = client.bytes_received
        batch["rawCacheHitsAfter"] = raw_cache.cache_hits
        batch["rawCacheMissesAfter"] = raw_cache.cache_misses
        batch["rawCacheInvalidEntries"] = list(raw_cache.cache_invalid)
        batch["remainingPendingNewIssuers"] = sum(
            1 for cik in progress.get("unseededIssuerQueue", [])
            if progress.get("issuers", {}).get(cik, {}).get("status") in ("pendingmetadata", "pendingfinancial")
        )
        batch["remainingPendingMetadataOnlyIssuers"] = sum(
            1 for cik in progress.get("metadataOnlyIssuerQueue", [])
            if progress.get("issuers", {}).get(cik, {}).get("status") == "seeded_existing_shell_screen_pending"
        )
        batch["remainingPendingIssuers"] = batch["remainingPendingNewIssuers"] + batch["remainingPendingMetadataOnlyIssuers"]
        progress.pop("activeBatch", None)
        finish_batch(progress, args.progress, batch)
        write_summary(progress, args.summary)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_batch(args)
    except SECForbidden as error:
        print(f"STOPPED: SEC returned HTTP 403. Full headers/body are in {args.progress}; no further SEC request was made.", flush=True)
        return 2
    except (sec.FetchError, sec.TooManyFailures, OSError, ValueError, RuntimeError) as error:
        print(f"Refusing/failed: {type(error).__name__}: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
