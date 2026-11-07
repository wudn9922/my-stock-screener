"""Offline replay of public run metadata; never sends notifications or queries APIs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.github/actions/notify-failure'))
from run_policy import decide, timestamp


def replay(snapshot):
    records = []
    for item in snapshot['runs']:
        candidates = [*item['newer']]
        if item.get('crossEventReplacement'):
            candidates.append(item['crossEventReplacement'])
        result = decide(item['run'], item['jobs'], candidates)
        run = item['run']
        visible = [candidate for candidate in candidates if timestamp(candidate['created_at']) <= timestamp(run['updated_at'])]
        at_cancellation = decide(run, item['jobs'], visible)
        records.append(dict(runId=run['id'], workflow=run['name'], event=run['event'],
                            branch=run['head_branch'], createdAt=run['created_at'], url=run['html_url'],
                            jobConclusions=[job['conclusion'] for job in item['jobs']],
                            **result, newerRuns=candidates, cancellationCompletedAt=run['updated_at'],
                            atCancellation=at_cancellation, crossEventReplacement=item.get('crossEventReplacement')))
    notifications = sum(row['notify'] for row in records)
    return dict(version=1, generatedAt=datetime.now(timezone.utc).isoformat(), window=snapshot['window'],
                source=snapshot['source'], queries=snapshot['queries'],
                summary=dict(runs=len(records), suppressed=len(records)-notifications, notifications=notifications,
                             atCancellation=dict(suppressed=sum(not row['atCancellation']['notify'] for row in records),
                                                 notifications=sum(row['atCancellation']['notify'] for row in records))),
                replayScope='Current metadata versus metadata visible by cancellation completion; no future witnesses in the second result.',
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
