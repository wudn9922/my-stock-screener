"""Read-only analysis views of a complete TW/US market's daily histories.

Sources sometimes append a flat zero-volume row for every stock on a closure.
Infer those dates from the whole cohort, never by rewinding one halted stock.
Raw caches and permanent archives are not modified.
"""
from __future__ import annotations

import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from official_tw_quotes import valid_closure_proof

UNKNOWN_VOLUME_DATES = "atlas_unknown_volume_dates"
CLOSED_DATES = "atlas_market_closed_dates"
CLOSURE_EVIDENCE = "atlas_emergency_closure_evidence"
PROJECTED = "atlas_market_sessions_projected"
REMOVED_WEEKENDS = "atlas_removed_weekend_dates"
CLOSURE_SHARE = 0.95
TRADING_SHARE = 0.05
TRADING_SYMBOLS = 50
PRIMARY_INDICES = {"tw": "^TWII", "us": "^GSPC"}
POLICY_FILE = "market-closures.json"
CALENDARS = {'tw': 'XTAI', 'us': 'XNYS'}


@lru_cache(maxsize=32)
def exchange_sessions(market, start, end):
    """Official exchange calendar labels, with a small margin for library bounds."""
    import exchange_calendars as xcals
    calendar = xcals.get_calendar(CALENDARS[market], start=pd.Timestamp(start)-pd.Timedelta(days=7),
                                 end=pd.Timestamp(end)+pd.Timedelta(days=7))
    return frozenset(calendar.sessions.strftime('%Y-%m-%d'))


def _emergency_proof(row, min_symbols=2):
    if not isinstance(row, dict):
        return False
    count, eligible = row.get('observedSymbols', 0), row.get('eligibleSymbols', 0)
    return (count >= min_symbols and eligible >= count and row.get('flatZeroSymbols', 0) >= eligible * CLOSURE_SHARE
            and 'primary-index-missing' in row.get('reasons', ())
            and 'flat-zero-cohort' in row.get('reasons', ())
            and row.get('positiveVolumeSymbols', 0) < TRADING_SYMBOLS
            and row.get('positiveVolumeSymbols', 0) < count * TRADING_SHARE)



def verified_closures(market):
    """Reviewed official empty quotes and the retained #230 emergency proof."""
    try:
        path = Path(__file__).resolve().parents[1] / 'atlas/src/market-data/verified-market-closures.json'
        seed = json.loads(path.read_text())
        if seed.get('version') != 1 or seed.get('threshold') != CLOSURE_SHARE:
            return {}
        return {row['date']: row for row in seed['markets'].get(market, {}).get('closedSessions', [])
                if market == 'tw' and valid_closure_proof(row) or _emergency_proof(row)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}

def cohort_statistics(frames):
    """Count valid OHLC observations, not ETFs/indices added to a stock universe.

    Close-only legacy inputs remain readable, but cannot prove O=H=L=C. Unknown
    volume is retained in the denominator and never counted as known zero.
    """
    frames = {s: f for s, f in frames.items() if f is not None and not f.empty}
    if not frames:
        return pd.DataFrame(columns=['observed', 'flatZero', 'unknownVolume', 'positiveVolume'])
    indexed = [(f, pd.DatetimeIndex(f.index).tz_localize(None).normalize()) for f in frames.values()]
    days = pd.DatetimeIndex(np.unique(np.concatenate([d.to_numpy(dtype='datetime64[ns]') for _, d in indexed])))
    counts = np.zeros((len(days), 4), dtype=np.int64)
    for frame, dates in indexed:
        unique = ~dates.duplicated(keep='last')
        frame, dates = frame.loc[unique], dates[unique]
        close = pd.to_numeric(frame["Close"], errors="coerce")
        volume = pd.to_numeric(frame["Volume"], errors="coerce")
        missing = ~np.isfinite(volume) | (volume < 0)
        lineage = {str(day)[:10] for day in frame.attrs.get(UNKNOWN_VOLUME_DATES, ())}
        if lineage:
            missing |= dates.strftime("%Y-%m-%d").isin(lineage)
        observed = np.isfinite(close) & (close > 0)
        flat = pd.Series(False, index=frame.index)
        if {'Open', 'High', 'Low', 'Close'}.issubset(frame.columns):
            price = frame[['Open', 'High', 'Low', 'Close']].apply(pd.to_numeric, errors='coerce')
            observed &= np.isfinite(price).all(axis=1) & (price > 0).all(axis=1)
            observed &= (price.High >= price.max(axis=1)) & (price.Low <= price.min(axis=1))
            flat = price.eq(price.Close, axis=0).all(axis=1)
        values = np.column_stack([observed, observed & flat & (volume == 0) & ~missing, observed & missing, observed & ~missing & (volume > 0)])
        counts[days.get_indexer(dates)] += values.astype(np.int64)
    return pd.DataFrame(counts, index=days, columns=['observed', 'flatZero', 'unknownVolume', 'positiveVolume'])


