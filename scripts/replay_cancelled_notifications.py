"""Offline replay of public run metadata; never sends notifications or queries APIs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.github/actions/notify-failure'))
from run_policy import decide


def replay(snapshot):
    records = []
    for item in snapshot['runs']:
        result = decide(item['run'], item['jobs'], item['newer'])
        run = item['run']
        records.append(dict(runId=run['id'], workflow=run['name'], event=run['event'],
                            branch=run['head_branch'], createdAt=run['created_at'], url=run['html_url'],
                            jobConclusions=[job['conclusion'] for job in item['jobs']],
                            **result, newerRuns=item['newer']))
    notifications = sum(row['notify'] for row in records)
    return dict(version=1, generatedAt=datetime.now(timezone.utc).isoformat(), window=snapshot['window'],
                source=snapshot['source'], queries=snapshot['queries'],
                summary=dict(runs=len(records), suppressed=len(records)-notifications, notifications=notifications),
                records=records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.input.resolve() == args.out.resolve():
        raise ValueError('Replay must preserve the input snapshot')
    result = replay(json.loads(args.input.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(args.out)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
