from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import PRICE_COLUMNS, clean_ohlcv


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_authorized_csv(
    source_file: str | Path,
    destination: str | Path,
    source_id: str,
    symbol: str,
    market: str,
    provider: str,
    source_url: str,
    license_text: str,
    adjustment_status: str,
    exchange_timezone: str,
) -> dict:
    """Idempotently add a licensed local export without touching repo data/*."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_id):
        raise ValueError("source_id must be a lowercase slug")
    if market not in {"US", "TW"}:
        raise ValueError("market must be US or TW")
    required = {"provider": provider, "source_url": source_url, "license": license_text, "adjustment_status": adjustment_status, "exchange_timezone": exchange_timezone}
    if any(not str(value).strip() for value in required.values()):
        raise ValueError("provider, URL, license, adjustment status and timezone are required")

    source = Path(source_file).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    raw = pd.read_csv(source)
    frame, _ = clean_ohlcv(raw)
    if frame.empty:
        raise ValueError("source has no valid OHLCV observations")
    frame = frame.reset_index()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    symbol = symbol.strip().upper()
    output_root = Path(destination) / "sources" / source_id
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / f"{symbol}.csv"
    if target.exists():
        old = pd.read_csv(target)
        merged = pd.concat([old, frame], ignore_index=True)
        merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce")
        merged = merged.dropna(subset=["Date"]).drop_duplicates(subset=["Date"], keep="last").sort_values("Date")
        merged["Date"] = merged["Date"].dt.strftime("%Y-%m-%d")
        frame = merged
    frame.to_csv(target, index=False, columns=["Date", *PRICE_COLUMNS])

    manifest_path = output_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "schema_version": 1,
        "source_id": source_id,
        "provider": provider,
        "source_url": source_url,
        "license": license_text,
        "exchange_timezone": exchange_timezone,
        "adjustment_status": adjustment_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }
    manifest["files"][symbol] = {
        "market": market,
        "path": target.name,
        "source_input_sha256": _sha256(source),
        "merged_output_sha256": _sha256(target),
        "first_date": frame["Date"].iloc[0],
        "last_date": frame["Date"].iloc[-1],
        "rows": len(frame),
        "resume_safe": True,
    }
    manifest["last_completed_symbol"] = symbol
    manifest["last_completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest["files"][symbol]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a licensed OHLCV export into the isolated Market Lab source area")
    parser.add_argument("source_file")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market", required=True, choices=("US", "TW"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--adjustment-status", required=True)
    parser.add_argument("--exchange-timezone", required=True)
    args = parser.parse_args()
    result = import_authorized_csv(args.source_file, args.destination, args.source_id, args.symbol, args.market, args.provider, args.source_url, args.license, args.adjustment_status, args.exchange_timezone)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