def classify_closures(stats, primary=None, known=(), min_symbols=2, *, trading=(), reviews=None,
                      market=None, official=None, missing=None):
    """One rule for scans, collection and calculations; evidence is JSON-safe.

    A calendar session cannot be closed just because prices are absent. Only a
    missing index AND the existing >=95% flat-zero cohort can override a session
    (emergency closure). Calendar gaps are observable missing data, never holidays.
    """
    if primary:
        candidates = pd.bdate_range(primary['firstDate'], primary['lastDate'])
        stats = stats.reindex(stats.index.union(candidates)).sort_index().fillna(0)
    market = (market or ('tw' if (primary or {}).get('symbol') == '^TWII' else 'us' if primary else '')).lower()
    bounds = [d.strftime('%Y-%m-%d') for d in stats.index] + list(known)
    seed = verified_closures(market)
    if bounds:
        seed = {day: row for day, row in seed.items() if min(bounds) <= day <= max(bounds)
                and (valid_closure_proof(row) or pd.Timestamp(day) not in stats.index
                     or stats.loc[pd.Timestamp(day)].get('unknownVolume', 0) == 0)}
    else:
        seed = {}
    known = {**(dict(known) if isinstance(known, dict) else {d: {} for d in known}), **seed}
    extra = set(known) | set(official or {})
    if extra:
        stats = stats.reindex(stats.index.union(pd.to_datetime(sorted(extra)))).sort_index().fillna(0)
    market = (market or ('tw' if (primary or {}).get('symbol') == '^TWII' else 'us' if primary else '')).lower()
    calendar = None
    if market in CALENDARS and len(stats):
        try:
            calendar = exchange_sessions(market, stats.index.min().strftime('%Y-%m-%d'), stats.index.max().strftime('%Y-%m-%d'))
        except Exception:
            # Calendar uncertainty must never turn an index hole into a closure.
            calendar = None
    records = {}
    traded = set(trading)
    for day, row in stats.iterrows():
        count, positive = int(row.observed), int(row.get('positiveVolume', 0))
        if positive >= TRADING_SYMBOLS or count and positive / count >= TRADING_SHARE:
            traded.add(pd.Timestamp(day).strftime('%Y-%m-%d'))
    reference = stats.observed.rolling(20, min_periods=1).quantile(.25) if len(stats) else pd.Series(dtype=float)
    for day, row in stats.iterrows():
        date = pd.Timestamp(day).strftime('%Y-%m-%d')
        count, flat = int(row.observed), int(row.flatZero)
        eligible = max(count, int(row.get('eligible', count)))
        positive = int(row.get('positiveVolume', 0))
        index_missing = bool(primary and primary['firstDate'] <= date <= primary['lastDate'] and date not in primary['dates'])
        index_absent = not primary or date not in primary['dates']
        is_session = date in calendar if calendar is not None else None
        flat_cohort = count >= min_symbols and count >= reference.loc[day] * .5 and flat >= eligible * CLOSURE_SHARE
        old_proof = _emergency_proof(known.get(date), min_symbols) if isinstance(known, dict) else False
        official_closed = market == 'tw' and valid_closure_proof(known.get(date, {})) and not (official or {}).get(date)
        emergency = index_absent and (flat_cohort or old_proof) and not (official or {}).get(date)
        if date in traded:
            if index_missing and reviews is not None and count and (positive >= TRADING_SYMBOLS or positive / count >= TRADING_SHARE):
                reviews[date] = dict(date=date, reason='primary-index-gap-with-trading',
                                    observedSymbols=count, positiveVolumeSymbols=positive,
                                    positiveVolumeShare=positive/count if count else None)
            if is_session is True and index_missing and missing is not None:
                missing[date] = dict(date=date, reason='missing-session-data', calendar=CALENDARS[market],
                                     calendarSession=True, primaryIndexMissing=True,
                                     observedSymbols=count, officialSources=list((official or {}).get(date, ())))
            continue
        if is_session is True and not emergency and not official_closed:
            if missing is not None and (index_absent or date in known):
                missing[date] = dict(date=date, reason='missing-session-data', calendar=CALENDARS[market],
                                     calendarSession=True, primaryIndexMissing=index_absent,
                                     observedSymbols=count, officialSources=list((official or {}).get(date, ())))
            continue
        if official_closed:
            records[date] = dict(known[date])
            continue
        reasons = []
        if pd.Timestamp(day).dayofweek >= 5:
            reasons.append('weekend')
        if is_session is False:
            reasons.append('exchange-calendar-closed')
        if index_missing or emergency:
            reasons.append('primary-index-missing')
        if flat_cohort:
            reasons.append('flat-zero-cohort')
        if date in known and (is_session is False or emergency or market not in CALENDARS):
            reasons.append('published-closure')
        if market in CALENDARS and is_session is None:
            reasons = [reason for reason in reasons if reason == 'weekend']
        if reasons:
            records[date] = dict(date=date, reasons=reasons, observedSymbols=count,
                                 flatZeroSymbols=flat, eligibleSymbols=eligible, unknownVolumeSymbols=int(row.unknownVolume),
                                 positiveVolumeSymbols=positive, positiveVolumeShare=positive/count if count else None,
                                 flatZeroShare=flat/eligible if eligible else None,
                                 coverageReference=float(reference.loc[day]), calendar=CALENDARS.get(market),
                                 calendarSession=is_session)
            if emergency and old_proof and not count:
                records[date] = {**known[date], 'reasons':list(dict.fromkeys([*known[date]['reasons'],'published-closure'])),
                                 'calendar': CALENDARS[market], 'calendarSession': is_session}
    return records


