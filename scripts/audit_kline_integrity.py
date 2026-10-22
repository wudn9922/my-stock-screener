"""Rescan every permanent stock/world CSV; quarantine is retained, never rewritten."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import kline_integrity as kline


def audit(root=ROOT / 'data', log=None):
    results, total = [], 0
    for path in sorted(Path(root).rglob('*.csv')):
        frame = pd.read_csv(path, index_col=0, float_precision='round_trip')
        total += len(frame)
        prices = frame[kline.FIELDS[:4]]
        mask = ~((prices > 0).all(axis=1) & (prices < float('inf')).all(axis=1)
                 & (prices.Low <= prices[['Open','Close']].min(axis=1))
                 & (prices.High >= prices[['Open','Close']].max(axis=1)))
        blocked = kline.quarantined_dates(path.stem, log)
        for day in frame.index[mask]:
            results.append(dict(file=path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
                                symbol=path.stem,date=str(day),quarantined=str(day) in blocked,
                                values=kline.bar_values(frame.loc[day])))
    return dict(version=1,generatedAt=datetime.now(timezone.utc).isoformat(),
                files=len(list(Path(root).rglob('*.csv'))),rows=total,
                unquarantinedViolations=sum(not r['quarantined'] for r in results),
                quarantinedViolations=sum(r['quarantined'] for r in results),violations=results,
                correctionLogSha256=hashlib.sha256(Path(log or kline.LOG_PATH).read_bytes()).hexdigest())


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'docs/report/kline-integrity-verification.json')
    args=parser.parse_args()
    report=audit()
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='violations'}))
    raise SystemExit(bool(report['unquarantinedViolations']))
