#!/usr/bin/env python3
"""Read-only bond inputs → generated JP spreads and monthly US/JP yield-band parameters.

Ports the user's yield_band.py formulas. Never writes rates.json, prices or archives.
BOJ target history uses effective dates; attached fit coefficients remain the bootstrap.
"""
import argparse
import calendar
import csv
import io
import logging
import math
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import build_rates as fred

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / 'atlas/data/yield-band-params.json'
MOF_URL = 'https://www.mof.go.jp/jgbs/reference/interest_rate/'
MOF_FILES = ['data/jgbcm_all.csv', 'jgbcm.csv']
# Historical entries originate in the attachment; recent effective dates verified against BOJ PDFs.
BOJ_CHANGES = [
    ('2006-07-14', .25), ('2007-02-21', .50), ('2008-10-31', .30), ('2008-12-19', .10),
    ('2016-02-16', -.10), ('2024-03-21', .10), ('2024-08-01', .25), ('2025-01-27', .50),
    ('2025-12-22', .75), ('2026-06-17', 1.), ('2026-09-24', 1.25),
]
BOJ_SOURCE = 'https://www.boj.or.jp/en/mopo/mpmdeci/state_2026/index.htm'
Z80 = 1.2816


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def policy_on(day):
    return next(((d, v) for d, v in reversed(BOJ_CHANGES) if d <= day), None)


def iso_japanese(value):
    text = value.strip()
    match = re.fullmatch(r'([SHR])(\d+)\.(\d+)\.(\d+)', text)
    if match:
        era, year, month, day = match.groups()
        return date({'S': 1925, 'H': 1988, 'R': 2018}[era] + int(year), int(month), int(day)).isoformat()
    return date.fromisoformat(text.replace('/', '-')).isoformat()


def parse_mof(body):
    text = body.decode('cp932') if isinstance(body, bytes) else body
    rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    header = next((i for i, row in enumerate(rows) if row and row[0].strip() == '基準日'), None)
    if header is None or '10年' not in rows[header]:
        raise ValueError('missing JGB 10Y column')
    column = rows[header].index('10年')
    points = {}
    for row in rows[header + 1:]:
        try:
            day, value = iso_japanese(row[0]), float(row[column])
            if finite(value):
                points[day] = value
        except (ValueError, IndexError):
            continue
    if not points:
        raise ValueError('no valid JGB observations')
    return points


def fit_band(rows):
    """[{date,y10,pol}] → same OLS / residual n−2 / spread n−1 math as the attachment."""
    if len(rows) < 3 or any(not finite(r[k]) for r in rows for k in ('pol', 'y10')):
        raise ValueError('insufficient finite pairs')
    n = len(rows)
    mx, my = sum(r['pol'] for r in rows) / n, sum(r['y10'] for r in rows) / n
    sxx = sum((r['pol'] - mx) ** 2 for r in rows)
    syy = sum((r['y10'] - my) ** 2 for r in rows)
    if sxx <= 0 or syy <= 0:
        raise ValueError('constant series')
    b = sum((r['pol'] - mx) * (r['y10'] - my) for r in rows) / sxx
    a = my - b * mx
    sse = sum((r['y10'] - a - b * r['pol']) ** 2 for r in rows)
    spread = [r['y10'] - r['pol'] for r in rows]
    sm = sum(spread) / n
    sd = math.sqrt(sse / (n - 2))
    if sd <= 0:
        raise ValueError('zero residual deviation')
    return dict(a=a, b=b, sd=sd, r2=1 - sse / syy, n=n,
                spread_mean=sm, spread_sd=math.sqrt(sum((s - sm) ** 2 for s in spread) / (n - 1)),
                first=rows[0]['date'], last=rows[-1]['date'])


def monthly_mean(points):
    months = defaultdict(list)
    for day, value in points:
        months[day[:7] + '-01'].append(value)
    return {day: sum(values) / len(values) for day, values in months.items()}


def policy_month(month):
    first = date.fromisoformat(month)
    days = calendar.monthrange(first.year, first.month)[1]
    return sum((policy_on((first + timedelta(days=i)).isoformat()) or ('', 0.))[1] for i in range(days)) / days


def fit_rows(country, series, today):
    cutoff = today.replace(day=1).isoformat()
    if country == 'US':
        ff = dict(series.get('FEDFUNDS', []))
        targets = dict(series.get('DFEDTAR', []))
        targets.update(dict(series.get('DFEDTARU', [])))
        pol = {d: v for d, v in ff.items() if d < '1982-10-01'}
        pol.update({d: v for d, v in monthly_mean(sorted(targets.items())).items() if d >= '1982-10-01'})
        yields = series.get('GS10', [])
    else:
        pol = {d: v for d, v in series.get('IRSTCI01JPM156N', []) if d < '2006-07-01'}
        yields = series.get('IRLTLT01JPM156N', [])
        pol.update({d: policy_month(d) for d, _ in yields if '2006-07-01' <= d < cutoff})
        yields = [(d, v) for d, v in yields if d >= '1989-01-01']
    return [{'date': d, 'y10': v, 'pol': pol[d]} for d, v in yields if d < cutoff and d in pol]