def primary_reference(frame, symbol, source, now=None):
    if frame is None or frame.empty or not {'Open','High','Low','Close'}.issubset(frame.columns):
        return None
    values = frame[['Open','High','Low','Close']].apply(pd.to_numeric, errors='coerce')
    good = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
    good &= (values.High >= values.max(axis=1)) & (values.Low <= values.min(axis=1))
    days = pd.DatetimeIndex(frame.index[good]).tz_localize(None).normalize()
    if now is not None:
        import universe_cache as uc
        market = 'TW' if symbol in ('^TWII', '^TWOII') else 'US'
        days = days[[uc.session_done(d.strftime('%Y-%m-%d'), market, now) for d in days]]
    if days.empty:
        return None
    dates = frozenset(days.strftime('%Y-%m-%d'))
    return dict(symbol=symbol, source=source, firstDate=min(dates), lastDate=max(dates), dates=dates)


def archive_primary(root, market, now=None):
    """Read and hash-check the tiny primary index and all its referenced years."""
    return archive_reference(root, market, PRIMARY_INDICES[market.lower()], now)


def archive_reference(root, market, symbol, now=None):
    """Hash-verified archive index reference; no network and no archive mutations."""
    try:
        root = Path(root)
        doc = json.loads((root / 'index' / market.lower() / (symbol.replace('^','%5E') + '.json')).read_text())
        if doc['symbol'] != symbol or doc['version'] != 1 or doc['market'] != market.lower():
            return None
        parts = []
        for year, detail in sorted(doc['years'].items()):
            if len(year) != 4 or not year.isdigit():
                return None
            body = (root / market.lower() / symbol / (year + '.csv')).read_bytes()
            if hashlib.sha256(body).hexdigest() != detail['sha256'] or len(body) != detail['csvBytes']:
                return None
            part = pd.read_csv(io.BytesIO(body), parse_dates=['Date']).set_index('Date')
            if (len(part) != detail['rows'] or part.index[0].strftime('%Y-%m-%d') != detail['firstDate']
                    or part.index[-1].strftime('%Y-%m-%d') != detail['lastDate']):
                return None
            parts.append(part)
        return primary_reference(pd.concat(parts), symbol, 'kline-archive', now) if parts else None
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def archive_official_days(root, market, now=None):
    """TPEx provenance is explicit in this index; Yahoo indices are not official evidence."""
    if market.lower() != 'tw':
        return {}
    try:
        source = json.loads((Path(root)/'index/tw/%5ETWOII.json').read_text()).get('source', '')
        if not source.startswith('TPEx official cash-index OHLC'):
            return {}
        reference = archive_reference(root, 'tw', '^TWOII', now)
        return {day: [dict(symbol='^TWOII', source=source)] for day in reference['dates']} if reference else {}
    except (OSError, ValueError, AttributeError):
        return {}


def read_policy(path):
    try:
        value = json.loads(Path(path).read_text())
        if value.get('version') != 1 or value.get('threshold') != CLOSURE_SHARE:
            return {}
        result = {}
        for market in ('tw', 'us'):
            part = value['markets'].get(market, {})
            traded = {g['date'] for g in part.get('indexGapsWithTrading', [])}
            veto = {g['date'] for g in part.get('missingSessions', [])}
            rows = part.get('closedSessions', [])
            days = exchange_sessions(market, min(r['date'] for r in rows), max(r['date'] for r in rows)) if rows else ()
            result[market] = {r['date']: r for r in rows if r['date'] not in traded and (r['date'] not in veto or market == 'tw' and valid_closure_proof(r))
                              and (r['date'] not in days or _emergency_proof(r) or market == 'tw' and valid_closure_proof(r))}
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError, RuntimeError):
        return {}


