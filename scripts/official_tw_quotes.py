"""Dated TWSE/TPEx observations only; no Yahoo requests or guessed bars."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode

NO_DATA = '很抱歉，沒有符合條件的資料!'
AUDIT_DAYS = ('2022-02-04', '2023-01-18', '2024-10-31', '2026-07-10')
REPAIR_DAYS = ('2021-04-06', '2025-08-01')


def roc_day(value):
    parts = str(value).split('/')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError('Invalid ROC date')
    return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2])).isoformat()


def urls(day):
    parsed = date.fromisoformat(day)
    return {
        'twse': 'https://www.twse.com.tw/exchangeReport/MI_INDEX?' + urlencode(
            dict(response='json', date=parsed.strftime('%Y%m%d'), type='ALLBUT0999')),
        'tpex': 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?' + urlencode(
            dict(date=f'{parsed.year-1911}/{parsed:%m/%d}', id='', response='json')),
        'twii': 'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?' + urlencode(
            dict(date=parsed.strftime('%Y%m%d'), response='json')),
    }



def url_candidates(exchange, day):
    primary = urls(day)[exchange]
    compact = day.replace('-', '')
    if exchange == 'twse':
        return [primary, f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={compact}&type=ALLBUT0999&response=json']
    if exchange == 'twii':
        return [primary, f'https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={compact}&response=json']
    return [primary]

def number(value):
    text = str(value).replace(',', '').strip()
    if text in ('', '--', '---', '-'):
        return None
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('Invalid official decimal') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Invalid official decimal')
    return result


def bar(day, values, volume):
    prices = [number(v) for v in values]
    supplied_volume = volume is not None
    volume = number(volume) if supplied_volume else None
    if supplied_volume and volume is None:
        return None
    if any(v is None or v <= 0 for v in prices) or volume == 0:
        return None  # One policy: skip every no-trade/missing-price row.
    o, h, l, c = prices
    if not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError('Invalid official OHLC bounds')
    if volume is not None and volume != volume.to_integral_value():
        raise ValueError('Official share volume must be integral')
    return (day, *prices, volume)


def daily_quotes(doc, exchange, day):
    """Select by field names; never rely on changing table/column offsets."""
    if exchange not in ('twse', 'tpex'):
        raise ValueError('Unknown official exchange')
    if exchange == 'twse' and doc.get('stat') == NO_DATA:
        if doc.get('date') not in (None, day.replace('-', '')):
            raise ValueError('Official no-data response date mismatch')
        return {}, {}, True
    if str(doc.get('stat', '')).lower() != 'ok' or doc.get('date') != day.replace('-', ''):
        raise ValueError('Official quote status/date mismatch')
    wanted = (['證券代號', '開盤價', '最高價', '最低價', '收盤價', '成交股數'] if exchange == 'twse'
              else ['代號', '開盤', '最高', '最低', '收盤', '成交股數'])
    tables = [t for t in doc.get('tables', []) if set(wanted) <= set(t.get('fields', []))]
    if not tables:
        raise ValueError('Official quote fields changed')
    result, skipped, seen = {}, {}, set()
    for table in tables:
        if exchange == 'tpex' and table.get('date') is not None and roc_day(table['date']) != day:
            raise ValueError('TPEx table ROC date mismatch')
        fields = table['fields']
        if len(set(fields)) != len(fields):
            raise ValueError('Duplicate official field')
        for row in table['data']:
            if len(row) != len(fields):
                raise ValueError('Official quote row width changed')
            values = [row[fields.index(key)] for key in wanted]
            code = str(values[0]).strip()
            if not re.fullmatch(r'[A-Z0-9]{1,20}', code):
                raise ValueError('Invalid official security identifier')
            symbol = code + ('.TW' if exchange == 'twse' else '.TWO')
            if symbol in seen:
                raise ValueError('Duplicate official quote')
            seen.add(symbol)
            parsed = bar(day, values[1:5], values[5]) if values[5] is not None else None
            if parsed is None:
                skipped[symbol] = 'official-no-trade-or-missing-ohlc'
            else:
                result[symbol] = parsed
    return result, skipped, not seen


def index_quotes(doc):
    fields = ['日期', '開盤指數', '最高指數', '最低指數', '收盤指數']
    if doc.get('stat') != 'OK' or doc.get('fields') != fields or not isinstance(doc.get('data'), list):
        raise ValueError('Official index fields/status changed')
    result = {}
    month = doc['date'][:6]
    for row in doc['data']:
        if len(row) != 5:
            raise ValueError('Official index row width changed')
        day = roc_day(row[0])
        if day.replace('-', '')[:6] != month or day in result:
            raise ValueError('Official index date mismatch/duplicate')
        parsed = bar(day, row[1:], None)  # This official endpoint does not publish volume.
        if parsed is None:
            raise ValueError('Missing official index OHLC')
        result[day] = parsed
    return result


def valid_closure_proof(row):
    """Evidence requires both dated official endpoints, explicit emptiness and hashes."""
    try:
        day = row['date']
        urls(day)  # Validate the ISO date before comparing exact dated URLs.
        evidence = row['officialEvidence']
        if 'official-no-quotes' not in row['reasons'] or len(evidence) != 2:
            return False
        if {e['exchange'] for e in evidence} != {'twse', 'tpex'}:
            return False
        for entry in evidence:
            exchange = entry['exchange']
            if (entry['url'] not in url_candidates(exchange, day) or type(entry['quoteRows']) is not int
                    or entry['quoteRows'] != 0 or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])):
                return False
            if exchange == 'twse' and (entry['stat'] != NO_DATA or entry.get('responseDate') not in (None, day.replace('-', ''))):
                return False
            if exchange == 'tpex' and (entry['stat'] != 'ok' or entry['responseDate'] != day.replace('-', '')):
                return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


class OfficialClient:
    def __init__(self, cache, offline=False):
        self.cache, self.offline = Path(cache), offline
        self.records, self.parsed = {}, {}
        self.last_request = 0.0

    def get(self, exchange, day):
        key = f'{exchange}-{day}'
        if key in self.parsed:
            return self.parsed[key]
        path = self.cache / (key + '.json')
        metadata = path.with_suffix('.source.json')
        candidates = url_candidates(exchange, day)
        url, attempts = candidates[0], []
        if path.exists():
            raw = path.read_bytes()
            if metadata.exists():
                saved = json.loads(metadata.read_text())
                if saved['url'] not in candidates or saved['sha256'] != hashlib.sha256(raw).hexdigest():
                    raise ValueError('Cached official response provenance mismatch')
                url, attempts = saved['url'], saved.get('httpAttempts', [])
        else:
            if self.offline:
                raise ValueError(f'Official response not cached: {key}')
            import requests
            raw = None
            for candidate in candidates:
                delay = 1 - (time.monotonic() - self.last_request)
                if delay > 0:
                    time.sleep(delay)
                with requests.get(candidate, timeout=30, stream=True, allow_redirects=False,
                                  headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    self.last_request = time.monotonic()
                    attempts.append(dict(url=candidate, httpStatus=response.status_code))
                    if response.status_code != 200:
                        if response.status_code in (301, 302, 303, 307, 308, 403):
                            continue  # Only try the fixed official equivalent, never Location.
                        raise ValueError(f'Official HTTP {response.status_code}: {key}')
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 16 * 1024 * 1024:
                            raise ValueError('Official response too large')
                        chunks.append(chunk)
                    candidate_raw = b''.join(chunks)
                    try:
                        json.loads(candidate_raw)
                    except (ValueError, UnicodeDecodeError):
                        continue  # A challenge/HTML page is never a no-data response.
                    raw, url = candidate_raw, candidate
                    break
            if raw is None:
                raise ValueError(f'No valid official JSON response: {key}')
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Official response too large')
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            raise ValueError('Invalid official response')
        if exchange == 'twii' and doc.get('date') != day.replace('-', ''):
            raise ValueError('Official index response date mismatch')
        parsed = index_quotes(doc) if exchange == 'twii' else daily_quotes(doc, exchange, day)
        if not path.exists():
            self.cache.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.tmp')
            temp.write_bytes(raw)
            temp.replace(path)
            metadata.write_text(json.dumps(dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), httpAttempts=attempts)))
        self.records[key] = dict(exchange=exchange, url=url, httpAttempts=attempts, bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), stat=doc.get('stat'), responseDate=doc.get('date'))
        self.parsed[key] = parsed
        return parsed

    def observations(self, day):
        result, skipped = {}, {}
        for exchange in ('twse', 'tpex'):
            quotes, missing, _ = self.get(exchange, day)
            result.update(quotes)
            skipped.update(missing)
        indices = self.get('twii', day[:8] + '01')
        if day in indices:
            result['^TWII'] = indices[day]
        return result, skipped

    def neighbors(self, day):
        current = date.fromisoformat(day)
        sessions = dict(self.get('twii', current.replace(day=1).isoformat()))
        ordinal = current.year * 12 + current.month - 1
        for offset in (-1, 1):
            candidates = [d for d in sessions if d < day] if offset == -1 else [d for d in sessions if d > day]
            if len(candidates) < 2:
                year, month = divmod(ordinal + offset, 12)
                sessions.update(self.get('twii', date(year, month + 1, 1).isoformat()))
        before = sorted(d for d in sessions if d < day)[-2:]
        after = sorted(d for d in sessions if d > day)[:2]
        if day not in sessions or len(before) != 2 or len(after) != 2:
            raise ValueError('Insufficient official trading-session calendar')
        return before + after

    def audit_days(self):
        closed, opened = [], []
        for day in AUDIT_DAYS:
            evidence, empty = [], []
            for exchange in ('twse', 'tpex'):
                quotes, skipped, no_rows = self.get(exchange, day)
                evidence.append({**self.records[f'{exchange}-{day}'], 'quoteRows': len(quotes) + len(skipped)})
                empty.append(no_rows)
            if all(empty):
                proof = dict(date=day, reasons=['official-no-quotes'], officialEvidence=evidence)
                if not valid_closure_proof(proof):
                    raise ValueError('Incomplete official closure evidence')
                closed.append(proof)
            elif any(empty):
                raise ValueError('Official exchanges disagree on opening')
            else:
                opened.append(day)
        return dict(checkedAt=datetime.now(timezone.utc).isoformat(), closed=closed, opened=opened)
