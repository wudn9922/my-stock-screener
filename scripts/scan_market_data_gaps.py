"""Read-only XTAI/XNYS archive session-data gap scan, 2021 through completed sessions."""
import argparse
from pathlib import Path

from scan_market_closures import gap_report, scan_archive, write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(args.archive_dir.resolve()):
        raise ValueError('Read-only scan cannot write inside the archive')
    result = gap_report(scan_archive(args.archive_dir))
    write_report(args.out, result)
    print(result['summary'])


if __name__ == '__main__':
    main()
