"""Read public IPO/extended quotes. Writes only new display snapshots; never K-lines/prices records.

No paid key. Nasdaq dates are estimates. Coverage is bounded and explicit; unchanged/failed
observations retain their actual timestamps. Raw response bodies are never published or logged.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo
import requests

ROOT = Path(__file__).resolve().parents[1]
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")

def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
    tmp.replace(path)

def fetch(url):
    r = requests.get(url, timeout=12, headers={'Accept': 'application/json', 'User-Agent': 'Atlas public-data research'})
    r.raise_for_status()
    return r.json()

def ipo_companies(payload, checked_at):
    data = payload.get('data')
    if not isinstance(data, dict):
        raise ValueError('IPO source did not provide a calendar')
    output = {}
    for section, status in [('filed', '已申報，掛牌日待確認'), ('upcoming', '預計上市（日期可能變動）'), ('priced', '已定價，掛牌狀態請查官方公告')]:
        table = data.get(section) or {}
        if section == 'upcoming':
            table = table.get('upcomingTable', table)
        for row in table.get('rows') or []:
            if not isinstance(row, dict):
                continue
            name = row.get('companyName')
            if not isinstance(name, str) or not name.strip():
                continue
            symbol = str(row.get('proposedTickerSymbol') or '')
            if (section == 'priced' and not symbol) or (symbol and not SYMBOL.fullmatch(symbol)):
                continue
            # Nasdaq expectedPriceDate is a pricing estimate, not a listing date.
            expected_pricing = row.get('expectedPriceDate') if section == 'upcoming' else None
            key = 'nasdaq-' + str(row.get('dealID') or symbol or name)
            output[key] = {'id': key, 'name': name[:250], 'symbol': symbol,
                'aliases': [], 'status': status, 'exchange': str(row.get('proposedExchange') or '')[:100],
                'filedDate': row.get('filedDate'), 'listingDate': row.get('listingDate'),
                'expectedPricingDate': expected_pricing, 'pricingDate': row.get('pricedDate') if section == 'priced' else None, 'price': row.get('proposedSharePrice'),
                'checkedAt': checked_at, 'sourceUrl': 'https://www.nasdaq.com/market-activity/ipos'}
    return list(output.values())

def extended_quote(payload, symbol, mode, now):
    page = f'https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/' + ('pre-market' if mode == 'pre' else 'after-hours')
    base = {'status': 'unavailable', 'price': None, 'asOf': None, 'sourceUrl': page, 'reason': '來源未提供此時段成交報價'}
    data = payload.get('data')
    if not isinstance(data, dict):
        return base
    trades = (data.get('tradeDetailTable') or {}).get('rows') or []
    update = ' '.join(data.get('lastUpdateInfo') or [])
    date = re.search(r'Data last updated ([A-Z][a-z]{2} \d{1,2}, \d{4})', update)
    if not trades or not date:
        return base
    row = trades[0]
    # Time is the last actual trade, not the page's refresh time or the regular close.
    try:
        stamp = datetime.strptime(date[1] + ' ' + row['time'], '%b %d, %Y %H:%M:%S').replace(tzinfo=ZoneInfo('America/New_York'))
        price_text = str(row['price']).replace('$', '').replace(',', '')
        if not re.fullmatch(r'\d+(?:\.\d+)?', price_text):
            return base
        price = float(price_text)
        minute = stamp.hour * 60 + stamp.minute
        window = (240 <= minute < 570) if mode == 'pre' else (960 <= minute <= 1200)
        if not math.isfinite(price) or price <= 0 or not window or stamp.timestamp() > now.timestamp() + 300:
            return base
    except (KeyError, TypeError, ValueError):
        return base
    return {**base, 'status': 'ok', 'price': price, 'asOf': stamp.astimezone(timezone.utc).isoformat(), 'reason': None}

def collect(root=ROOT, getter=fetch, now=None, limit=240, budget=150):
    now = now or datetime.now(timezone.utc)
    checked = now.replace(microsecond=0).isoformat()
    dest = root / 'docs/report'
    # Curated verified records are a floor, never stamped fresh just because a run happened.
    seeds = read(root / 'config/ipo-watch.json', [])
    previous = read(dest / 'ipo-watch.json', {})
    companies = {}
    for item in previous.get('companies', []) + seeds:
        if item.get('checkedAt', '') >= companies.get(item['id'], {}).get('checkedAt', ''):
            companies[item['id']] = item
    try:
        calendar = getter(f'https://api.nasdaq.com/api/ipo/calendar?date={now:%Y-%m}')
        fresh = ipo_companies(calendar, checked)
        # Remove only this source's prior calendar entries; curated issuer filings are retained.
        companies = {k: v for k, v in companies.items() if not k.startswith('nasdaq-')}
        for item in fresh:
            curated = next((old for old in companies.values() if not old['id'].startswith('nasdaq-')
                and old['symbol'] == item['symbol'] and old['name'].casefold() in item['name'].casefold()), None)
            if curated:
                # Preserve issuer aliases/official link while enriching with actually published terms.
                merged = {**curated, **{k: v for k, v in item.items() if v not in (None, '', [])}}
                merged.update(id=curated['id'], name=curated['name'], aliases=curated['aliases'])
                companies[curated['id']] = merged
            else:
                companies[item['id']] = item
        for row in ((calendar.get('data') or {}).get('priced') or {}).get('rows') or []:
            for key, company in companies.items():
                if company['symbol'] and company['symbol'] == row.get('proposedTickerSymbol') and company['name'].casefold() in str(row.get('companyName', '')).casefold():
                    companies[key] = {**company, 'status': '已定價，掛牌狀態請查官方公告',
                        'price': row.get('proposedSharePrice') or company['price'], 'pricingDate': row.get('pricedDate'), 'checkedAt': checked,
                        'sourceUrl': 'https://www.nasdaq.com/market-activity/ipos'}
                    # pricedDate is pricing day, not listing day; never substitute one for the other.
        failure = None if fresh else '本月行事曆暫無資料'
    except (requests.RequestException, ValueError, TypeError):
        failure = 'IPO 行事曆更新失敗，保留上次核對資料'
    write(dest / 'ipo-watch.json', {'version': 1, 'generatedAt': checked, 'reason': failure, 'companies': list(companies.values())[:1000]})
    trending = read(dest / 'trending-stocks.json', {}).get('symbols', [])
    report = read(dest / 'latest.json', {})
    symbols = trending + [s for g in report.get('groups', []) for s in [i.get('symbol', '') for i in g.get('items', [])]]
    # No personal watchlists/account data is read. Public report symbols only, with a transparent cap.
    symbols = list(dict.fromkeys(s for s in symbols + ['AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA'] if SYMBOL.fullmatch(s)))[:limit]
    deadline = time.monotonic() + budget
    def one(symbol):
        path = dest / 'extended-quotes' / f'{symbol}.json'
        old = read(path, {})
        output = {'version': 1, 'symbol': symbol, 'checkedAt': checked, 'source': 'Nasdaq 公開延長時段成交快照（非即時）'}
        for mode in ('pre', 'post'):
            if time.monotonic() >= deadline:
                output[mode] = {**old.get(mode, {}), 'status': 'stale', 'reason': '本次收集已達時間上限，保留上次報價時間'}
                continue
            try:
                incoming = extended_quote(getter(f'https://api.nasdaq.com/api/quote/{symbol}/extended-trading?assetclass=stocks&markettype={mode}'), symbol, mode, now)
                prior = old.get(mode) or {}
                if prior.get('price') and prior.get('asOf') and (not incoming.get('asOf') or incoming['asOf'] < prior['asOf']):
                    output[mode] = {**prior, 'status': 'stale', 'reason': '來源本次沒有較新的此時段成交，顯示上次可用報價'}
                else:
                    output[mode] = incoming
            except (requests.RequestException, ValueError, TypeError):
                output[mode] = {**old.get(mode, {}), 'status': 'stale', 'reason': '公開來源連線失敗，保留上次報價時間'}
        write(path, output)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(one, symbol) for symbol in symbols]
        for i, future in enumerate(as_completed(futures), 1):
            future.result()
            if i % 40 == 0 or i == len(symbols):
                print(f'Market extras: {i}/{len(symbols)} symbols, {time.monotonic()-started:.1f}s', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=240)
    args = parser.parse_args()
    collect(limit=max(1, min(500, args.limit)))
