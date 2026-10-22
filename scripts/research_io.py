"""Shared bounded public-data transport and atomic, unchanged-aware snapshots (research-v1)."""

from __future__ import annotations
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "research-v1"


def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    """Never truncate a good file, never emit NaN; avoid timestamp-only git churn."""
    path = Path(path)
    old = read(path)
    if isinstance(old, dict) and isinstance(value, dict):
        ignore = {"generatedAt", "checkedAt", "fetchedAt"}
        if {k: v for k, v in old.items() if k not in ignore} == {
            k: v for k, v in value.items() if k not in ignore
        }:
            return
    body = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(body)
    os.replace(temp, path)


class SourceError(Exception):
    """Expected remote/format failure, safe to retain the previous snapshot."""


class Http:
    def __init__(self, user_agent=None, delay=0.6, timeout=35):
        self.user_agent = (
            user_agent
            or "my-stock-screener/1.0 (+https://github.com/wudn9922/my-stock-screener)"
        )
        self.delay, self.timeout, self.last = delay, timeout, 0
        self.receipts = []

    def get(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": self.user_agent}),
                timeout=self.timeout,
            ) as response:
                body = response.read()
                receipt = {
                    "url": url,
                    "fetchedAt": stamp(),
                    "lastModified": response.headers.get("Last-Modified"),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                }
                self.receipts.append(receipt)
                return body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"{url}: {exc}") from exc

    def json(self, url):
        try:
            return json.loads(self.get(url))
        except (ValueError, UnicodeError) as exc:
            raise SourceError(
                f"{url}: invalid JSON (HTML/maintenance responses are not empty datasets)"
            ) from exc


def meta(
    source, url, asof=None, status="ok", reason=None, fetched=None, published=None
):
    return dict(
        methodVersion=VERSION,
        source=source,
        sourceUrl=url,
        asOf=asof,
        publishedAt=published,
        fetchedAt=fetched,
        generatedAt=stamp(),
        status=status,
        missingReason=reason,
    )


def retain(previous, source, url, error):
    value = dict(previous or {})
    value.update(
        status="stale" if previous else "unavailable",
        missingReason=str(error),
        checkedAt=stamp(),
    )
    if not previous:
        value.update(meta(source, url, status="unavailable", reason=str(error)))
    return value


def universe():
    data = read(ROOT / "docs/report/universe.json", {})
    fields = data.get("fields", [])
    ix = fields.index("symbol") if "symbol" in fields else 0
    out = {}
    for market in ("TW", "US"):
        out[market] = {
            row[ix] for row in data.get("markets", {}).get(market, {}).get("rows", [])
        }
    return out


def price_rows(path):
    from build_tw_chips import to_number

    with Path(path).open() as f:
        return [
            dict(
                date=r["Date"][:10],
                **{
                    k.lower(): to_number(r.get(k))
                    for k in ("Open", "High", "Low", "Close", "Volume")
                },
                amount=to_number(r.get("Amount")),
                action=r.get("Action") or None,
            )
            for r in csv.DictReader(f)
        ]


def market_paths(cache):
    """Full-market cache takes precedence. data/ is explicitly reported as partial coverage."""
    paths = {p.stem: p for p in (ROOT / "data").glob("*.csv")}
    paths.update({p.stem: p for p in (Path(cache) / "bars").glob("*.csv")})
    return paths
