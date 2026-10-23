# PR 2: remove regenerated main copies

This PR removes 95 tracked paths (118,440,528 bytes / 112.95 MiB), selected only
by `owners` in `.github/generated-paths.json`, excluding the two frozen PNGs.
The machine-readable [verification record](generated-branch-pr2-verification.json)
lists every removal, size, main blob, current generated blob or producer deletion.
No generator behavior or production setting changes.

All `records` paths and all 9,550 inventory `record` entries remain on main,
including K-line CSVs, world CSVs, financial statements, fundamentals state,
correction journals, hand-curated evidence and existing fixtures. Main history
remains intact, including the `95184ade77cd30c565d3873e9f9050f3ec7c0054` pin.
The pre-cutover policy merge remains an ancestor. This is a normal deletion
commit; it does not reclaim main's historical Git objects.

## Storage proof before removal

- Initial main: `b634d0619b73f8ba0ac15b58c87b9cae17ad26fd`;
  generated: `c4fd63c39e542149607eac7cea4a47f74840be99`.
- 64 removal paths have current counterparts on generated. Their blobs are
  recorded individually; changed outputs need not equal main's stale blobs.
- 31 superseded hashed Atlas assets are intentionally absent. Each main blob
  exactly matches original orphan seed
  `d803d90f01bd60d89314f6cc9fb265d67a4037e2` and the retained migration snapshot
  `bcf6997eab412bda6650cf516181f34e5c7e0728`. Each deletion is traced to an Atlas
  producer commit: `352bfe3095d0ec10fd54d49418eb8ddf87ff9d66`
  ([producer run](https://github.com/wudn9922/my-stock-screener/actions/runs/39846854692))
  or `6c38516d347554ac0e5f26826366e491ee419db1`
  ([producer run](https://github.com/wudn9922/my-stock-screener/actions/runs/39894124969)).
  The evidence verifies the exact blob in each deletion's parent and its
  absence afterwards. Obsolete hashes are not republished.
- `docs/wsj.png` stays at blob `e5003f42783e18418bbb4aa0c228ab1d160a7222`;
  `docs/barrons.png` stays at `353309454edeb45e7199328106a431f28da06188`.
  These frozen compatibility copies preserve historical LINE raw/main links.
  Generated continues to own current screenshots and Pages overlays them.

## Deployed Pages proof

Downloaded the actual `github-pages` deployment artifact, extracted its tar,
and compared every relative path and SHA-256 against a local assembly.

| Deployment | Pinned main / generated | Files | Bytes | Differences |
| --- | --- | ---: | ---: | ---: |
| [39927582637](https://github.com/wudn9922/my-stock-screener/actions/runs/39927582637) | `b634d0619` / `c4fd63c39` | 9,632 | 307,467,035 | 0 |
| [39938009032](https://github.com/wudn9922/my-stock-screener/actions/runs/39938009032) | `dc3cceac5` / `dc71621e5` | 9,634 | 310,063,479 | 0 |

The second comparison includes producers that completed during this work.
Neither comparison needs an exception for outputs changed after cutover.
ZIP hashes and the final full-site SHA-256 manifest digest are in the evidence.
The PR assembly is also compared to the same main/generated assembly after
the removals; no URL or byte changes are allowed for this PR.

At the refreshed main `dc3cceac55bc9b2dfac13cd48e626bea1c219edd`, the tracked
tree is 362,556,519 bytes. Removing the 95 copies leaves 244,115,991 bytes before
the small fixture, test, CI and verification-document additions. The PR body
reports the measured final tree size, including those additions. Git history,
clone transfer and GitHub repository storage do not shrink by this tree delta.

## Readers and checks

Offline unit tests use reviewed research excerpts and retained world CSVs.
The world-candle Python contract rebuilds snapshots in a temporary directory
with an offline source response from the retained records, verifies their
bytes did not change, and checks every candle against those records. Theme
and growth tests use a frozen symbol/name universe so no tests silently skip
when regenerated caches disappear. Browser mocks share the excerpt corpus,
existing theme fixtures and retained world records. No checked-in report cache
is used as a unit-test fallback.

CI runs the entire Python suite twice (records-only and restored generated).
Atlas has an additional records-only unit/typecheck/lint/build/Pages-build job.
Its existing restored job also validates every live research shard, the actual
Pages data smoke check and desktop/mobile/WebKit browser regressions. Storage
CI assembles the PR tree, checks protected records and ignore coverage, and
compares with the latest main assembly; only explicit durable-doc edits may
change a site in other PRs. This PR has none.

Local commands without any generated restore:

```bash
cp docs/report/kline-corrections.json /tmp/test-kline-corrections.json
KLINE_CORRECTIONS_LOG=/tmp/test-kline-corrections.json python -m unittest discover -s tests -v
cd atlas
npm ci
npm test
npm run typecheck
npm run lint
npm run build
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages
npm run refresh:watchlists
```

Operational scripts, previews and full-site acceptance tools still need current
generated inputs, exactly as their CI reader jobs do. Before running those
tools locally, use the existing restore (ignored files stay out of main):

```bash
git fetch origin main
GENERATED_BRANCH_MODE=1 python scripts/generated_branch.py restore
```

No independent review or physical-device Safari sign-off is claimed. Actual
local and CI results, including browser coverage, are reported in the PR.
