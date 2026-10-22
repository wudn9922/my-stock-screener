"""Stable, range-addressable market snapshots; no run clocks outside module indexes.

Objects use a deterministic field table: [shape-id, ...values]; lists start with null.
Each encoded record occupies one line in a valid JSON array. Index byte ranges allow
Pages clients to retrieve a single stock, without downloading a market's history.
Shape ids are content hashes, so adding a shape never renumbers existing records.
Legacy shards are read only for the one-time, lossless migration.
"""

import hashlib
import json
import os
from pathlib import Path
from research_io import read, semantic

INDEX = {
    "volume": "volume",
    "institutions": "institutions",
    "sec": "sec-radar",
    "turnover": "institutions",
}
MARKETS = {
    "volume": ("TW", "US"),
    "institutions": ("TW", "TWO"),
    "sec": ("US",),
    "turnover": ("TW", "TWO"),
}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def encode(value, shapes):
    if isinstance(value, dict):
        keys = sorted(value)
        key = hashlib.sha256(dumps(keys).encode()).hexdigest()[:12]
        if key in shapes and shapes[key] != keys:
            raise ValueError("Field table hash collision")
        shapes[key] = keys
        return [key] + [encode(value[k], shapes) for k in keys]
    if isinstance(value, list):
        return [None] + [encode(v, shapes) for v in value]
    return value


def decode(value, shapes):
    if not isinstance(value, list):
        return value
    if value[0] is None:
        return [decode(v, shapes) for v in value[1:]]
    keys = shapes[value[0]]
    if len(keys) != len(value) - 1:
        raise ValueError("Invalid research field table")
    return {k: decode(v, shapes) for k, v in zip(keys, value[1:])}


def market_for(module, key):
    if module == "volume":
        return "TW" if key.endswith((".TW", ".TWO")) else "US"
    return "US" if module == "sec" else key.split(".")[-1]


class MarketStore:
    def __init__(self, root, module):
        self.root, self.module = Path(root), module
        index = read(self.root / (INDEX[module] + ".json"), {})
        storage = index.get(
            "turnoverStorage" if module == "turnover" else "storage", {}
        )
        self.records = {m: {} for m in MARKETS[module]}
        for market, spec in storage.get("markets", {}).items():
            data = read(self.root / spec["path"])
            if data is None:
                raise ValueError("Missing market snapshot: " + spec["path"])
            keys = sorted(spec["records"])
            if len(keys) != len(data):
                raise ValueError("Market snapshot record count mismatch")
            self.records[market] = {
                k: decode(v, spec["shapes"]) for k, v in zip(keys, data)
            }
        if not storage:
            for path in (self.root / module).glob("**/*.json"):
                market = (
                    path.parent.name
                    if module == "turnover"
                    else market_for(module, path.stem)
                )
                value = read(path)
                # TW is also a real US ticker. Detect the legacy record by its content,
                # never by reserving ticker names that happen to equal market names.
                if market in self.records and isinstance(value, dict):
                    self.records[market][path.stem] = semantic(value)

    def get(self, key, default=None, market=None):
        return self.records[market or market_for(self.module, key)].get(key, default)

    def put(self, key, value, market=None):
        self.records[market or market_for(self.module, key)][key] = semantic(value)

    def save(self):
        manifest = {"format": "research-market-v1", "markets": {}}
        for market, records in self.records.items():
            shapes, offsets, parts, pos = {}, {}, [b"[\n"], 2
            for i, key in enumerate(sorted(records)):
                body = dumps(encode(semantic(records[key]), shapes)).encode()
                offsets[key] = [pos, len(body), i]
                line = body + (b",\n" if i < len(records) - 1 else b"\n")
                parts.append(line)
                pos += len(line)
            parts.append(b"]\n")
            body = b"".join(parts)
            relative = f"{self.module}/{market}.json"
            path = self.root / relative
            if not path.exists() or path.read_bytes() != body:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(".tmp")
                temp.write_bytes(body)
                os.replace(temp, path)
            manifest["markets"][market] = dict(
                path=relative,
                records=offsets,
                shapes=dict(sorted(shapes.items())),
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        return manifest


def migrate(root):
    """Replace legacy files only after reloading and verifying every semantic record."""
    from research_io import write

    for module in INDEX:
        store = MarketStore(root, module)
        manifest = store.save()
        path = Path(root) / (INDEX[module] + ".json")
        index = read(path, {})
        index["turnoverStorage" if module == "turnover" else "storage"] = manifest
        write(path, index)
        check = MarketStore(root, module)
        assert semantic(store.records) == check.records
        for path in (Path(root) / module).glob("**/*.json"):
            if path.relative_to(Path(root) / module).as_posix() not in [
                m + ".json" for m in MARKETS[module]
            ]:
                path.unlink()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs/report/research",
    )
    migrate(p.parse_args().root)
