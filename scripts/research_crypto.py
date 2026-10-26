"""Binance BTC/USDT spot daily OHLC, quoted in USDT with BTC base volume.

The public market-data host needs no API key. Only completed UTC days enter the
permanent record and published candle snapshot; BTC/USD is never a substitute.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path

import kline_integrity as kline
from research_io import SourceError, meta, retain, stamp, write
from research_candles import FIELDS, read_csv, save_csv

API = "https://data-api.binance.vision/api/v3"
SYMBOL = "CRYPTO-BTCUSDT"
PROVIDER_SYMBOL = "BTCUSDT"
PAIR_URL = "https://www.binance.com/en/trade/BTC_USDT?type=spot"
SOURCE = "Binance BTC/USDT spot daily OHLC"
DAY_MS = 86_400_000


def parse_spot_pair(payload):
    try:
        pair, = payload["symbols"]
        if (pair["symbol"], pair["baseAsset"], pair["quoteAsset"]) != ("BTCUSDT", "BTC", "USDT"):
            raise SourceError("Binance pair is not BTC/USDT")
        if payload["timezone"] != "UTC" or not pair["isSpotTradingAllowed"] or pair["status"] != "TRADING":
            raise SourceError("BTC/USDT is not an active UTC spot pair")
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("Binance spot-pair metadata schema changed") from exc


def parse_klines(payload, now=None):
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today_ms = int(clock.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    if not isinstance(payload, list) or not payload:
        raise SourceError("No BTC/USDT daily klines from Binance")
    rows, previous = [], -1
    try:
        for raw in payload:
            if not isinstance(raw, (list, tuple)) or len(raw) < 7:
                raise SourceError("Binance daily-kline row schema changed")
            opened, closed = raw[0], raw[6]
            if (not isinstance(opened, int) or isinstance(opened, bool)
                    or opened % DAY_MS or closed != opened + DAY_MS - 1
                    or opened <= previous):
                raise SourceError("Binance klines are not ascending unique UTC daily candles")
            previous = opened
            if opened >= today_ms:
                continue
            values = [float(raw[i]) for i in (1, 2, 3, 4, 5)]
            if any(not math.isfinite(v) for v in values) or values[-1] < 0:
                raise SourceError("Invalid BTC/USDT source prices or base volume")
            if kline.violation(dict(zip(FIELDS[1:], values))):
                raise SourceError("Invalid BTC/USDT source OHLC bounds")
            if values[-1].is_integer():
                values[-1] = int(values[-1])
            day = datetime.fromtimestamp(opened / 1000, timezone.utc).date().isoformat()
            rows.append([day, *values])
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise SourceError("Binance daily-kline response schema changed") from exc
    return rows


def build_btc_usdt(out, data_dir, http, old=None, now=None):
    """Build just this genuine pair; source failure retains its existing record."""
    out, data_dir = Path(out), Path(data_dir)
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = data_dir / (SYMBOL + ".csv")
    try:
        parse_spot_pair(http.json(API + "/exchangeInfo?symbol=BTCUSDT"))
        retained = read_csv(path)
        start = (datetime.fromisoformat(retained[-1][0]).replace(tzinfo=timezone.utc) - timedelta(days=30)
                 if retained else clock.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=5 * 366))
        start_ms = int(start.timestamp() * 1000)
        fresh = []
        # Five years fit in two 1,000-day pages; cap catch-up requests as well.
        for _ in range(8):
            url = API + f"/klines?symbol=BTCUSDT&interval=1d&limit=1000&startTime={start_ms}"
            payload = http.json(url)
            rows = parse_klines(payload, clock)
            fresh.extend(rows)
            next_ms = payload[-1][0] + DAY_MS
            if len(payload) < 1000 or next_ms >= int(clock.timestamp() * 1000):
                break
            if next_ms <= start_ms:
                raise SourceError("Binance pagination did not advance")
            start_ms = next_ms
        else:
            raise SourceError("BTC/USDT source exceeded the bounded history request budget")
        if not fresh:
            raise SourceError("No completed UTC BTC/USDT daily candles")
        merged = {r[0]: r for r in retained}
        merged.update({r[0]: r for r in fresh})
        save_csv(path, [merged[d] for d in sorted(merged)], now=clock, zone="UTC", source_url=API + "/klines?symbol=BTCUSDT&interval=1d")
        blocked = kline.quarantined_dates(SYMBOL)
        usable = [r for r in read_csv(path) if r[0] not in blocked
                  and not kline.violation(dict(zip(FIELDS[1:], r[1:])))
                  and kline.session_complete(r[0], SYMBOL, now=clock, zone="UTC")]
        if not usable:
            raise SourceError("No usable completed BTC/USDT spot candles")
        relative = "candles/btcusdt.json"
        candle_path = out.parent / relative
        write(candle_path, dict(version=1, symbol=SYMBOL, interval="1D", fields=FIELDS, bars=usable))
        return dict(meta(SOURCE, PAIR_URL, usable[-1][0], fetched=stamp()),
                    name="BTC/USDT", symbol=SYMBOL, providerSymbol=PROVIDER_SYMBOL,
                    frequency="daily", unit="USDT/BTC", instrumentType="crypto-spot",
                    pricePrecision=2, timezone="UTC", latest=usable[-1][4],
                    candlePath=relative, candleSha256=hashlib.sha256(candle_path.read_bytes()).hexdigest(),
                    csvSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    baseCurrency="BTC", quoteCurrency="USDT", volumeUnit="BTC",
                    quoteDirection="每 1 BTC 兌 USDT",
                    priceBasis="Binance BTC/USDT spot OHLC; 24/7 UTC daily sessions; volume in BTC. USDT is the quote asset, not USD.",
                    coverageFrom=usable[0][0], observations=len(usable), excludedInvalidOHLC=0)
    except SourceError as exc:
        print("btcusdt", str(exc), flush=True)
        return retain(old, SOURCE, PAIR_URL, exc)
