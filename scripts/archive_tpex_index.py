"""Read-only TPEx cash-index source used by the website (^TWOII has no Yahoo history).

Reads the existing permanent CSV without editing it; missing months come from the same official
monthly endpoint as main.py. Requires actual O/H/L/C, and never invents missing trading volume.
"""
from datetime import date, timedelta
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo
import pandas as pd
import kline_integrity as integrity
import universe_cache as uc

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.tpex.org.tw/web/stock/iNdex_info/inxh/Inx_result.php'

def parse_month(payload):
    tables = payload.get('tables', [])
    if not tables and payload.get('aaData'):
        tables = [{'fields': ['日期', '開市', '最高', '最低', '收市'], 'data': payload['aaData']}]
    for table in tables:
        if '報酬' in str(table.get('title', '')): continue
        fields = table.get('fields') or []
        aliases = [('日期', 'Date'), ('開市', '開盤', 'Open'), ('最高', 'High'), ('最低', 'Low'), ('收市', '收盤', 'Close')]
        positions = []
        for names in aliases:
            positions.append(next((i for i, field in enumerate(fields) if str(field).strip() in names), None))
        if any(i is None for i in positions): continue
        rows = []
        for raw in table.get('data') or []:
            try:
                text = str(raw[positions[0]]).strip()
                year, month, day = map(int, re.split(r'[/\-]', text))
                day = date(year + 1911 if year < 1911 else year, month, day)
                values = [float(str(raw[i]).replace(',', '').strip()) for i in positions[1:]]
                record = dict(zip(uc.PRICE_COLUMNS[:4], values))
                if integrity.violation(record): raise ValueError('invalid OHLC')
                rows.append((day.isoformat(), *values, None))
            except (ValueError, TypeError, IndexError):
                raise ValueError('TPEx source contains an invalid dated OHLC row') from None
        if rows: return uc.frame_from_rows(rows)
    raise ValueError('no data from source: TPEx did not provide actual daily OHLC')

def history(start, now, net, *, source_csv=None, time_left=lambda: math.inf, sleep=time.sleep):
    """Every missing month must succeed before a backfill is marked done. No source file is written."""
    start = date.fromisoformat(str(start)[:10])
    local = uc.read_bars(str(source_csv or ROOT / 'data/^TWOII.csv'))
    if not local.empty:
        # The legacy index importer used 0 when optional volume retrieval failed.
        # This cash index retains positive known shares; unknown stays blank.
        raw_local = uc._volume_source_frame(local)
        raw_local.loc[raw_local['Volume'] <= 0, 'Volume'] = math.nan
        local = uc.clean(raw_local)
    first_month = local.index[0].strftime('%Y-%m') if not local.empty else ''
    last_month = local.index[-1].strftime('%Y-%m') if not local.empty else ''
    taipei = now.astimezone(ZoneInfo('Asia/Taipei'))
    completed = taipei.date()
    if taipei.time().replace(tzinfo=None) < uc.SESSION_DONE['TW']: completed -= timedelta(days=1)
    while completed.weekday() >= 5: completed -= timedelta(days=1)
    current_fresh = not local.empty and local.index[-1].date() >= completed
    merged = local
    month = date(start.year, start.month, 1)
    while month <= date(taipei.year, taipei.month, 1):
        key = month.strftime('%Y-%m')
        covered = bool(first_month and first_month <= key <= last_month)
        if covered and (key != taipei.strftime('%Y-%m') or current_fresh):
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
            continue
        if time_left() < 30: raise ValueError('TPEx history incomplete: request budget exhausted; retry later')
        print(f'Archive TPEx: read complete OHLC month {key}', flush=True)
        raw = net.get_json(URL, params={'l': 'zh-tw', 'd': f'{month.year-1911}/{month.month:02d}', 'o': 'json'})
        fresh = parse_month(raw)
        old_raw, fresh_raw = uc._volume_source_frame(merged), uc._volume_source_frame(fresh)
        # The index table has no shares. Preserve actual known shares from the existing CSV.
        overlap = fresh_raw.index.intersection(old_raw.index)
        if len(overlap): fresh_raw.loc[overlap, 'Volume'] = old_raw.loc[overlap, 'Volume']
        merged = uc.clean(pd.concat([old_raw, fresh_raw]))
        sleep(1.2)  # same official-source pacing as main.py
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    if merged.empty: raise ValueError('no data from source: TPEx returned no history')
    merged = merged[merged.index.date >= start]
    merged = uc.drop_unfinished(merged, 'TW', now)
    return integrity.usable_frame(merged, '^TWOII')
