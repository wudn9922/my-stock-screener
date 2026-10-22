"""Allowlisted artifact hand-off. Collectors have no repository write credentials."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from research_io import ROOT

FILES = {
    "institutional": (
        "institutions.json",
        "institutions/TW.json",
        "institutions/TWO.json",
        "turnover/TW.json",
        "turnover/TWO.json",
    ),
    "volume": ("volume.json", "volume/TW.json", "volume/US.json"),
    "sec": ("sec-radar.json", "sec/US.json"),
    "alerts": ("alerts.json",),
    "world": ("world-assets.json",),
}


def world_files(root):
    from research_candles import ASSETS

    index = json.loads((root / "world-assets.json").read_text())
    out = {}
    for key, asset in index.get("series", {}).items():
        if not asset.get("candlePath"):
            continue
        if (
            key not in ASSETS
            or asset["candlePath"] != f"candles/{key}.json"
            or asset.get("symbol") != ASSETS[key][2]
        ):
            raise ValueError("Unapproved world candle path/symbol")
        out[key] = asset
    return out


def files_for(root, module):
    return FILES[module] + (
        tuple(a["candlePath"] for a in world_files(root).values())
        if module == "world"
        else ()
    )


def validate(root, module):
    expected = set(files_for(root, module))
    assets = world_files(root) if module == "world" else {}
    expected.update(f"_records/{a['symbol']}.csv" for a in assets.values())
    if module == 'world' and (root / '_kline-corrections.json').exists():
        expected.add('_kline-corrections.json')
        from kline_integrity import read_log
        read_log(root / '_kline-corrections.json')
    found = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if found != expected:
        raise ValueError(f"Invalid {module} artifact file set: {found ^ expected}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Artifact symlinks are forbidden")
    index = json.loads((root / FILES[module][0]).read_text())
    if index.get("version") != 1:
        raise ValueError("Unsupported research index")
    for asset in assets.values():
        for relative, digest in [
            (asset["candlePath"], asset["candleSha256"]),
            (f"_records/{asset['symbol']}.csv", asset["csvSha256"]),
        ]:
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
                raise ValueError("World candle/CSV checksum mismatch")
        from research_candles import read_csv
        from kline_integrity import violation, quarantined_dates
        quarantine = root / '_kline-corrections.json'
        excluded = quarantined_dates(asset['symbol'], quarantine if quarantine.exists() else None)
        for row in read_csv(root / '_records' / (asset['symbol'] + '.csv')):
            if row[0] not in excluded and violation(dict(zip(['Open','High','Low','Close','Volume'], row[1:]))):
                raise ValueError('Invalid unquarantined world OHLC in artifact')
    for storage in (index.get("storage"), index.get("turnoverStorage")):
        if storage is None:
            continue
        if storage.get("format") != "research-market-v1":
            raise ValueError("Unsupported research storage")
        for spec in storage["markets"].values():
            if spec["path"] not in expected:
                raise ValueError("Unapproved market snapshot path")
            body = (root / spec["path"]).read_bytes()
            if (
                len(body) != spec["bytes"]
                or hashlib.sha256(body).hexdigest() != spec["sha256"]
            ):
                raise ValueError("Market snapshot checksum mismatch")


def stage(root, dest, module, records_root=None):
    target = dest / module
    for relative in files_for(root, module):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, path)
    if module == "world":
        corrections = root.parent / 'kline-corrections.json'
        if corrections.exists():
            shutil.copyfile(corrections, target / '_kline-corrections.json')
        for asset in world_files(root).values():
            name = asset["symbol"] + ".csv"
            path = target / "_records" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(records_root or ROOT / "data" / "world") / name, path)
    validate(target, module)


def overlay(artifacts, dest, records_root=None):
    # Validate every artifact before changing any working-tree file.
    modules = []
    for folder in sorted(artifacts.iterdir()):
        if folder.name not in FILES or not folder.is_dir() or folder.is_symlink():
            raise ValueError("Unexpected research artifact: " + folder.name)
        validate(folder, folder.name)
        if folder.name == "world":
            from research_candles import read_csv

            for asset in world_files(folder).values():
                name = asset["symbol"] + ".csv"
                existing = Path(records_root or ROOT / "data" / "world") / name
                candidate = folder / "_records" / name
                if existing.exists() and not {r[0] for r in read_csv(existing)} <= {
                    r[0] for r in read_csv(candidate)
                }:
                    raise ValueError("Refusing to remove retained world K-line dates")
        modules.append(folder.name)
    if not modules:
        raise ValueError("No successful research artifacts to publish")
    for module in modules:
        correction_artifact = artifacts / module / '_kline-corrections.json'
        if module == 'world' and correction_artifact.exists():
            from kline_integrity import read_log, merge_logs, _atomic
            target = dest.parent / 'kline-corrections.json'
            merged = merge_logs(read_log(target), read_log(correction_artifact))
            _atomic(target, json.dumps(merged, ensure_ascii=False, indent=2)+'\n')
        for relative in files_for(artifacts / module, module):
            path = dest / relative
            source = artifacts / module / relative
            if not path.exists() or path.read_bytes() != source.read_bytes():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, path)
        if module == "world":
            for asset in world_files(artifacts / module).values():
                name = asset["symbol"] + ".csv"
                target = Path(records_root or ROOT / "data" / "world") / name
                source = artifacts / module / "_records" / name
                if not target.exists() or target.read_bytes() != source.read_bytes():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["stage", "overlay"])
    p.add_argument("module", nargs="?", choices=FILES)
    p.add_argument("--root", type=Path, default=ROOT / "docs/report/research")
    p.add_argument("--dest", type=Path, required=True)
    p.add_argument("--artifacts", type=Path)
    args = p.parse_args()
    if args.action == "stage":
        stage(args.root, args.dest, args.module)
    else:
        overlay(args.artifacts, args.dest)
