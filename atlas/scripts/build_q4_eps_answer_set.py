"""
The Q4 EPS answer set (handoff §0, amendment 1 §1): the SEC direct grade-A Q4 values at one git ref.

  python atlas/scripts/build_q4_eps_answer_set.py --ref 95184ade77cd30c565d3873e9f9050f3ec7c0054 --out PATH

The tree is read only with `git ls-tree` and `git show <commit>:<path>`, never the working tree.

A row is a US file (market != "TW", key not ending in .TW or .TWO) with a Q4 period (quarterly.fq[i] == 4) whose
quarterly.epsQuality[i][field] is "A":
- value: secQ4EpsFacts[end][field].value, the SEC fact (the answer, amendment 1 §1);
- stored: quarterly.values[field][i], kept as evidence only;
- netIncome: quarterly.values.netIncome[i]; fy: quarterly.fy[i];
- accession, filed, form: from the SEC fact.

rows is the epsDiluted set and rowsBasic the epsBasic set, each sorted by (key, end). The output is
json.dumps(..., ensure_ascii=False, indent=1, sort_keys=True) + "\n", so a rebuild is byte-identical.
"""
import argparse
import collections
import json
import os
import subprocess
import sys

FINANCIALS = "docs/report/financials/"
TW_SUFFIXES = (".TW", ".TWO")
FIELDS = ("epsDiluted", "epsBasic")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BuildError(Exception):
    pass


def git(repo, *args):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def resolve_commit(repo, ref):
    return git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}").decode("ascii").strip()


def financial_paths(repo, commit):
    raw = git(repo, "ls-tree", "-r", "-z", "--name-only", commit, FINANCIALS)
    return sorted(p for p in raw.decode("utf-8").split("\0") if p.endswith(".json"))


def load_document(repo, commit, path):
    raw = git(repo, "show", f"{commit}:{path}")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise BuildError(f"{path}: not valid JSON ({exc})")


def is_us(key, document):
    return document.get("market") != "TW" and not key.endswith(TW_SUFFIXES)


def at(values, index):
    return values[index] if isinstance(values, list) and index < len(values) else None


def rows_of_field(key, document, field):
    quarterly = document.get("quarterly")
    if not isinstance(quarterly, dict):
        return []
    ends = quarterly.get("end")
    fqs = quarterly.get("fq")
    fys = quarterly.get("fy")
    quality = quarterly.get("epsQuality")
    values = quarterly.get("values") if isinstance(quarterly.get("values"), dict) else {}
    facts = document.get("secQ4EpsFacts") if isinstance(document.get("secQ4EpsFacts"), dict) else {}
    rows = []
    for i, fq in enumerate(fqs if isinstance(fqs, list) else []):
        if fq != 4:
            continue
        grade = at(quality, i)
        if not isinstance(grade, dict) or grade.get(field) != "A":
            continue
        end = at(ends, i)
        by_end = facts.get(end) if isinstance(end, str) else None
        fact = by_end.get(field) if isinstance(by_end, dict) else None
        value = fact.get("value") if isinstance(fact, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BuildError(f"{key} {end}: no SEC fact with a number for {field}")
        rows.append({
            "key": key,
            "end": end,
            "fy": at(fys, i),
            "value": value,
            "stored": at(values.get(field), i),
            "netIncome": at(values.get("netIncome"), i),
            "accession": fact.get("accession"),
            "filed": fact.get("filed"),
            "form": fact.get("form"),
        })
    return rows


def collect(ref, repo=REPO):
    """The answer set at `ref` as a dict (the file is rendered from it by render)."""
    commit = resolve_commit(repo, ref)
    us_files = 0
    rows = {field: [] for field in FIELDS}
    for path in financial_paths(repo, commit):
        key = path[len(FINANCIALS):-len(".json")]
        if "/" in key:
            raise BuildError(f"unexpected nested path {path}")
        document = load_document(repo, commit, path)
        if not is_us(key, document):
            continue
        us_files += 1
        for field in FIELDS:
            rows[field].extend(rows_of_field(key, document, field))
    for field in FIELDS:
        rows[field].sort(key=lambda r: (r["key"], r["end"]))
        pairs = [(r["key"], r["end"]) for r in rows[field]]
        if len(pairs) != len(set(pairs)):
            raise BuildError(f"duplicate (key, end) in {field}")
    diluted, basic = rows["epsDiluted"], rows["epsBasic"]
    return {
        "ref": commit[:8],
        "commit": commit,
        "field": "epsDiluted",
        "counts": {
            "usFiles": us_files,
            "rows": len(diluted),
            "keys": len({r["key"] for r in diluted}),
            "rowsBasic": len(basic),
            "keysBasic": len({r["key"] for r in basic}),
            "byEndYear": dict(collections.Counter(r["end"][:4] for r in diluted)),
        },
        "rows": diluted,
        "rowsBasic": basic,
    }


def render(answer):
    return (json.dumps(answer, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the Q4 EPS answer set from one git ref.")
    parser.add_argument("--ref", required=True, help="git ref to read (the committed set is 95184ade)")
    parser.add_argument("--out", required=True, help="output path; written only after a full build")
    args = parser.parse_args(argv)
    try:
        answer = collect(args.ref)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with open(args.out, "wb") as f:
        f.write(render(answer))
    counts = answer["counts"]
    print(f"answer set {answer['ref']}: {counts['rows']} diluted rows in {counts['keys']} keys, "
          f"{counts['rowsBasic']} basic rows in {counts['keysBasic']} keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
