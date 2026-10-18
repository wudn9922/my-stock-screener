#!/usr/bin/env python3
"""OCR cached SEC image exhibits and write a source-linked review sidecar.

The script never downloads documents. It validates every cached body against the
hash recorded by Source D and can reuse a prior OCR sidecar by exact URL/hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs/report/sec-q4-eps-poc"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_body_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / f"{sha256(url.encode('utf-8'))}.body"


def run(source_d: Path, cache_dir: Path, output: Path, reuse: Path | None = None) -> dict:
    source = json.loads(source_d.read_text(encoding="utf-8"))
    reused = {}
    if reuse and reuse.exists():
        for item in json.loads(reuse.read_text(encoding="utf-8")):
            reused[(item.get("url"), item.get("sha256"))] = item

    tesseract = shutil.which("tesseract")
    version = None
    if tesseract:
        result = subprocess.run([tesseract, "--version"], capture_output=True, text=True, check=False)
        version = (result.stdout or result.stderr).splitlines()[0] if result.returncode == 0 else None

    rows = []
    for accession, filing in source.get("filings", {}).items():
        for document in filing.get("documents", []):
            if document.get("parse_status") != "unsupported_nontext":
                continue
            row = {
                "accession": accession,
                "ticker": filing.get("metadata", {}).get("tickers", []),
                "windows": filing.get("metadata", {}).get("period_windows", []),
                "name": document.get("name"),
                "url": document.get("url"),
                "sha256": document.get("sha256"),
                "bytes": document.get("bytes"),
                "ocr_engine": version,
            }
            prior = reused.get((row["url"], row["sha256"]))
            if prior is not None:
                row.update({"ocr_status": prior.get("ocr_status", "reused"),
                            "ocr_text": prior.get("ocr_text", ""),
                            "reused_from_prior_sidecar": True})
                rows.append(row)
                continue

            body_path = cache_body_path(cache_dir, row["url"])
            if not body_path.exists():
                row.update({"ocr_status": "source_body_unavailable", "ocr_text": ""})
                rows.append(row)
                continue
            body = body_path.read_bytes()
            if sha256(body) != row["sha256"]:
                row.update({"ocr_status": "source_hash_mismatch", "ocr_text": ""})
                rows.append(row)
                continue
            if not tesseract:
                row.update({"ocr_status": "tesseract_unavailable", "ocr_text": ""})
                rows.append(row)
                continue

            suffix = Path(row["name"] or "").suffix or ".img"
            with tempfile.TemporaryDirectory(prefix="sec-q4-eps-ocr-") as temporary:
                image_path = Path(temporary) / f"source{suffix}"
                image_path.write_bytes(body)
                process = subprocess.run(
                    [tesseract, str(image_path), "stdout", "--psm", "6"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            row["ocr_status"] = "ok" if process.returncode == 0 else "engine_failure"
            row["ocr_text"] = process.stdout.strip()
            if process.returncode != 0:
                row["ocr_error"] = process.stderr[-1000:]
            row["reused_from_prior_sidecar"] = False
            rows.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "source_d": str(source_d),
        "output": str(output),
        "tesseract_version": version,
        "image_count": len(rows),
        "status_counts": {
            status: sum(row.get("ocr_status") == status for row in rows)
            for status in sorted({row.get("ocr_status") for row in rows})
        },
        "all_images_ocr_ok": bool(rows) and all(row.get("ocr_status") == "ok" for row in rows),
        "ocr_characters": sum(len(row.get("ocr_text", "")) for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-d", type=Path, default=DEFAULT_REPORT / "source-d.json")
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/sec-q4-eps-poc-cache"))
    parser.add_argument("--reuse-existing", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT / "source-d-ocr.json")
    args = parser.parse_args()
    summary = run(args.source_d, args.cache_dir, args.out, args.reuse_existing)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_images_ocr_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
