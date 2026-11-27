"""Optional SIP daily-bar fallback. No credentials means no HTTP."""
from datetime import date, datetime, time as day_time, timedelta, timezone
import os
import re
import threading
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

URL = "https://data.alpaca.markets/v2/stocks/bars"
NY = ZoneInfo("America/New_York")
MIN_DAY = date(2016, 1, 1)
_cache = {}
_coverage = {}
_lock = threading.RLock()
_last_request = None


def enabled():
    return bool(os.environ.get("ALPACA_API_KEY_ID", "").strip()
                and os.environ.get("ALPACA_API_SECRET_KEY", "").strip())


def alpaca_symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,5}(?:-[A-Z])?", symbol):
        return None
    return symbol.replace("-", ".")


def trading_day(timestamp):
    # Alpaca timestamps are RFC3339 UTC, not the date in the viewer's timezone.
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(NY).date()


def _request(params):
    global _last_request
    if not enabled():
        return None
    import requests
    headers = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY_ID"],
               "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET_KEY"]}
    for attempt in range(4):
        if _last_request is not None:
            time.sleep(max(0, 0.35 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        try:
            response = requests.get(URL, params=params, headers=headers, timeout=30)
            if response.status_code == 429:
                if attempt == 3:
                    return None
                try:
                    delay = float(response.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    from email.utils import parsedate_to_datetime
                    try:
                        delay = (parsedate_to_datetime(response.headers["Retry-After"])
                                 - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, KeyError):
                        delay = 1
                time.sleep(max(0.35, delay))
                continue
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (requests.RequestException, ValueError):
            # Never include exception text or request headers in a log.
            return None
    return None


def prefetch(symbols, start, end):
    """Fetch inclusive NY dates in batches, caching only fully fetched ranges.

    Return True for a successful fetch (including empty results), None when
    disabled, unsupported or failed. A failed page is never cached as complete.
    """
    if not enabled():
        return None
    from kline_integrity import session_complete, utcnow
    start = max(date.fromisoformat(str(start)[:10]), MIN_DAY)
    end = min(date.fromisoformat(str(end)[:10]), datetime.now(NY).date())
    now = datetime.now(timezone.utc)
    while end >= start and (end.weekday() >= 5 or not session_complete(end, "AAPL", now)):
        end -= timedelta(days=1)
    if start > end:
        return None
    names = {alpaca_symbol(s): s for s in symbols if alpaca_symbol(s)}
    if not names:
        return None
    with _lock:
        names = {a: s for a, s in names.items()
                 if not any(lo <= start and hi >= end for lo, hi in _coverage.get(s, ()))}
        keys = sorted(names)
        for offset in range(0, len(keys), 100):
            chunk = keys[offset:offset + 100]
            upper = min(datetime.combine(end + timedelta(days=1), day_time.min, NY)
                        .astimezone(timezone.utc) - timedelta(microseconds=1),
                        now - timedelta(minutes=16, seconds=1))
            params = dict(symbols=",".join(chunk), timeframe="1Day",
                          start=datetime.combine(start, day_time.min, NY).astimezone(timezone.utc).isoformat(),
                          end=upper.isoformat(), adjustment="split", feed="sip", limit=10000)
            staged = {}
            seen_tokens = set()
            while True:
                payload = _request(params)
                if payload is None:
                    return None
                try:
                    bars = payload.get("bars") or {}
                    if not isinstance(bars, dict):
                        return None
                    source = URL + "?" + urlencode(params)
                    fetched = utcnow()
                    for name, entries in bars.items():
                        if name not in chunk:
                            continue
                        for item in entries:
                            day = trading_day(item["t"])
                            if start <= day <= end and day.weekday() < 5 and session_complete(day, names[name], now):
                                values = dict(zip(("Open", "High", "Low", "Close", "Volume"),
                                                  (item.get(k) for k in ("o", "h", "l", "c", "v"))))
                                staged[(names[name], day.isoformat())] = (values, source, fetched)
                    token = payload.get("next_page_token")
                    if not token:
                        break
                    if not isinstance(token, str) or token in seen_tokens:
                        return None
                    seen_tokens.add(token)
                    params["page_token"] = token
                except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
                    return None
            _cache.update(staged)
            for name in chunk:
                _coverage.setdefault(names[name], []).append((start, end))
        return True


def alpaca_bar(symbol, day):
    if not enabled() or not alpaca_symbol(symbol):
        return None
    day = str(day)[:10]
    if prefetch([symbol], day, day) is None:
        return None
    return _cache.get((symbol, day))
