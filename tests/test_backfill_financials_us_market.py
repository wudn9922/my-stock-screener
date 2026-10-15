"""Offline safety and resume tests for the bounded US market SEC backfill."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atlas" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import backfill_financials_us_market as market  # noqa: E402
import build_financials as financials  # noqa: E402


class DirectoryAndIssuerScreenTests(unittest.TestCase):
    def test_directory_row_audit_matches_filters_and_labels_spac_as_heuristic(self):
        nasdaq = """Symbol|Security Name|ETF|Test Issue|NextShares
ACME|Acme Common Stock|N|N|N
FUND|Example Fund|N|N|N
SPAC|Example Acquisition Corp. Common Stock|N|N|N
TEST|Test Common Stock|N|Y|N
WARR|Acme Warrants|N|N|N
File Creation Time: 20261015
"""
        other = """ACT Symbol|Security Name|Exchange|ETF|Test Issue
BETA|Beta Corporation Common Shares|N|N|N
ODD|Odd Corporation Common Shares|X|N|N
File Creation Time: 20261015
"""
        symbols, audit = market.classify_directory_rows(nasdaq, other)
        self.assertEqual(set(symbols), {"ACME", "BETA"})
        excluded = {row["rawSymbol"]: row for row in audit if row["decision"] == "excluded"}
        self.assertEqual(excluded["SPAC"]["reason"], "spac")
        self.assertEqual(excluded["SPAC"]["reasonBasis"], "issuer-name heuristic in universe_sources.py")
        self.assertEqual(excluded["TEST"]["reasonBasis"], "NasdaqTrader Test Issue flag")
        self.assertEqual(excluded["ODD"]["reason"], "exchange")

    def test_only_sic_6770_is_confirmed_blank_check_and_mappings_are_checked(self):
        payload = {"cik": 12, "sic": "6770", "entityType": "operating", "tickers": ["DUAL", "DUAL-A"]}
        result, evidence = market.submissions_classification(payload, "0000000012", ["DUAL", "DUAL-A"])
        self.assertEqual(result, "excluded_sic_6770")
        self.assertIn("== 6770", evidence["basis"])

        payload.update(sic="3571", entityType="shell")
        result, evidence = market.submissions_classification(payload, "0000000012", ["DUAL", "DUAL-A"])
        self.assertEqual(result, "operating_candidate")
        self.assertIn("does not certify", evidence["limitation"])

        wrong_cik = dict(payload, cik=13)
        result, _ = market.submissions_classification(wrong_cik, "0000000012", ["DUAL", "DUAL-A"])
        self.assertEqual(result, "unknownmetadata")
        wrong_class = dict(payload, tickers=["DUAL"])
        result, _ = market.submissions_classification(wrong_class, "0000000012", ["DUAL", "DUAL-A"])
        self.assertEqual(result, "unknownmetadata")
        missing_sic = dict(payload)
        missing_sic.pop("sic")
        result, _ = market.submissions_classification(missing_sic, "0000000012", ["DUAL", "DUAL-A"])
        self.assertEqual(result, "unknownmetadata")


class CacheAndQueueTests(unittest.TestCase):
    def test_raw_cache_validates_body_hash_and_requested_cik(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = market.RawResponseCache(Path(tmp))
            url = market.SUBMISSIONS_URL.format(cik="0000000012")
            body = json.dumps({"cik": 12, "sic": "3571"}).encode()
            cache.store(url, 200, {"Content-Type": "application/json"}, body)
            valid, payload = cache.cached_json(url, lambda doc: market._cik_text(doc.get("cik")) == "0000000012")
            self.assertTrue(valid)
            self.assertEqual(payload["sic"], "3571")

            valid, _ = cache.cached_json(url, lambda doc: market._cik_text(doc.get("cik")) == "0000000099")
            self.assertFalse(valid)
            self.assertIn("CIK/schema identity", cache.cache_invalid[-1]["reason"])

            entry = cache.manifest["responses"][url]
            (Path(tmp) / entry["file"]).write_bytes(b"corrupt")
            valid, _ = cache.cached_json(url)
            self.assertFalse(valid)
            self.assertIn("SHA-256 mismatch", cache.cache_invalid[-1]["reason"])

    def test_explicit_unknown_metadata_retry_bypasses_cached_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = market.RawResponseCache(Path(tmp))
            url = market.SUBMISSIONS_URL.format(cik="0000000012")
            cache.store(url, 200, {}, json.dumps({"cik": 12}).encode())
            client = market.CachedSecClient("research contact test@example.com", cache, sleeper=lambda _: None)
            self.assertNotIn("sic", client.submissions("12"))

            fresh = {"cik": 12, "sic": "3571"}
            with mock.patch.object(client.wire, "get_json", return_value=fresh) as fetch:
                self.assertEqual(client.submissions("12", force_network=True), fresh)
                fetch.assert_called_once_with(url, allow_404=False)

    def test_submission_cache_is_reused_only_within_24_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = market.RawResponseCache(Path(tmp))
            url = market.SUBMISSIONS_URL.format(cik="0000000012")
            cached = {"cik": 12, "sic": "3571"}
            cache.store(url, 200, {}, json.dumps(cached).encode())
            client = market.CachedSecClient("research contact test@example.com", cache, sleeper=lambda _: None)
            with mock.patch.object(client.wire, "get_json") as wire_fetch:
                self.assertEqual(client.submissions("12"), cached)
                wire_fetch.assert_not_called()

            entry = cache.manifest["responses"][url]
            entry["retrievedAt"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
            market.atomic_json(cache.manifest_path, cache.manifest)
            fresh = {"cik": 12, "sic": "6770"}
            with mock.patch.object(client.wire, "get_json", return_value=fresh) as wire_fetch:
                self.assertEqual(client.submissions("12"), fresh)
                wire_fetch.assert_called_once_with(url, allow_404=False)

    def test_companyfacts_are_downloaded_once_per_cik_for_two_share_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = market.RawResponseCache(Path(tmp))
            client = market.CachedSecClient("research contact test@example.com", cache, sleeper=lambda _: None)
            facts = {"cik": 12, "facts": {}}
            with mock.patch.object(client, "get_json", return_value=facts) as fetch:
                self.assertIs(client.company_facts("12"), facts)
                self.assertIs(client.company_facts("12"), facts)
                fetch.assert_called_once()

    def test_403_stops_after_one_sec_request_and_keeps_full_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = market.RawResponseCache(Path(tmp))
            client = market.CachedSecClient("research contact test@example.com", cache, sleeper=lambda _: None)
            headers = Message()
            headers["X-SEC-Test"] = "preserved"
            body = b"SEC denied this request"
            calls = []

            def denied(request, timeout):
                calls.append(request.full_url)
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", headers, io.BytesIO(body))

            with mock.patch.object(market.urllib.request, "urlopen", side_effect=denied):
                with self.assertRaises(market.SECForbidden) as raised:
                    client.submissions("12")
            self.assertEqual(len(calls), 1)
            self.assertEqual(client.requests, 1)
            self.assertEqual(raised.exception.response["headers"]["X-SEC-Test"], "preserved")
            self.assertEqual(raised.exception.response["bodyUtf8"], body.decode())
            self.assertEqual(raised.exception.response["bodyBase64"], "U0VDIGRlbmllZCB0aGlzIHJlcXVlc3Q=")


class PersistenceAndRecoveryTests(unittest.TestCase):
    @staticmethod
    def document(symbol: str, source: str, values: dict[str, float | None]) -> dict:
        return {
            "version": 1,
            "symbol": symbol,
            "market": "US",
            "currency": "USD",
            "fyEndMonth": 12,
            "split": {"adjusted": False, "events": []},
            "annual": {
                "end": ["2024-12-31"], "fy": [2024], "src": [source],
                "values": {field: [value] for field, value in values.items()},
            },
            "quarterly": {"end": [], "fy": [], "fq": [], "src": [], "values": {}},
        }

    def test_staging_preserves_current_yahoo_nulls_and_keeps_sec_nonnull_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            financials_dir = Path(tmp) / "financials"
            financials_dir.mkdir()
            symbol = "ACME"
            old = self.document(symbol, "yahoo", {
                "revenue": 100, "costOfRevenue": 60, "grossProfit": 40,
                "netIncome": 11, "epsDiluted": 1.1, "epsBasic": 1.0, "dilutedShares": 10,
            })
            target = financials_dir / f"{symbol}.json"
            target.write_text(json.dumps(old), encoding="utf-8")
            sec_doc = self.document(symbol, "sec", {
                "revenue": 100, "costOfRevenue": None, "grossProfit": None,
                "netIncome": 15, "epsDiluted": None, "epsBasic": None, "dilutedShares": 10,
            })

            def fake_seed(ticker, cik_map, client, out_dir, force, now, splits=None):
                self.assertEqual(ticker, symbol)
                self.assertEqual(cik_map, {symbol: "0000000012"})
                financials.write_atomic(str(Path(out_dir) / f"{symbol}.json"), sec_doc)
                return {"result": "written", "document": sec_doc}

            with mock.patch.object(market, "FINANCIALS_DIR", financials_dir), \
                    mock.patch.object(market.sec, "seed_ticker", side_effect=fake_seed):
                outcome = market.seed_symbol_preserving_yahoo(
                    symbol, "0000000012", object(), None,
                    datetime(2026, 10, 15, tzinfo=timezone.utc), "c1c9b4e1e",
                )

            final = json.loads(target.read_text(encoding="utf-8"))
            values = final["annual"]["values"]
            self.assertEqual(final["annual"]["src"], ["sec"])
            self.assertEqual(values["revenue"], [100])
            self.assertEqual(values["netIncome"], [15])  # fresh SEC values win
            self.assertEqual(values["costOfRevenue"], [60])
            self.assertEqual(values["grossProfit"], [40])
            self.assertEqual(values["epsDiluted"], [1.1])  # same fiscal/split/share basis
            self.assertEqual(values["epsBasic"], [1.0])
            self.assertEqual(outcome["restoration"]["referenceSource"], "current pre-seed financial file")
            self.assertTrue(outcome["restoration"]["restoredRows"]["annual"])

    def test_share_classes_publish_separate_files_while_reusing_one_cik_facts_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            financials_dir = Path(tmp) / "financials"
            financials_dir.mkdir()
            cache = market.RawResponseCache(Path(tmp) / "cache")
            client = market.CachedSecClient("research contact test@example.com", cache, sleeper=lambda _: None)
            facts = {"cik": 12, "facts": {}}
            fetch_calls = []
            real_get_json = client.get_json

            def get_json(*args, **kwargs):
                fetch_calls.append(args[0])
                return facts

            seeded_symbols = []

            def fake_seed(ticker, cik_map, passed_client, out_dir, force, now, splits=None):
                self.assertIs(passed_client, client)
                self.assertEqual(cik_map, {ticker: "0000000012"})
                passed_client.company_facts("12")
                seeded_symbols.append(ticker)
                document = self.document(ticker, "sec", {"revenue": 100})
                financials.write_atomic(str(Path(out_dir) / f"{ticker}.json"), document)
                return {"result": "written", "document": document}

            with mock.patch.object(market, "FINANCIALS_DIR", financials_dir), \
                    mock.patch.object(client, "get_json", side_effect=get_json), \
                    mock.patch.object(market.sec, "seed_ticker", side_effect=fake_seed):
                market.seed_symbol_preserving_yahoo("DUAL", "0000000012", client, None,
                                                     datetime(2026, 10, 15, tzinfo=timezone.utc), "baseline")
                market.seed_symbol_preserving_yahoo("DUAL-A", "0000000012", client, None,
                                                     datetime(2026, 10, 15, tzinfo=timezone.utc), "baseline")

            self.assertEqual(seeded_symbols, ["DUAL", "DUAL-A"])
            self.assertEqual(fetch_calls, [market.sec.FACTS_URL.format(cik="0000000012")])
            self.assertTrue((financials_dir / "DUAL.json").is_file())
            self.assertTrue((financials_dir / "DUAL-A.json").is_file())
            self.assertEqual(json.loads((financials_dir / "DUAL.json").read_text())["symbol"], "DUAL")
            self.assertEqual(json.loads((financials_dir / "DUAL-A.json").read_text())["symbol"], "DUAL-A")

    def test_resume_reconciles_index_after_file_write_before_checkpoint_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            financials_dir = Path(tmp) / "financials"
            financials_dir.mkdir()
            document = self.document("CRASH", "sec", {"revenue": 100})
            financials.write_atomic(str(financials_dir / "CRASH.json"), document)
            financials.write_atomic(str(financials_dir / "index.json"), {
                "version": financials.VERSION, "generatedAt": "2026-01-01T00:00:00Z", "items": {},
            })
            with mock.patch.object(market, "FINANCIALS_DIR", financials_dir):
                result = market.reconcile_financial_index({"CRASH"})
            index = json.loads((financials_dir / "index.json").read_text(encoding="utf-8"))
            self.assertIn("CRASH", index["items"])
            self.assertEqual(index["items"]["CRASH"]["latestAnnual"], "2024-12-31")
            self.assertEqual(result["state"], "updated")
            self.assertIn("CRASH", result["validatedSymbols"])

    def test_snapshot_without_progress_is_recoverable_after_ticker_map_failure(self):
        symbols = [f"A{i:04d}" for i in range(3000)]
        cohort_hash = hashlib.sha256("\n".join(symbols).encode()).hexdigest()
        snapshot = {
            "version": 1, "snapshotAt": "2026-10-15T00:00:00Z", "sourcePairSha256": "sources",
            "cohortSymbolsSha256": cohort_hash,
            "eligibleSymbols": [{"symbol": symbol, "name": symbol, "exchange": "NASDAQ"} for symbol in symbols],
        }
        mapping = {symbol: f"{index + 1:010d}" for index, symbol in enumerate(symbols)}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot-original.json"
            progress_path = root / "progress.json"
            summary_path = root / "summary.json"
            raw_cache = root / "raw"
            args = market.parse_args([
                "--snapshot", str(snapshot_path), "--progress", str(progress_path),
                "--summary", str(summary_path), "--raw-cache-dir", str(raw_cache), "--batch-size", "1",
            ])
            with mock.patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}), \
                    mock.patch.object(market.yahoo_restore, "resolve_revision", return_value="baseline"), \
                    mock.patch.object(market, "fetch_directory_snapshot", return_value=snapshot), \
                    mock.patch.object(market.CachedSecClient, "ticker_map", side_effect=market.sec.FetchError("offline")):
                with self.assertRaises(market.sec.FetchError):
                    market.run_batch(args)
            self.assertEqual(json.loads(progress_path.read_text())["status"], "needs_ticker_map")

            # Moving the immutable snapshot is supported: its hash, not a machine path, pins identity.
            moved_snapshot = root / "snapshot-moved.json"
            moved_snapshot.write_bytes(snapshot_path.read_bytes())
            resume_args = market.parse_args([
                "--snapshot", str(moved_snapshot), "--progress", str(progress_path),
                "--summary", str(summary_path), "--raw-cache-dir", str(raw_cache), "--batch-size", "1", "--resume",
            ])
            validation = {"currentSymbols": symbols, "validatedAt": "2026-10-15T00:00:01Z"}
            with mock.patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}), \
                    mock.patch.object(market.yahoo_restore, "resolve_revision", return_value="baseline"), \
                    mock.patch.object(market, "validate_current_membership", return_value=validation), \
                    mock.patch.object(market.CachedSecClient, "ticker_map", return_value=mapping), \
                    mock.patch.object(market, "reconcile_financial_index", return_value={"state": "unchanged", "validatedSymbols": []}), \
                    mock.patch.object(market, "eligible_queue", return_value=[]), \
                    mock.patch.object(market, "eligible_metadata_queue", return_value=[]):
                self.assertEqual(market.run_batch(resume_args), 0)
            recovered = json.loads(progress_path.read_text())
            self.assertEqual(recovered["status"], "ready")
            self.assertEqual(recovered["cohortSymbolCount"], 3000)
            self.assertEqual(recovered["snapshotSha256"], market.snapshot_hash(snapshot))


if __name__ == "__main__":
    unittest.main()
