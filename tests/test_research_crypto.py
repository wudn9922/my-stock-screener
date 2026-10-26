"""A true BTC/USDT spot pair, UTC completed sessions and immutable-source failures."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_crypto import API, SYMBOL, build_btc_usdt, parse_klines, parse_spot_pair
from research_candles import read_csv
from research_io import SourceError
from research_publish import world_files


def pair_payload():
    return {"timezone": "UTC", "symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC",
            "quoteAsset": "USDT", "isSpotTradingAllowed": True, "status": "TRADING"}]}


def kline(day, volume="1.25"):
    opened = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return [opened, "100", "110", "90", "105", volume, opened + 86_400_000 - 1, "0", 1, "0", "0", "0"]


class Transport:
    def __init__(self, rows):
        self.rows, self.calls, self.error = rows, [], None

    def json(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return pair_payload() if "exchangeInfo" in url else self.rows


class CryptoCandlesTest(unittest.TestCase):
    def test_publisher_refuses_an_accidentally_relabelled_btc_usd_pair(self):
        asset = {"candlePath": "candles/btcusdt.json", "symbol": SYMBOL,
                 "providerSymbol": "BTCUSDT", "baseCurrency": "BTC", "quoteCurrency": "USDT",
                 "instrumentType": "crypto-spot", "timezone": "UTC", "unit": "USDT/BTC", "volumeUnit": "BTC"}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            def write(value):
                (root / "world-assets.json").write_text(json.dumps({"series": {"btcusdt": value}}))
            write(asset)
            self.assertEqual(world_files(root)["btcusdt"], asset)
            for field, value in (("providerSymbol", "BTCUSD"), ("quoteCurrency", "USD"),
                                 ("baseCurrency", "ETH"), ("instrumentType", "futures")):
                write(dict(asset, **{field: value}))
                with self.assertRaisesRegex(ValueError, "BTC/USDT"):
                    world_files(root)

    def test_pair_identity_refuses_btc_usd_or_nonspot_substitutes(self):
        parse_spot_pair(pair_payload())
        for key, value in (("quoteAsset", "USD"), ("symbol", "BTCUSD"), ("isSpotTradingAllowed", False)):
            p = pair_payload()
            p["symbols"][0][key] = value
            with self.assertRaises(SourceError):
                parse_spot_pair(p)

    def test_weekend_trades_are_kept_and_today_is_not_a_completed_utc_session(self):
        rows = parse_klines([kline("2026-10-24"), kline("2026-10-25"), kline("2026-10-26")],
                            datetime(2026, 10, 26, 23, 59, tzinfo=timezone.utc))
        self.assertEqual([r[0] for r in rows], ["2026-10-24", "2026-10-25"])
        self.assertEqual(rows[-1][1:], [100, 110, 90, 105, 1.25])

    def test_invalid_response_never_becomes_a_partial_or_fabricated_candle(self):
        for payload in ([], {}, [{}], [None], ["schema changed"], [[0]], [kline("2026-10-24"), kline("2026-10-24")],
                        [kline("2026-10-24", "nan")], [kline("2026-10-24", "-1")]):
            with self.subTest(payload=payload), self.assertRaises(SourceError):
                parse_klines(payload)
        bad = kline("2026-10-24")
        bad[2] = "99"
        with self.assertRaises(SourceError):
            parse_klines([bad])

    def test_snapshot_and_record_use_usdt_quote_btc_volume_and_utc_dates(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            transport = Transport([kline("2026-10-24"), kline("2026-10-25")])
            now = datetime(2026, 10, 25, 23, tzinfo=timezone.utc)
            asset = build_btc_usdt(root / "research/world-assets.json", root / "data", transport, now=now)
            self.assertEqual(asset["status"], "ok")
            self.assertEqual((asset["providerSymbol"], asset["quoteCurrency"], asset["volumeUnit"]), ("BTCUSDT", "USDT", "BTC"))
            self.assertEqual((asset["timezone"], asset["asOf"], asset["unit"]), ("UTC", "2026-10-24", "USDT/BTC"))
            csv = root / "data" / (SYMBOL + ".csv")
            body = root / "research/candles/btcusdt.json"
            self.assertEqual(json.loads(body.read_text())["bars"], read_csv(csv))
            before = csv.read_bytes(), body.read_bytes()
            transport.error = SourceError("rate limit", status=429)
            stale = build_btc_usdt(root / "research/world-assets.json", root / "data", transport, asset, now)
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["asOf"], asset["asOf"])
            self.assertEqual(before, (csv.read_bytes(), body.read_bytes()))

    def test_malformed_row_retains_prior_snapshot_and_never_writes_source_rows(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            now = datetime(2026, 10, 25, 23, tzinfo=timezone.utc)
            transport = Transport([kline("2026-10-24")])
            asset = build_btc_usdt(root / "research/world-assets.json", root / "data", transport, now=now)
            csv = root / "data" / (SYMBOL + ".csv")
            candles = root / "research/candles/btcusdt.json"
            before = csv.read_bytes(), candles.read_bytes()
            transport.rows = [{}]
            stale = build_btc_usdt(root / "research/world-assets.json", root / "data", transport, asset, now)
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["asOf"], asset["asOf"])
            self.assertIn("row schema changed", stale["missingReason"])
            self.assertEqual(before, (csv.read_bytes(), candles.read_bytes()))

    def test_invalid_source_does_not_create_a_permanent_record(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            transport = Transport([kline("2026-10-24", "-10")])
            result = build_btc_usdt(root / "research/world-assets.json", root / "data", transport)
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse((root / "data" / (SYMBOL + ".csv")).exists())

    def test_pagination_advances_and_includes_more_than_one_thousand_days(self):
        start = datetime(2021, 1, 1, tzinfo=timezone.utc)
        raw = [kline((start + timedelta(days=i)).date().isoformat()) for i in range(1001)]
        class Paged(Transport):
            def json(self, url):
                self.calls.append(url)
                if "exchangeInfo" in url:
                    return pair_payload()
                return raw[:1000] if len(self.calls) == 2 else raw[1000:]
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            transport = Paged(raw)
            result = build_btc_usdt(root / "research/world-assets.json", root / "data", transport)
            self.assertEqual(result["observations"], 1001)
            self.assertIn(f"startTime={raw[1000][0]}", transport.calls[-1])


if __name__ == "__main__":
    unittest.main()
