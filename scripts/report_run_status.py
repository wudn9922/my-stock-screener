"""Run-only report failures; never a published website artifact.

The daily entry point resets this file after its public-owner check. A separate
post-publication step reads it so a report failure cannot skip LINE or Dify.
"""
import json
import os
from pathlib import Path


def status_path():
    root = Path(os.environ["RUNNER_TEMP"]) if os.environ.get("RUNNER_TEMP") else Path(__file__).resolve().parents[1] / ".cache"
    return root / "daily-report-output-status.json"


def _write(failures):
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"failures": failures}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def reset_status():
    _write([])


def _read():
    data = json.loads(status_path().read_text(encoding="utf-8"))
    failures = data["failures"]
    if not isinstance(failures, list) or any(not isinstance(item, str) for item in failures):
        raise ValueError("invalid daily report status")
    return failures


def record_failure(output, error):
    # Log only the output name and exception class in the run-only status file.
    # The existing producer logs include the detailed validation reason.
    try:
        failures = _read()
    except FileNotFoundError:
        # Standalone series writers may be invoked outside the daily entry point.
        failures = []
    failures.append(f"{output}: {type(error).__name__}")
    _write(failures)


def main():
    try:
        failures = _read()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Daily report status unavailable: {type(exc).__name__}")
        return 1
    for failure in failures:
        print(f"Daily report output failed: {failure}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