def japan_payload(points, previous, today, now, missing):
    # Retain prior official observations when one MOF download is unavailable; never regress a date.
    for block in ('weekly', 'daily'):
        part = (previous or {}).get(block) or {}
        for day, value in zip(part.get('dates', []), (part.get('series') or {}).get('JGB10', [])):
            if finite(value):
                points.setdefault(day, value)
    valid = sorted((d, v) for d, v in points.items() if d <= today.isoformat() and policy_on(d))
    if not valid:
        return None
    asof, value = valid[-1]
    effective, target = policy_on(asof)
    weeks = {}
    for day, _ in valid:
        dt = date.fromisoformat(day)
        weeks[dt.isocalendar()[:2]] = day
    values = dict(valid)
    def block(dates):
        return {'dates': dates, 'series': {'JGB10': [values[d] for d in dates], 'BOJ_TARGET': [policy_on(d)[1] for d in dates]}}
    return dict(version=1, generatedAt=now, asOf=asof,
                tenYear={'value': value, 'date': asof, 'source': MOF_URL + 'index.htm'},
                policy={'value': target, 'date': effective, 'source': BOJ_SOURCE},
                spreadPp=round(value - target, 6), missing=missing,
                weekly=block(list(weeks.values())), daily=block([d for d, _ in valid[-260:]]))


def http_get(url, params=None, timeout=(10, 20)):
    import requests
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    response = requests.get(url, params=params, timeout=timeout, headers={'User-Agent': fred.USER_AGENT})
    return response.status_code, response.content if url.startswith(MOF_URL) else response.text


def full_history_get(get):
    """Reuse FRED retries/auth without the existing UI collector's 2003 history cutoff."""
    def fetch(url, params, timeout):
        params = dict(params or {})
        for key in ('cosd', 'observation_start'):
            if key in params:
                params[key] = '1954-01-01'
        return get(url, params, timeout)
    return fetch


def run(out_dir, get=None, today=None, refit=False, now=None):
    get = get or http_get
    today = today or datetime.now(timezone.utc).date()
    now = now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    out = Path(out_dir)
    params_file, jp_file = out / 'yield-band-params.json', out / 'jp-rates.json'
    params = fred.read_json(params_file) or fred.read_json(SEED)
    if not params:
        raise ValueError('missing yield-band bootstrap')
    previous = fred.read_json(jp_file)
    points, missing = {}, []
    for path in MOF_FILES:
        try:
            status, body = get(MOF_URL + path, None, (10, 20))
            if status != 200:
                raise ValueError('source status')
            points.update(parse_mof(body))
        except Exception as exc:
            missing.append(path)
            print(f'MOF {path}: unavailable ({type(exc).__name__}); retaining prior observations')
    jp = japan_payload(points, previous, today, now, missing)
    # Each country independently retries a missing monthly update; incomplete current months never enter OLS.
    needed = [c for c in ('US', 'JP') if refit or params.get(c, {}).get('fittedAt', params.get('asof', ''))[:7] < today.isoformat()[:7]]
    if needed:
        ids = []
        if 'US' in needed:
            ids += ['FEDFUNDS', 'DFEDTAR', 'DFEDTARU', 'GS10']
        if 'JP' in needed:
            ids += ['IRSTCI01JPM156N', 'IRLTLT01JPM156N']
        fetchers = fred.make_fetchers(full_history_get(get), os.environ.get('FRED_API_KEY'), budget=240)
        series, _ = fred.load_series(ids, fetchers, {})
        for country in needed:
            try:
                rows = fit_rows(country, series, today)
                if (len(rows) < 120 or rows[-1]['date'] < params[country]['last']
                        or rows[0]['date'] > params[country]['first']
                        or (not refit and rows[-1]['date'] == params[country]['last'])):
                    raise ValueError('incomplete or regressive fit history')
                values = fit_band(rows)
                values['fittedAt'] = today.isoformat()
                params[country] = {k: round(v, 8) if isinstance(v, float) else v for k, v in values.items()}
            except Exception as exc:
                print(f'{country}: monthly fit unavailable ({type(exc).__name__}); keeping previous parameters')
    # Do not restamp old model dates or use the attachment's historical quotes as today's prices.
    fred.write_atomic(str(params_file), params)
    if jp:
        fred.write_atomic(str(jp_file), jp)
        print(f"Japan spread updated through {jp['asOf']}; band fits: " + ', '.join(f"{c} {params[c].get('fittedAt', params.get('asof'))}" for c in ('US', 'JP')))
        return 0
    print('Japan rates not updated: no official or retained observations')
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', default=str(ROOT / 'docs/report'))
    parser.add_argument('--refit', action='store_true', help='Refit monthly parameters using completed months only')
    args = parser.parse_args()
    return run(args.out_dir, refit=args.refit)


if __name__ == '__main__':
    raise SystemExit(main())
