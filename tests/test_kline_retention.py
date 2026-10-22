"""Run the real CSV retention hook without importing network clients from main."""
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class KLineRetentionTest(unittest.TestCase):
    def test_stale_delisted_and_invalid_csvs_are_never_deleted_in_either_mode(self):
        source = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "prune_stale_csv")
        namespace = {"DATA_DIR": "data"}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {
                "DELISTED.csv": b"Date,Close\n2000-01-01,1\n",  # would previously be pruned
                "ACTIVE.csv": b"Date,Close\n2099-12-31,2\n",
                "INVALID.csv": b"an unreadable historical record",
                "notes.txt": b"unrelated file",
            }
            for name, body in files.items():
                (root / name).write_bytes(body)
            for mode in ("", "1"):
                with self.subTest(mode=mode), patch.dict(os.environ, {"GENERATED_BRANCH_MODE": mode}):
                    self.assertEqual(namespace["prune_stale_csv"](root, max_age_days=0), 0)
                    self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, files)

    def test_failed_atomic_update_preserves_the_existing_k_line_csv(self):
        import pandas as pd
        source = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write_ohlcv_csv_atomic")
        namespace = {"os": os, "tempfile": tempfile}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        frame = pd.DataFrame({key: [1] for key in ("Open", "High", "Low", "Close", "Volume")})
        with tempfile.TemporaryDirectory() as temp:
            csv = Path(temp) / "RETAINED.csv"
            csv.write_bytes(b"permanent historical bars")
            with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk error")):
                with self.assertRaisesRegex(OSError, "disk error"):
                    namespace["write_ohlcv_csv_atomic"](frame, csv)
            self.assertEqual(csv.read_bytes(), b"permanent historical bars")
            self.assertEqual(list(Path(temp).iterdir()), [csv])


if __name__ == "__main__":
    unittest.main()
