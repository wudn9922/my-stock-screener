"""Rebuild the delivery coverage receipt from published real snapshots, without network I/O."""

import json, sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_io import read, write, universe
from research_io import ROOT, stamp

r = ROOT / "docs/report/research"
u = universe()
raw = read(ROOT / "docs/report/universe.json")
health = {"methodVersion": "research-v1", "generatedAt": stamp()}
for market, symbols in u.items():
    rows = [read(r / "volume" / f"{s}.json") for s in symbols]
    rows = [x for x in rows if x]
    health[market] = {
        "listed": raw["markets"][market]["counts"]["listed"],
        "universeRows": len(symbols),
        "priceVolumeSnapshots": len(rows),
        "coveragePctOfRows": len(rows) / len(symbols) * 100,
        "coveragePctOfListed": len(rows)
        / raw["markets"][market]["counts"]["listed"]
        * 100,
        "excludedByExistingUniverse": raw["markets"][market]["counts"]["noVolume"],
        "rvol20Ready": sum(x["latest"]["rvol"] is not None for x in rows),
        "full60VolumeObservations": sum(
            x["volumeAverages"]["60"]["observations"] == 60 for x in rows
        ),
        "stale": sum(x["status"] == "stale" for x in rows),
        "dates": dict(sorted(Counter(x["asOf"] for x in rows).items())),
        "latest": max(x["asOf"] for x in rows),
    }
    if market == "TW":
        flows = [read(r / "institutions" / f"{s}.json") for s in symbols]
        flows = [x for x in flows if x]
        health[market]["institutions"] = {
            "snapshots": len(flows),
            "full60ForeignWindows": sum(
                x["summary"]["foreign"]["windows"]["60"]["complete"] for x in flows
            ),
            "cost20MatchedAllDays": sum(
                x["costs"]["foreign"]["20"]["observations"] == 20 for x in flows
            ),
            "cost20ReferenceAvailable": sum(
                x["costs"]["foreign"]["20"]["reference"] is not None for x in flows
            ),
        }
chips = read(ROOT / "docs/report/tw-chips.json")
large = [
    e["largeHolders"][-1]
    for s, e in chips["stocks"].items()
    if s in u["TW"] and e.get("largeHolders")
]
health["TDCC"] = {
    "latest": max(x["date"] for x in large),
    "coveredUniverse": len(large),
    "maxLargeHolderWeeks": max(
        len(e.get("largeHolders", [])) for e in chips["stocks"].values()
    ),
}
sec = read(r / "sec-radar.json")
health["SEC"] = {
    "events": len(sec["events"]),
    "issuersWithEvents": len(set(x["cik"] for x in sec["events"])),
    "checkedSymbols": len(sec["checkedSymbols"]),
    "mapped": sec["mapped"],
    "indexedDates": sec["indexedDates"],
    "parsedEvents": sum(x["summaryStatus"] == "parsed" for x in sec["events"]),
    "amendments": sum(x["amendment"] for x in sec["events"]),
    "status": sec["status"],
    "missingReason": sec["missingReason"],
}
a = read(r / "alerts.json")
health["alerts"] = {
    "events": len(a["events"]),
    "counts": dict(Counter(x["market"] + ":" + x["kind"] for x in a["events"])),
    "sources": {
        k: {f: s.get(f) for f in ("status", "asOf", "historyFrom")}
        for k, s in a["sources"].items()
    },
}
health["world"] = {
    k: {f: s.get(f) for f in ("latest", "asOf", "frequency", "status", "unit")}
    for k, s in read(r / "world-assets.json")["series"].items()
}
health["turnover"] = {
    m: {
        "dates": len(list((r / "turnover" / m).glob("*.json"))),
        "first": min(
            (p.stem for p in (r / "turnover" / m).glob("*.json")), default=None
        ),
        "latest": max(
            (p.stem for p in (r / "turnover" / m).glob("*.json")), default=None
        ),
    }
    for m in ("TW", "TWO")
}
health["TW"]["officialADV20Complete"] = sum(
    (read(r / "volume" / f"{s}.json") or {})
    .get("amountAverages", {})
    .get("20", {})
    .get("officialObservations")
    == 20
    for s in u["TW"]
)
write(ROOT / "docs/research-verification/coverage.json", health)
print(json.dumps(health, ensure_ascii=False, indent=2))