def read_trading_policy(path):
    try:
        value = json.loads(Path(path).read_text())
        return {m:{r['date']:r for r in value.get('markets',{}).get(m,{}).get('indexGapsWithTrading',[])} for m in ('tw','us')}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def snapshot_statistics(bars, listed):
    frame = pd.DataFrame.from_dict({s:b for s,b in bars.items() if s in listed},orient='index')
    if frame.empty:
        return pd.DataFrame(columns=['observed','flatZero','unknownVolume','positiveVolume','eligible'])
    price = frame.reindex(columns=['open','high','low','close']).apply(pd.to_numeric,errors='coerce')
    volume = pd.to_numeric(frame.get('volume',pd.Series(index=frame.index,dtype=float)),errors='coerce')
    valid = np.isfinite(price).all(axis=1) & (price > 0).all(axis=1)
    valid &= (price.high >= price.max(axis=1)) & (price.low <= price.min(axis=1))
    unknown = ~np.isfinite(volume) | (volume < 0)
    flat = price.eq(price.close,axis=0).all(axis=1)
    result = pd.DataFrame({'observed':valid.astype(int),'flatZero':(valid & flat & (volume == 0) & ~unknown).astype(int),
                           'unknownVolume':(valid & unknown).astype(int), 'positiveVolume':(valid & ~unknown & (volume > 0)).astype(int)})
    result.index = pd.to_datetime(frame['date'],errors='coerce').to_numpy()
    result = result.loc[result.index.notna()].groupby(level=0).sum().sort_index()
    result['eligible'] = len(listed)
    return result


def non_trading_dates(frames, primary=None, known=(), *, market=None, official=None, evidence=None):
    inherited = set(known)
    proofs = dict(known) if isinstance(known, dict) else {}
    for frame in frames.values():
        if frame is not None:
            inherited.update(frame.attrs.get(CLOSED_DATES, ()))
            proofs.update(frame.attrs.get(CLOSURE_EVIDENCE, {}))
    if market is None:
        if (primary or {}).get('symbol') in PRIMARY_INDICES.values():
            market = next(m for m,symbol in PRIMARY_INDICES.items() if symbol == primary['symbol'])
        elif frames:
            market = 'tw' if any(s.endswith(('.TW', '.TWO')) or s in ('^TWII', '^TWOII') for s in frames) else 'us'
    # Preserve the published evidence, not just its date, for emergency closures.
    records = classify_closures(cohort_statistics(frames), primary,
        {day:proofs.get(day, {}) for day in inherited | set(proofs)}, market=market,official=official)
    if evidence is not None:
        evidence.update(records)
    return frozenset(records)


def analysis_prices(frame, excluded_dates):
    """Remove only cohort-confirmed non-trading dates, preserving unknown-volume lineage."""
    if frame is None or frame.empty or not excluded_dates:
        return frame
    days = pd.DatetimeIndex(frame.index).tz_localize(None).strftime("%Y-%m-%d")
    result = frame.loc[~days.isin(excluded_dates)].copy()
    result.attrs.update(frame.attrs)
    return result


def market_prices(frames, primary=None, known=(), *, market=None, official=None):
    """Return projected market histories and the dates needed for custom-stock views."""
    if frames and primary is None and not known and all(f is not None and f.attrs.get(PROJECTED) == 'official-closures-v1' for f in frames.values()):
        return frames, frozenset(d for f in frames.values() for d in f.attrs.get(CLOSED_DATES, ()))
    evidence = {}
    excluded = non_trading_dates(frames, primary, known, market=market,official=official,evidence=evidence)
    projected = {}
    for symbol, frame in frames.items():
        view = analysis_prices(frame, excluded)
        if view is None or view.empty:
            continue
        if view is frame:
            view = frame.copy(deep=False)
        # Mark the cohort, so a later sector/monthly view cannot infer a whole
        # market closure from its own few halted members. Never mutate raw attrs.
        view.attrs[CLOSED_DATES] = tuple(sorted(excluded))
        # Ordinary calendar holidays can be rechecked. Keep only the exceptional
        # emergency proof, so downstream projections do not need the removed rows.
        view.attrs[CLOSURE_EVIDENCE] = {day:row for day,row in evidence.items() if row.get('calendarSession') is True}
        view.attrs[PROJECTED] = 'official-closures-v1'
        view.attrs[REMOVED_WEEKENDS] = tuple(sorted(set(frame.attrs.get(REMOVED_WEEKENDS,())) |
            set(frame.index[frame.index.dayofweek >= 5].strftime('%Y-%m-%d'))))
        projected[symbol] = view
    return {s: f for s, f in projected.items() if f is not None and not f.empty}, excluded
