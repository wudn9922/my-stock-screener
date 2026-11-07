"""Import regressions must run before any test can add scripts/ to sys.path."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RootScriptImportsTest(unittest.TestCase):
    def run_python(self, *args, cwd=ROOT):
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, *args], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_daily_report_imports_from_repository_root(self):
        self.run_python('-c', 'import report_metrics; assert callable(report_metrics.compute_stock_metrics)')

    def test_market_sessions_package_import_from_repository_root(self):
        self.run_python('-c', 'import scripts.market_sessions as m; assert callable(m.valid_closure_proof)')

    def test_market_sessions_direct_import_from_scripts_directory(self):
        self.run_python('-c', 'import market_sessions as m; assert callable(m.valid_closure_proof)',
                        cwd=ROOT / 'scripts')

    def test_repair_package_import_from_repository_root(self):
        self.run_python('-c', 'import scripts.repair_kline_archive as m; assert callable(m.plan_backfill)')

    def test_repair_command_supports_script_and_module_entrypoints(self):
        for args in [('scripts/repair_kline_archive.py', '--help'),
                     ('-m', 'scripts.repair_kline_archive', '--help')]:
            with self.subTest(args=args):
                self.assertIn('--archive-dir', self.run_python(*args).stdout)
