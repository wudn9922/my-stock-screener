"""Public archive-only instrument scope; never reads account/watchlist secrets or prices."""
from __future__ import annotations
import csv
import io
import json
from pathlib import Path
import re
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/kline-archive-instruments.json'
US_SYMBOL = re.compile(r'^[A-Z][A-Z0-9.-]{0,14}$')
TW_SYMBOL = re.compile(r'^[0-9]{4,6}[A-Z]?\.(TW|TWO)$')
INSTRUMENTS_STATE = 'state/instruments.json'
REPORT_FILES = ('latest.json', 'themes.json', 'themes-tw.json', 'trending-stocks.json')
PUBLIC_CATALOG_URL = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/generated/docs/atlas/symbols/taiwan.json'
NASDAQ_LISTS = ('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt')

def configuration(root=ROOT):
    return json.loads((Path(root) / 'config/kline-archive-instruments.json').read_text())

INDEX_METADATA = {r['symbol']: r for r in configuration()['indices']}

def bucket(symbol):
    if symbol in INDEX_METADATA:
        return INDEX_METADATA[symbol]['bucket']
    return 'tw' if symbol.endswith(('.TW', '.TWO')) else 'us'

def url_path(symbol):
    return f'{bucket(symbol)}/{quote(symbol, safe="")}'

def nasdaq_etfs(text):
    """Use the exchange ETF flag, never name heuristics or guesses about a ticker."""
    result = {}
    for row in csv.DictReader(io.StringIO(text), delimiter='|'):
        symbol = (row.get('Symbol') or row.get('ACT Symbol') or '').strip().upper()
        if row.get('ETF') == 'Y' and row.get('Test Issue') != 'Y' and US_SYMBOL.fullmatch(symbol):
            result[symbol] = (row.get('Security Name') or symbol)[:250]
    return result

def public_symbols(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ('symbol', 'ticker', 'etf', 'benchmark') and isinstance(child, str):
                yield child
            elif key in ('symbols', 'tickers') and isinstance(child, list):
                yield from (s for s in child if isinstance(s, str))
                yield from public_symbols(child)
            else:
                yield from public_symbols(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, list) and len(child) >= 2 and isinstance(child[0], str):
                yield child[0]  # the public symbol directory's compact tuples
            yield from public_symbols(child)

def read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default

def get_text(url):
    import requests
    response = requests.get(url, timeout=20, headers={'User-Agent': 'Atlas public archive scope/1.0'})
    response.raise_for_status()
    return response.text

def scope(root=ROOT, reports=None, previous=None, getter=get_text):
    root = Path(root)
    config = configuration(root)
    result = {}
    warnings = []
    for symbol, item in (previous or {}).items():
        if item.get('kind') in ('etf', 'index'):
            result[symbol] = dict(item)  # previously public ETFs remain monitored after leaving a list
    catalog = read(Path(reports or root / 'docs/report') / 'symbol-catalog.json', {}) or read(root / 'docs/atlas/symbols/taiwan.json', {})
    if not catalog:
        catalog = read(root / 'atlas/public/symbols/taiwan.json', {})
        warnings.append('Deployed SymbolCatalog unavailable; using the tracked catalog')
    for row in catalog.get('entries', []):
        if row.get('kind') == 'etf' and TW_SYMBOL.fullmatch(row.get('symbol', '')):
            result[row['symbol']] = {'kind': 'etf', 'name': row.get('name', row['symbol']), 'currency': 'TWD', 'timezone': 'Asia/Taipei', 'source': 'website SymbolCatalog kind=etf'}
    references = set(config['majorUsEtfs'])
    for file in [root / 'atlas/public/symbols/directory.json', root / 'atlas/scripts/market-symbols.json']:
        value = read(file, {})
        references.update(public_symbols(value))
        if isinstance(value, list): references.update(s for s in value if isinstance(s, str))
    for file in REPORT_FILES:
        path = Path(reports or root / 'docs/report') / file
        value = read(path, {})
        if not value: warnings.append('Public reference unavailable: ' + file)
        references.update(public_symbols(value))
    classified = {}
    for url in NASDAQ_LISTS:
        try:
            classified.update(nasdaq_etfs(getter(url)))
        except (OSError, ValueError):
            warnings.append('Nasdaq ETF flag list unavailable; keep configured and previously known ETFs')
    for symbol in sorted(references):
        if US_SYMBOL.fullmatch(symbol) and (symbol in classified or symbol in config['majorUsEtfs']):
            result[symbol] = {'kind': 'etf', 'name': classified.get(symbol, symbol), 'currency': 'USD', 'timezone': 'America/New_York', 'source': 'major ETF configuration' if symbol in config['majorUsEtfs'] else 'public website reference + Nasdaq ETF=Y'}
    for row in config['indices']:
        result[row['symbol']] = {**{k: v for k, v in row.items() if k not in ('symbol', 'bucket')}, 'kind': 'index', 'source': 'website index configuration'}
        if row['symbol'] == '^TWOII':
            result[row['symbol']]['source'] = 'TPEx official cash-index OHLC; read-only existing CSV; unavailable volume stays unknown'
    return dict(sorted(result.items())), warnings

def restore_public_reports(destination, getter=get_text):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    failures = []
    urls = {file: 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/generated/docs/report/' + file for file in REPORT_FILES}
    urls['symbol-catalog.json'] = PUBLIC_CATALOG_URL
    for file, url in urls.items():
        try:
            body = getter(url)
            json.loads(body)
            (destination / file).write_text(body)
        except (OSError, ValueError):
            failures.append(file)
    return failures
