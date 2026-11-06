"""Bounded Yahoo index downloads with a read-only, SHA-256 checked archive fallback.

Both sources use split-adjusted OHLC, never dividend-adjusted prices. Invalid
Yahoo sessions are kept for the caller's existing calculation/denominator rules.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sys
import time

import exchange_calendars as xcals
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kline_archive as archive
from kline_instruments import bucket, url_path
from build_universe_full import RATE_LIMIT_WAITS, RateLimited

DEFAULT_ARCHIVE = Path(__file__).resolve().parents[1] / '.cache/kline-archive'
PRICES = ['Open', 'High', 'Low', 'Close']
RETRY_WAITS = (5, 10)


class IndexDailyError(ValueError):
    pass


class IndexDailyBars(dict):
    def __init__(self):
        super().__init__()
        self.sources = {}
        self.errors = {}


@lru_cache(maxsize=16)
def _calendar(market, start_year, end_year):
    return xcals.get_calendar('XTAI' if market == 'tw' else 'XNYS',
                              start=f'{start_year}-01-01', end=f'{end_year}-12-31')


def completed_bars(frame, symbol, now):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=PRICES + ['Volume'], index=pd.DatetimeIndex([]))
    frame = frame.copy()
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None).normalize()
    frame = frame[~frame.index.duplicated(keep='last')].sort_index()
    frame = frame.reindex(columns=PRICES + ['Volume']).apply(pd.to_numeric, errors='coerce')
    cal = _calendar(bucket(symbol), frame.index[0].year - 1, now.year + 1)
    start = max(frame.index[0], cal.first_session.tz_localize(None))
    end = min(pd.Timestamp(now.astimezone(cal.tz).date()), cal.last_session.tz_localize(None))
    if start > end:
        return frame.iloc[:0]
    sessions = cal.sessions_in_range(start, end)
    closed = [day.tz_localize(None) for day in sessions if cal.session_close(day).to_pydatetime() <= now]
    return frame.loc[frame.index.isin(closed)]


def _valid(frame):
    values = frame[PRICES]
    good = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
    good &= (frame.High >= values.max(axis=1)) & (frame.Low <= values.min(axis=1))
    return good


def valid_count(frame):
    return int(_valid(frame).sum())


def _require_complete(frame, min_bars, as_of):
    good = _valid(frame)
    count = int(good.sum())
    if count < min_bars:
        raise ValueError(f'已收盤有效日 K 僅 {count} 根，少於 {min_bars} 根')
    if as_of is not None and frame.index[good][-1] != pd.Timestamp(as_of):
        raise ValueError(f'最新有效日 K 與資料日 {as_of} 不同')
    return count


def _extract(raw, symbol, single=False):
    if not isinstance(raw, pd.DataFrame):
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        for level in range(raw.columns.nlevels):
            if symbol in raw.columns.get_level_values(level):
                return raw.xs(symbol, axis=1, level=level).copy()
        return pd.DataFrame()
    return raw.copy() if single else pd.DataFrame()


def _rate_limited(exc):
    return (isinstance(exc, RateLimited) or type(exc).__name__ in ('RateLimited', 'YFRateLimitError')
            or getattr(exc, 'status', None) == 429
            or getattr(getattr(exc, 'response', None), 'status_code', None) == 429)


def _read_archive(root, symbol, period, now):
    root = Path(root)
    archive.market_of(symbol)  # same canonical symbol/path safety as the writer
    relative = url_path(symbol)
    doc = json.loads((root / f'index/{relative}.json').read_text())
    if (not isinstance(doc, dict) or doc.get('version') != 1 or doc.get('symbol') != symbol
            or doc.get('market') != bucket(symbol) or doc.get('path') != relative):
        raise ValueError('封存 index 代號／路徑不符')
    if not isinstance(doc.get('years'), dict) or not doc['years']:
        raise ValueError('封存 index 年度明細不符')
    start = pd.Timestamp(now.astimezone(_calendar(bucket(symbol), now.year - 1, now.year + 1).tz).date()) - pd.DateOffset(years=int(period[:-1]))
    rows = []
    for year, stat in sorted(doc['years'].items()):
        if not (str(year).isdigit() and len(str(year)) == 4 and isinstance(stat, dict)):
            raise ValueError('封存年份不符')
        if not start.year <= int(year) <= now.year:
            continue
        # CSV directories are canonical symbols; index paths are URL encoded.
        path = root / bucket(symbol) / symbol / f'{year}.csv'
        body = path.read_bytes()
        if archive.digest(body) != stat['sha256'] or len(body) != stat['csvBytes']:
            raise ValueError(f'{year} 封存 SHA-256／大小不符')
        part = archive.read_rows(path, strict=True)
        if (len(part) != stat['rows'] or part[0][0] != stat['firstDate']
                or part[-1][0] != stat['lastDate'] or any(not r[0].startswith(year + '-') for r in part)):
            raise ValueError(f'{year} 封存日期／根數不符')
        rows.extend(part)
    frame = pd.DataFrame(rows, columns=['Date', *PRICES, 'Volume']).set_index('Date')
    frame.index = pd.to_datetime(frame.index)
    return frame.loc[frame.index >= start]


def read_index_daily(symbols, *, period, min_bars, now=None, archive_dir=DEFAULT_ARCHIVE,
                     as_of=None, download=None, sleep=None, log=print):
    """One batch, at most two retries per bad symbol, then that symbol's archive.

    Explicit 429/RateLimited errors use the existing 30/60 second cooldowns.
    Persistent limiting stops further Yahoo calls, like the universe collector.
    Per-symbol errors are returned so callers can isolate markets or fail closed.
    """
    if download is None:
        import yfinance as yf
        download = yf.download
    now = now or datetime.now(timezone.utc)
    sleep = sleep or time.sleep
    symbols = list(dict.fromkeys(symbols))
    result = IndexDailyBars()
    kwargs = dict(period=period, auto_adjust=False, progress=False, group_by='ticker', threads=False, timeout=15)
    batch_error = None
    try:
        raw = download(symbols, **kwargs)
    except Exception as exc:
        raw, batch_error = pd.DataFrame(), exc
    stop_yahoo = False
    for symbol in symbols:
        error, attempts = batch_error, 1
        frame = _extract(raw, symbol, len(symbols) == 1)
        reason = ''
        for retry in range(3):
            try:
                frame = completed_bars(frame, symbol, now)
                if as_of is not None:
                    frame = frame.loc[:pd.Timestamp(as_of)]
                count = _require_complete(frame, min_bars, as_of)
                error = None
                break
            except (ValueError, TypeError, KeyError) as exc:
                error = error or exc
                reason = str(error)
            if retry == 2 or stop_yahoo:
                break
            wait = RATE_LIMIT_WAITS[retry] if _rate_limited(error) else RETRY_WAITS[retry]
            log(f'Index daily {symbol}: Yahoo 重試 {retry + 1}/2，等待 {wait} 秒（{reason}）')
            sleep(wait)
            attempts += 1
            try:
                frame, error = _extract(download([symbol], **kwargs), symbol, True), None
            except Exception as exc:
                frame, error = pd.DataFrame(), exc
        if error is not None:
            stop_yahoo |= _rate_limited(error)
            try:
                frame = completed_bars(_read_archive(archive_dir, symbol, period, now), symbol, now)
                if as_of is not None:
                    frame = frame.loc[:pd.Timestamp(as_of)]
                count = _require_complete(frame, min_bars, as_of)
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                result.errors[symbol] = f'{symbol} Yahoo：{reason}；封存：{exc}'
                log('Index daily 未取得：' + result.errors[symbol])
                continue
            source = 'kline-archive'
            log(f'Index daily {symbol}: 使用 kline-archive 備援，{count} 根，截至 {frame.index[-1].date()}（Yahoo：{reason}）')
        else:
            source = 'yahoo'
        result[symbol] = frame
        result.sources[symbol] = dict(source=source, lastDate=str(frame.index[_valid(frame)][-1].date()), validBars=count,
                                      attempts=attempts, priceBasis='split-adjusted', dividendAdjusted=False,
                                      fallbackReason=reason if source == 'kline-archive' else None)
    return result
