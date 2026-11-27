import datetime as dt
import email.utils
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

import requests

spec = importlib.util.spec_from_file_location("conservative_pe", Path(__file__).resolve().parents[1] / "scripts/build_conservative_pe.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = dt.datetime(2026, 11, 27, tzinfo=dt.timezone.utc)
BASELINE = dict(eps=10, basis="annual", end="2025-12-31", annualEnd="2025-12-31",
                currency="USD", shareBasis="us-reported", adrRatio=None)
NEWS = [{"url": "https://news.google.com/rss/articles/a", "date": "2026-11-25", "title": "Test stock news"}]
SEGMENTS = dict(url="https://www.sec.gov/Archives/edgar/test.htm", date="2026-02-05",
                periodEnd="2025-12-31", facts={"Segment operating income": 0.4})
ANSWER = dict(concern="風險", evidence="假設", affectedEarningsRatio=0.3, confidence="high",
              news=[{key: NEWS[0][key] for key in ("url", "date")}], segmentEvidence="Segment operating income")
PUBLIC = dict(symbol="TEST", market="US", companyName="Test Inc.", baseline=BASELINE)


def response(code=200, payload=None, headers=None):
    return Mock(status_code=code, headers=headers or {}, **{"json.return_value": payload})


def model_answer(provider, raw):
    if provider == "gemini":
        return response(payload={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": raw}]}}]})
    return response(payload={"choices": [{"finish_reason": "stop", "message": {"content": raw}}]})


class ScenarioTests(unittest.TestCase):
    def test_refresh_is_off_without_both_workflow_opt_ins(self):
        for flags in ({}, {"CONSERVATIVE_PE_AI_ENABLED": "1"}, {"CONSERVATIVE_PE_AI_FREE_TIER": "1"}):
            with (self.subTest(flags=flags), patch.dict(os.environ, flags, clear=True),
                  patch.object(sys, "argv", ["build_conservative_pe.py", "--selector", "unused"]),
                  patch.object(m.requests, "Session") as session, self.assertRaises(m.Invalid)):
                m.main()
            session.assert_not_called()

    def test_verified_citations_and_confidence(self):
        result = m.validate_answer(json.dumps(ANSWER), NEWS, SEGMENTS)
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["segment"]["maxRatio"], 0.4)
        self.assertEqual(result["affectedEarningsRatio"], 0.3)

    def test_missing_segment_suppresses_numeric_valuation(self):
        answer = dict(ANSWER, segmentEvidence=None)
        result = m.validate_answer(json.dumps(answer), NEWS, None)
        self.assertEqual(result["confidence"], "low")
        self.assertIsNone(result["affectedEarningsRatio"])
        self.assertIsNone(result["segment"])

    def test_hallucinated_unbounded_or_unverified_evidence_is_rejected(self):
        for changes in ({"affectedEarningsRatio": True}, {"affectedEarningsRatio": 0.41},
                        {"news": [{"url": "https://invented.example/", "date": "2026-11-25"}]},
                        {"segmentEvidence": "invented"}, {"evidence": "https://invented.example/"}):
            with self.subTest(changes=changes), self.assertRaises(m.Invalid):
                m.validate_answer(json.dumps(dict(ANSWER, **changes)), NEWS, SEGMENTS)

    def test_fallback_only_for_statuses_or_connection_failures_and_only_once(self):
        valid = model_answer("groq", json.dumps(ANSWER))
        for first in (response(429), response(503), requests.Timeout(), requests.ConnectionError()):
            with self.subTest(first=first):
                session = Mock()
                providers = m.Providers(session, {"GEMINI_API_KEY": "fake", "GROQ_API_KEY": "fake"}, sleep=Mock())
                session.post.side_effect = [first, valid]
                self.assertEqual(providers.analyze(PUBLIC, NEWS, SEGMENTS)["model"], "qwen/qwen3.8-27b")
                primary, fallback = session.post.call_args_list
                self.assertIn("/gemini-3.8-flash:", primary.args[0])
                self.assertEqual(primary.kwargs["json"]["generationConfig"]["responseMimeType"], "application/json")
                self.assertEqual(fallback.kwargs["json"]["response_format"], {"type": "json_object"})
                session.post.reset_mock()
                session.post.side_effect = [first, response(503), valid]
                with self.assertRaises(m.Transient):
                    providers.analyze(PUBLIC, NEWS, SEGMENTS)
                self.assertEqual(session.post.call_count, 2)

    def test_invalid_json_sources_and_other_http_errors_never_fallback(self):
        invented = json.dumps(dict(ANSWER, segmentEvidence="fabricated"))
        for first in (response(400), response(401), response(404), response(500), response(502),
                      model_answer("gemini", "not JSON"), model_answer("gemini", invented)):
            with self.subTest(first=first):
                session = Mock()
                session.post.side_effect = [first]
                providers = m.Providers(session, {"GEMINI_API_KEY": "fake", "GROQ_API_KEY": "fake"}, sleep=Mock())
                with self.assertRaises(m.Invalid):
                    providers.analyze(PUBLIC, NEWS, SEGMENTS)
                session.post.assert_called_once()

    def test_retry_after_supports_seconds_and_http_date_per_provider(self):
        session, sleep = Mock(), Mock()
        providers = m.Providers(session, {"GEMINI_API_KEY": "fake"}, sleep=sleep, clock=lambda: 0)
        session.post.side_effect = [response(429, headers={"Retry-After": "45"}), model_answer("gemini", "{}")]
        with self.assertRaises(m.Transient):
            providers.call("gemini", "public")
        self.assertEqual(providers.next["gemini"], 45)
        providers.call("gemini", "public")
        sleep.assert_called_with(45)
        retry_at = email.utils.format_datetime(NOW + dt.timedelta(seconds=75))
        self.assertEqual(m.retry_seconds(retry_at, NOW), 75)
        session.post.side_effect = [response(429, headers={"Retry-After": "3600"})]
        with self.assertRaises(m.Transient):
            providers.call("gemini", "public")
        with self.assertRaises(m.Invalid):
            providers.call("gemini", "public")
        self.assertEqual(session.post.call_count, 3)

    def test_only_public_fields_enter_the_prompt(self):
        session = Mock()
        session.post.return_value = model_answer("gemini", json.dumps(ANSWER))
        providers = m.Providers(session, {"GEMINI_API_KEY": "test-secret"}, sleep=Mock())
        public = dict(PUBLIC, account="PRIVATE", quantity="PRIVATE", list="PRIVATE", baseline=dict(BASELINE, averageCost="PRIVATE"))
        providers.analyze(public, NEWS, SEGMENTS)
        message = session.post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("PRIVATE", message)
        self.assertNotIn("test-secret", message)
        self.assertIn('"eps": 10', message)

    def test_seven_day_cache_and_calculated_eps(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TEST.json"
            answer = m.validate_answer(json.dumps(ANSWER), NEWS, SEGMENTS)
            m.write_report(path, PUBLIC, dict(answer, provider="gemini", model=m.GEMINI_MODEL), NOW)
            self.assertEqual(json.loads(path.read_text())["scenarioEps"], 7)
            self.assertTrue(m.recent(path, NOW + dt.timedelta(days=6), BASELINE))
            self.assertFalse(m.recent(path, NOW + dt.timedelta(days=6), dict(BASELINE, eps=11)))
            self.assertFalse(m.recent(path, NOW + dt.timedelta(days=7), BASELINE))
            self.assertFalse(m.recent(path, NOW - dt.timedelta(days=1), BASELINE))

    def test_weekly_budget_survives_reruns_and_rotates_failed_or_uncached_symbols(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            state_file = output / "budget.json"
            symbols = [f"S{i:02}" for i in range(27)]
            selected = {symbol: dict(PUBLIC, symbol=symbol) for symbol in symbols}
            state = m.budget_state(state_file, NOW)
            self.assertEqual([r["symbol"] for r in m.plan_reports(symbols, selected, state, 20, output, NOW)], symbols[:20])
            m.write_json(state_file, state)
            self.assertEqual(m.plan_reports(symbols, selected, m.budget_state(state_file, NOW), 20, output, NOW), [])
            next_week = NOW + dt.timedelta(days=7)
            following = m.budget_state(state_file, next_week)
            self.assertEqual([r["symbol"] for r in m.plan_reports(symbols, selected, following, 10, output, next_week)],
                             symbols[20:] + symbols[:3])
            self.assertEqual(following["used"], 10)
            m.write_json(state_file, dict(following, used=-1))
            with self.assertRaises(m.Invalid):
                m.budget_state(state_file, next_week)

    def test_partial_failure_preserves_valid_output(self):
        selection, sec, providers = Mock(), Mock(), Mock()
        sec.company_name.return_value = PUBLIC["companyName"]
        sec.segments.return_value = SEGMENTS
        good = dict(m.validate_answer(json.dumps(ANSWER), NEWS, SEGMENTS), provider="gemini", model=m.GEMINI_MODEL)
        providers.analyze.side_effect = [m.Invalid(), good]
        with TemporaryDirectory() as directory, patch.object(m, "now", return_value=NOW), patch.object(m, "news_items", return_value=NEWS):
            output = Path(directory)
            self.assertEqual(m.refresh_reports([PUBLIC, dict(PUBLIC, symbol="OTHER")], output, m.iso_week(NOW),
                                               Mock(), selection, sec, providers), (2, 1, 1))
            self.assertEqual([p.name for p in output.iterdir()], ["OTHER.json"])
            self.assertEqual(selection.require_cloud.call_count, 3)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.owner = "U" + "a"*32
        self.env = {"SUPABASE_URL": "https://example.com", "SUPABASE_USER_ID": self.owner,
                    "ADMIN_LINE_USER_ID": self.owner, "SUPABASE_SERVICE_ROLE_KEY": "fake"}

    def test_owner_scoped_minimal_projections_and_explicit_cloud_permission(self):
        session = Mock()
        session.post.return_value = response(payload=self.owner)
        grant = response(payload=[dict(line_user_id=self.owner, storage_mode="cloud")])
        session.get.side_effect = [grant, response(payload=[dict(line_user_id=self.owner, symbol="aapl")]),
                                   response(payload=[dict(line_user_id=self.owner, kind="manual", symbols=["2330.TW"]),
                                                     dict(line_user_id=self.owner, kind="rules", members=[dict(symbol="MSFT", name="x")])]),
                                   grant]
        selection = m.AccountSelection(self.env, session)
        self.assertEqual(selection.symbols(), ["2330.TW", "AAPL", "MSFT"])
        queries = [entry.kwargs["params"] for entry in session.get.call_args_list]
        self.assertTrue(all(item["line_user_id"] == "eq." + self.owner for item in queries))
        self.assertEqual(queries[1]["select"], "line_user_id,symbol")
        self.assertIn("definition->history->-1->members", queries[2]["select"])
        self.assertTrue(session.post.call_args.args[0].endswith("/rpc/atlas_storage_owner_id"))
        session.get.side_effect = [response(payload=[dict(line_user_id=self.owner, storage_mode="device")])]
        with self.assertRaises(m.Invalid):
            selection.symbols()

    def test_mismatched_config_and_foreign_row_fail_closed(self):
        session = Mock()
        other = "U" + "b"*32
        with self.assertRaises(m.Invalid):
            m.AccountSelection(dict(self.env, ADMIN_LINE_USER_ID=other), session)
        session.get.assert_not_called()
        selection = m.AccountSelection(self.env, session)
        session.get.return_value = response(payload=[dict(line_user_id=other, storage_mode="cloud")])
        session.post.return_value = response(payload=other)
        with self.assertRaises(m.Invalid):
            selection.symbols()
        session.get.assert_not_called()
        session.post.return_value = response(payload=self.owner)
        with self.assertRaises(m.Invalid):
            selection.symbols()
        session.get.assert_called_once()

    def test_public_allowlist_and_w6_baseline_contract(self):
        self.assertEqual(m.public_symbols("aapl, 2330.tw BRK.B\n"), ["2330.TW", "AAPL", "BRK-B"])
        for value in ("", "INVALID/../FILE"):
            with self.assertRaises(m.Invalid):
                m.public_symbols(value)
        self.assertEqual(m.validate_selection([PUBLIC], ["TEST"], NOW), {"TEST": PUBLIC})
        for row in (dict(PUBLIC, symbol="PRIVATE"), dict(PUBLIC, baseline=dict(BASELINE, shareBasis="adr")),
                    dict(PUBLIC, baseline=dict(BASELINE, currency="TWD")),
                    dict(PUBLIC, baseline=dict(BASELINE, annualEnd="2020-12-31"))):
            with self.subTest(row=row), self.assertRaises(m.Invalid):
                m.validate_selection([row], ["TEST"], NOW)

    def test_relevant_company_finance_news_and_dates(self):
        rss = "<rss><channel>"
        for i, (title, published) in enumerate([
            ("Test stock earnings - Publisher", "Wed, 25 Nov 2026 12:00:00 GMT"),
            ("Testimony stock earnings", "Wed, 25 Nov 2026 12:00:00 GMT"),
            ("Test football match", "Wed, 25 Nov 2026 12:00:00 GMT"),
            ("Another stock - Test", "Wed, 25 Nov 2026 12:00:00 GMT"),
            ("Test stock earnings", "Wed, 25 Nov 2025 12:00:00 GMT"),
            ("Test earnings", "bad date"),
            ("Five stocks to watch: Other, Test, and more", "Wed, 25 Nov 2026 12:00:00 GMT"),
        ]):
            rss += f"<item><title>{escape(title)}</title><link>https://news.google.com/rss/articles/{i}</link><pubDate>{published}</pubDate><source>{'Test' if i == 3 else 'Publisher'}</source></item>"
        session = Mock()
        session.get.return_value = Mock(status_code=200, content=(rss + "</channel></rss>").encode())
        result = m.news_items(session, "TS", "US", "Test Inc.", NOW.date())
        self.assertEqual([item["title"] for item in result], ["Test stock earnings"])
        self.assertIn('"Test" (stock OR earnings OR TS)', session.get.call_args.kwargs["params"]["q"])

    def test_sec_segment_parser_uses_operating_income_for_exact_period_currency(self):
        def context(id, member="", end="2025-12-31"):
            tag = f'<xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">{member}</xbrldi:explicitMember>' if member else ""
            return f'<xbrli:context id="{id}"><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>{tag}</xbrli:context>'

        def fact(id, value, name="OperatingIncomeLoss", unit="u"):
            return f'<ix:nonFraction name="us-gaap:{name}" contextRef="{id}" unitRef="{unit}" scale="6">{value}</ix:nonFraction>'

        source = ('<html><xbrli:unit id="u"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
                  + context("total") + context("s1", "test:CloudMember") + context("s2", "test:SalesMember")
                  + context("s3", "test:LegacyMember", end="2025-09-30") + context("s4", "test:ForeignMember")
                  + fact("total", 100) + fact("s1", 40) + fact("s2", 80, "Revenues") + fact("s3", 22) + fact("s4", 10, unit="wrong") + '</html>')
        facts = m.operating_segments(source.encode(), "2025-12-31", "USD")
        self.assertEqual(facts, {"Cloud: 40,000,000 USD operating income; consolidated operating income 100,000,000 USD": 0.4})
        self.assertEqual(m.operating_segments(source.encode(), "2025-12-31", "TWD"), {})

    def test_sec_uses_the_actual_report_date_of_a_month_end_normalized_fiscal_year(self):
        sec = m.SecSegments(Mock(), "test contact")
        sec.get = Mock(side_effect=[
            response(payload={"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}}),
            response(payload={"filings": {"recent": {"form": ["10-K", "10-K"],
                "reportDate": ["2026-06-30", "2025-09-27"],
                "accessionNumber": ["0000320193-26-000026", "0000320193-25-000026"],
                "primaryDocument": ["aapl-20260630.htm", "aapl-20250927.htm"],
                "filingDate": ["2026-08-26", "2025-10-05"]}}}),
            Mock(content=b"official filing"),
        ])
        baseline = dict(BASELINE, end="2025-09-30", annualEnd="2025-09-30")
        with patch.object(m, "operating_segments", return_value=SEGMENTS["facts"]) as operating:
            result = sec.segments(dict(PUBLIC, symbol="AAPL", baseline=baseline), NOW.date())
            self.assertEqual(result["periodEnd"], "2025-09-27")
            operating.assert_called_once_with(b"official filing", "2025-09-27", "USD")


if __name__ == "__main__":
    unittest.main()
