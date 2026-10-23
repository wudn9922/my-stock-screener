"""Candle provenance, source quality, retention and publication contracts."""

import copy
import hashlib
import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_candles import ASSETS, parse_ohlc, save_csv, read_csv, build_world_candles
from research_io import SourceError, read
from research_publish import stage, overlay


def payload(symbol="GC=F"):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "dataGranularity": "1d",
                        "exchangeTimezoneName": "America/New_York",
                        "currency": "USD",
                        "instrumentType": "FUTURE",
                        "shortName": "Gold",
                    },
                    "timestamp": [1792512000, 1792598400, 1792684800],
                    "indicators": {
                        "quote": [
                            {
                                "open": [10, 11, 12],
                                "high": [12, 13, 14],
                                "low": [9, 10, 11],
                                "close": [11, 12, 13],
                                "volume": [100, None, 120],
                            }
                        ]
                    },
                }
            ]
        }
    }


class CandleSources(unittest.TestCase):
    def test_latest_completed_invalid_bar_refetches_once_before_the_forming_row(self):
        from unittest.mock import Mock
        p = payload()
        p['chart']['result'][0]['indicators']['quote'][0]['high'][1] = 1
        retry = Mock(return_value=payload())
        rows, _ = parse_ohlc(p, 'GC=F', datetime(2026,10,22,18,tzinfo=timezone.utc), refetch=retry)
        retry.assert_called_once()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1][2], 13)

    def test_overnight_active_session_is_excluded_even_if_its_date_is_yesterday(self):
        p = payload()
        item = p['chart']['result'][0]
        start = int(datetime(2026,10,21,22,tzinfo=timezone.utc).timestamp())
        end = int(datetime(2026,10,22,21,tzinfo=timezone.utc).timestamp())
        item['timestamp'] = [int(datetime(2026,10,20,4,tzinfo=timezone.utc).timestamp()),start,start+3600]
        item['meta']['currentTradingPeriod'] = {'regular':{'start':start,'end':end}}
        rows, _ = parse_ohlc(p,'GC=F',datetime(2026,10,22,18,tzinfo=timezone.utc))
        self.assertEqual(len(rows),1)

    def test_real_ohlc_missing_volume_and_forming_session(self):
        rows, meta = parse_ohlc(
            payload(), "GC=F", datetime(2026, 10, 22, 18, tzinfo=timezone.utc)
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1:5], [10, 12, 9, 11])
        self.assertIsNone(rows[-1][-1])

    def test_source_symbol_interval_and_invalid_prices(self):
        with self.assertRaises(SourceError):
            parse_ohlc(payload(), "CL=F")
        p = payload()
        p["chart"]["result"][0]["meta"]["dataGranularity"] = "1mo"
        with self.assertRaises(SourceError):
            parse_ohlc(p, "GC=F")
        p = payload()
        p["chart"]["result"][0]["indicators"]["quote"][0]["high"][0] = 1
        rows, meta = parse_ohlc(
            p, "GC=F", datetime(2026, 10, 22, 18, tzinfo=timezone.utc)
        )
        self.assertEqual(meta["excludedInvalidOHLC"], 1)
        self.assertEqual(len(rows), 1)

    def test_nonpositive_prices_are_rejected_under_the_positive_kline_policy(self):
        p = payload()
        q = p["chart"]["result"][0]["indicators"]["quote"][0]
        q.update(open=[-10] * 3, high=[-8] * 3, low=[-13] * 3, close=[-11] * 3)
        with self.assertRaises(SourceError):
            parse_ohlc(p, "GC=F")

    def test_csv_noop_atomic_failure_and_historical_deletion_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.csv"
            rows = [["2026-10-20", 1, 3, 0.5, 2, None]]
            save_csv(p, rows)
            before = p.read_bytes(), p.stat().st_mtime_ns
            save_csv(p, rows)
            self.assertEqual(before, (p.read_bytes(), p.stat().st_mtime_ns))
            with self.assertRaises(ValueError):
                save_csv(p, [])
            with patch("research_candles.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    save_csv(p, rows + [["2026-10-21", 2, 4, 1, 3, 10]])
            self.assertEqual(p.read_bytes(), before[0])

    def test_rerun_source_failure_preserves_candles_and_dates(self):
        class Transport:
            fail = False
            calls = 0

            def json(self, url):
                from urllib.parse import unquote

                self.calls += 1
                symbol = unquote(url.split("/chart/")[1].split("?")[0])
                p = payload(symbol)
                if self.fail:
                    raise SourceError("429", status=429)
                if symbol.endswith("=X"):
                    p["chart"]["result"][0]["meta"].update(
                        instrumentType="CURRENCY",
                        currency=next(
                            v[3].split("/")[0]
                            for v in ASSETS.values()
                            if v[1] == symbol
                        ),
                    )
                return p

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport = Transport()
            out = root / "research/world-assets.json"
            data = root / "data"
            build_world_candles(out, data, transport)
            before = {
                p.name: p.read_bytes() for p in (out.parent / "candles").glob("*.json")
            }
            csv_before = {
                p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in data.glob("*.csv")
            }
            self.assertEqual(len(before), 11)
            build_world_candles(out, data, transport)
            self.assertEqual(
                before,
                {
                    p.name: p.read_bytes()
                    for p in (out.parent / "candles").glob("*.json")
                },
            )
            self.assertEqual(
                csv_before,
                {
                    p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in data.glob("*.csv")
                },
            )
            transport.fail = True
            before_limit = transport.calls
            build_world_candles(out, data, transport)
            self.assertEqual(transport.calls, before_limit + 1)
            self.assertEqual(
                before,
                {
                    p.name: p.read_bytes()
                    for p in (out.parent / "candles").glob("*.json")
                },
            )
            self.assertEqual(read(out)["series"]["gold"]["status"], "stale")
            stage(out.parent, root / "artifacts", "world", data)
            overlay(root / "artifacts", root / "published", root / "retained")
            self.assertEqual(len(list((root / "retained").glob("*.csv"))), 11)
            kept = root / "retained/CMD-WTI.csv"
            rows = read_csv(kept)
            later = date.fromisoformat(rows[-1][0]) + timedelta(days=1)
            save_csv(kept, rows + [[later.isoformat(), 1, 3, 0.5, 2, None]],
                     now=datetime.fromisoformat(later.isoformat()).replace(tzinfo=timezone.utc)+timedelta(days=2))
            with self.assertRaisesRegex(ValueError, "retained"):
                overlay(root / "artifacts", root / "published", root / "retained")

    def test_snapshot_never_contains_a_bar_the_record_deferred(self):
        """FX sessions are judged in New York time for the record but in the
        exchange zone for the snapshot; the snapshot must follow the record."""
        import research_candles

        class Transport:
            def json(self, url):
                from urllib.parse import unquote

                symbol = unquote(url.split("/chart/")[1].split("?")[0])
                p = payload(symbol)
                meta = p["chart"]["result"][0]["meta"]
                if symbol.endswith("=X"):
                    meta.update(instrumentType="CURRENCY", exchangeTimezoneName="Europe/London",
                                currency=next(v[3].split("/")[0] for v in ASSETS.values() if v[1] == symbol))
                return p

        stamps = payload()["chart"]["result"][0]["timestamp"]
        latest = datetime.fromtimestamp(max(stamps), timezone.utc).date().isoformat()
        def session_complete(day, symbol, now=None, zone=None):
            return not (str(day)[:10] == latest and zone == "America/New_York")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out, data = root / "research/world-assets.json", root / "data"
            with patch.object(research_candles.kline, "session_complete", session_complete), \
                 patch.object(research_candles.kline, "LOG_PATH", root / "log.json"):
                build_world_candles(out, data, Transport())
            for key, asset in ASSETS.items():
                record = read_csv(data / (asset[2] + ".csv"))
                bars = json.loads((out.parent / f"candles/{key}.json").read_text())["bars"]
                self.assertNotIn(latest, [r[0] for r in record])
                self.assertEqual(bars, record, key)

    def test_all_committed_candles_have_real_bounds_and_matching_records(self):
        root = Path(__file__).resolve().parents[1]
        # main retains CSV evidence, not view snapshots. Rebuild offline in a
        # temporary directory; CI additionally validates its restored live tree.
        work = self.enterContext(tempfile.TemporaryDirectory())
        data = Path(work) / "data"
        shutil.copytree(root / "data/world", data)

        class RetainedSource:
            def json(self, url):
                from urllib.parse import unquote
                ticker = unquote(url.split("/chart/")[1].split("?")[0])
                name, _, symbol, unit = next(a for a in ASSETS.values() if a[1] == ticker)
                row = read_csv(data / (symbol + ".csv"))[-1]
                packet = payload(ticker)
                item = packet["chart"]["result"][0]
                item["meta"].update(
                    instrumentType="CURRENCY" if symbol.startswith("FX-") else "FUTURE",
                    exchangeTimezoneName="UTC", currency=unit.split("/")[0], shortName=name,
                )
                item["timestamp"] = [int(datetime.fromisoformat(row[0]).replace(
                    hour=12, tzinfo=timezone.utc).timestamp())]
                item["indicators"]["quote"] = [dict(zip(
                    ("open", "high", "low", "close", "volume"), ([v] for v in row[1:])
                ))]
                return packet

        research = Path(work) / "research"
        build_world_candles(research / "world-assets.json", data, RetainedSource())
        self.assertEqual(
            {p.name: p.read_bytes() for p in data.glob("*.csv")},
            {p.name: p.read_bytes() for p in (root / "data/world").glob("*.csv")},
        )
        if os.environ.get("RESEARCH_REPORT_DIR"):
            research = Path(os.environ["RESEARCH_REPORT_DIR"])
        index = read(research / "world-assets.json")
        for key, (_, ticker, symbol, unit) in ASSETS.items():
            a = index["series"][key]
            self.assertEqual(a["providerSymbol"], ticker)
            body = (research / a["candlePath"]).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), a["candleSha256"])
            rows = json.loads(body)["bars"]
            self.assertGreater(len(rows), 1000)
            self.assertEqual(rows, read_csv(root / "data/world" / (symbol + ".csv")))
            for _, o, h, l, c, _ in rows:
                self.assertGreaterEqual(h, max(o, c, l))
                self.assertLessEqual(l, min(o, c, h))
            self.assertNotIn("history", a)
            self.assertEqual(a["unit"], unit)


if __name__ == "__main__":
    unittest.main()
