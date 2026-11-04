"""US valuation must not silently shrink when the symbol directory or quote batches are partial."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atlas/scripts"))
import build_valuation_us as valuation


class ShrinkGuardTests(unittest.TestCase):
    def test_losing_a_whole_exchange_keeps_the_previous_file(self):
        # 2026-11-02: otherlisted.txt was unavailable, 5,122 -> 3,059 records.
        previous = {f"S{i}": {} for i in range(5122)}
        with self.assertRaises(RuntimeError):
            valuation.check_shrink({f"S{i}": {} for i in range(3059)}, previous)

    def test_ordinary_churn_and_first_build_pass(self):
        previous = {f"S{i}": {} for i in range(5122)}
        valuation.check_shrink({f"S{i}": {} for i in range(5000)}, previous)
        valuation.check_shrink({f"S{i}": {} for i in range(3059)}, {})


if __name__ == "__main__":
    unittest.main()
