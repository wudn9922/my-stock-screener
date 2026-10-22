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


def validate(root, module):
    expected = set(FILES[module])
    found = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if found != expected:
        raise ValueError(f"Invalid {module} artifact file set: {found ^ expected}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Artifact symlinks are forbidden")
    index = json.loads((root / FILES[module][0]).read_text())
    if index.get("version") != 1:
        raise ValueError("Unsupported research index")
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


def stage(root, dest, module):
    target = dest / module
    for relative in FILES[module]:
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, path)
    validate(target, module)


def overlay(artifacts, dest):
    # Validate every artifact before changing any working-tree file.
    modules = []
    for folder in sorted(artifacts.iterdir()):
        if folder.name not in FILES or not folder.is_dir() or folder.is_symlink():
            raise ValueError("Unexpected research artifact: " + folder.name)
        validate(folder, folder.name)
        modules.append(folder.name)
    if not modules:
        raise ValueError("No successful research artifacts to publish")
    for module in modules:
        for relative in FILES[module]:
            path = dest / relative
            source = artifacts / module / relative
            if not path.exists() or path.read_bytes() != source.read_bytes():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, path)


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
