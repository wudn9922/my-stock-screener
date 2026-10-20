from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(PACKAGE_DIR))

from market_lab.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    here = Path(__file__).resolve().parent
    repo_root = here.parents[1]
    parser = argparse.ArgumentParser(description="Run the isolated Market Research Lab analysis")
    parser.add_argument("--data-dir", default=str(repo_root / "data"))
    parser.add_argument("--financial-dir", default=str(repo_root / "docs/report/financials"))
    parser.add_argument("--tw-fundamentals", default=str(repo_root / "docs/report/fundamentals/tw.json"))
    parser.add_argument("--financial-inventory", default="")
    parser.add_argument("--output-dir", default=str(here / "reports"))
    parser.add_argument("--cache-dir", default=str(here / ".cache"))
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--resume", action="store_true", help="reuse event and inventory cache when all input hashes match")
    parser.add_argument("--no-cache", action="store_true", help="force a complete recomputation")
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args()
    run_pipeline(
        data_dir=Path(args.data_dir),
        financial_dir=Path(args.financial_dir) if args.financial_dir else None,
        tw_fundamentals=Path(args.tw_fundamentals) if args.tw_fundamentals else None,
        financial_inventory_csv=Path(args.financial_inventory) if args.financial_inventory else None,
        output_dir=Path(args.output_dir),
        cache_dir=Path(args.cache_dir),
        source_commit=args.source_commit,
        resume=bool(args.resume and not args.no_cache),
        bootstrap_replicates=max(50, int(args.bootstrap_replicates)),
        spec_dir=here / "config",
        methodology_path=here / "methodology.md",
    )


if __name__ == "__main__":
    main()
