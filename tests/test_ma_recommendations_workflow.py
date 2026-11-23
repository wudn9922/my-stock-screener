"""Offline execution checks for the MA workflow's trial and publishing modes."""
import ast
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ma-recommendations.yml").read_text(encoding="utf-8")
DOC = yaml.load(WORKFLOW, Loader=yaml.BaseLoader)
STEPS = {step["name"]: step for step in DOC["jobs"]["build"]["steps"]}
STUB = textwrap.dedent("""\
    import json, sys
    from pathlib import Path
    args = sys.argv[1:]
    Path('args.txt').write_text(repr(args))
    output = Path(args[args.index('--out') + 1])
    output.mkdir(parents=True, exist_ok=True)
    index = dict(requested=2, updated=2, symbols=['AAPL', 'MSFT'], failed=[], referenceAsof='2026-11-01')
    (output / 'index.json').write_text(json.dumps(index))
""")


class MaRecommendationsWorkflowTests(unittest.TestCase):
    def test_schedule_and_trial_publish_conditions(self):
        self.assertEqual(DOC["on"]["schedule"][0]["cron"], "17 7 2 * *")
        self.assertIn("台灣時間約 15:17（UTC 07:17）", WORKFLOW)
        self.assertEqual(DOC["env"]["MA_TRIAL"],
                         "${{ github.event_name == 'workflow_dispatch' && github.event.inputs.limit != '' }}")
        self.assertEqual(STEPS["Restore generated inputs"]["if"],
                         "env.MA_TRIAL != 'true' && vars.GENERATED_BRANCH_MODE == '1'")
        self.assertEqual(STEPS["Restore trial ticker input"]["if"],
                         "env.MA_TRIAL == 'true' && vars.GENERATED_BRANCH_MODE == '1'")
        self.assertIn("git show FETCH_HEAD:docs/report/universe.json > .cache/ma-universe.json",
                      STEPS["Restore trial ticker input"]["run"])
        self.assertEqual(STEPS["Publish owned outputs to generated"]["if"],
                         "env.MA_TRIAL != 'true' && vars.GENERATED_BRANCH_MODE == '1' "
                         "&& steps.generated_restore.outcome == 'success'")
        self.assertEqual(STEPS["Commit and Push changes"]["if"],
                         "env.MA_TRIAL != 'true' && vars.GENERATED_BRANCH_MODE != '1'")

    def test_trial_and_formal_commands_with_current_and_legacy_checkout(self):
        for modern, limit, prune in ((True, "2", "1"), (True, "2", "0"), (True, "", "1"),
                                     (True, "", "0"), (False, "2", "1"), (False, "", "0")):
            with self.subTest(modern=modern, limit=limit, prune=prune), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                production = root / "docs/report/ma-selector"
                production.mkdir(parents=True)
                before = {"index.json": b"existing production index", "OLD.json": b"existing recommendation"}
                for name, body in before.items():
                    (production / name).write_bytes(body)
                checkout = root / "_ma-selector/scripts"
                checkout.mkdir(parents=True)
                (checkout / "build_recommendations.py").write_text(STUB + ("\n# --shard\n" if modern else ""))
                summary = root / "summary.md"
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", STEPS["Build recommendations"]["run"]],
                                        cwd=root, capture_output=True, text=True,
                                        env={**os.environ, "LIMIT": limit, "PRUNE": prune, "SHARD_INPUT": "1",
                                             "MAX_MISSING": "1500", "MA_TRIAL": "true" if limit else "false",
                                             "GITHUB_STEP_SUMMARY": str(summary)})
                self.assertEqual(result.returncode, 0, result.stderr)
                args = ast.literal_eval((root / "args.txt").read_text())
                self.assertEqual(args[args.index("--out") + 1], ".cache/ma-trial" if limit else "docs/report/ma-selector")
                self.assertEqual("--no-prune" in args, bool(limit) or (modern and prune != "1"))
                self.assertEqual("--limit" in args, bool(limit))
                self.assertIn("--tickers-file" if modern else "--from-dir", args)
                self.assertEqual("試跑，未發布" in summary.read_text(), bool(limit))
                self.assertIn("要求 2 檔、更新 2 檔、可用 2 檔", summary.read_text())
                if limit:
                    self.assertEqual(args[args.index("--limit") + 1], limit)
                    self.assertEqual({p.name: p.read_bytes() for p in production.iterdir()}, before)
