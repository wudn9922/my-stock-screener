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


def configured(*ids):
    rows = {row["id"]: row for row in m.load_providers()}
    return [rows[key] for key in ids]


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

    def provider(self, session, env=None, state=None, publish=None, config=None):
        return m.Providers(session, env if env is not None else {
            "GEMINI_API_KEY": "fake", "GROQ_API_KEY": "fake",
            "CLOUDFLARE_API_TOKEN": "fake", "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        }, state=state, publish=publish, config=config, sleep=Mock(),
            clock=lambda: 0, current=lambda: NOW)

    def test_order_long_retry_after_and_separate_gemini_quotas(self):
        session = Mock()
        session.post.side_effect = [
            response(429, headers={"Retry-After": "3600"}),
            model_answer("gemini", json.dumps(ANSWER)),
            response(429), model_answer("groq", json.dumps(ANSWER)),
            model_answer("groq", json.dumps(ANSWER)),
        ]
        # Explicit order: this test covers fallback and separate Gemini quotas, not production order.
        provider = self.provider(session, config=configured("gemini-hydrogen", "gemini-flash", "groq-qwen"))
        self.assertEqual(provider.analyze(PUBLIC, NEWS, SEGMENTS)["provider"], "gemini-flash")
        self.assertEqual(provider.analyze(PUBLIC, NEWS, SEGMENTS)["provider"], "groq-qwen")
        self.assertEqual(provider.analyze(PUBLIC, NEWS, SEGMENTS)["provider"], "groq-qwen")
        self.assertEqual(provider.used, {"gemini-hydrogen": 1, "gemini-flash": 2, "groq-qwen": 2})
        self.assertEqual(provider.disabled, {"gemini-hydrogen", "gemini-flash"})
        self.assertIn("/gemini-4-hydrogen:", session.post.call_args_list[0].args[0])
        self.assertIn("/gemini-3.8-flash:", session.post.call_args_list[1].args[0])
        self.assertEqual(session.post.call_args_list[3].kwargs["json"]["model"], "qwen/qwen3.8-27b")

    def test_missing_secrets_quota_and_unknown_model(self):
        session = Mock()
        with self.assertRaises(m.Invalid):
            self.provider(session, {})
        with self.assertRaises(m.Invalid):
            self.provider(session, {"CLOUDFLARE_API_TOKEN": "fake"})
        provider = self.provider(session, {"GROQ_API_KEY": "fake", "GROQ_MODEL": "unreviewed"})
        self.assertEqual(list(provider.rows), ["groq-qwen"])
        with self.assertRaises(m.Invalid):
            provider.call("unreviewed", "public")
        session.post.assert_not_called()
        row = provider.rows["groq-qwen"]
        provider.state["providers"]["groq-qwen"] = row["daily_limit"]
        with self.assertRaises(m.Exhausted):
            provider.analyze(PUBLIC, NEWS, SEGMENTS)
        session.post.assert_not_called()

    def test_quota_switch_and_persist_before_post_including_failures(self):
        session, publish = Mock(), Mock()
        state = m.empty_budget(NOW)
        provider = self.provider(session, state=state, publish=publish)
        state["providers"]["gemini-hydrogen"] = provider.rows["gemini-hydrogen"]["daily_limit"]
        state["providers"]["gemini-flash"] = provider.rows["gemini-flash"]["daily_limit"]

        def post(*args, **kwargs):
            self.assertGreater(state["providers"]["groq-qwen"], 0)
            publish.assert_called_once_with(state)
            return response(429, headers={"Retry-After": "3600"})
        session.post.side_effect = [response(429), model_answer("groq", json.dumps(ANSWER))]
        def dispatch(*args, **kwargs):
            if "groq.com" in args[0]:
                return post(*args, **kwargs)
            self.assertEqual(state["providers"]["cloudflare-qwen"], 1)
            self.assertEqual(publish.call_count, 2)
            self.assertIn("/accounts/" + "a" * 32 + "/ai/v1/chat/completions", args[0])
            return model_answer("groq", json.dumps(ANSWER))
        session.post.side_effect = dispatch
        self.assertEqual(provider.analyze(PUBLIC, NEWS, SEGMENTS)["provider"], "cloudflare-qwen")
        self.assertEqual(state["providers"]["groq-qwen"], 1)

    def test_invalid_answer_never_reasks_and_thought_is_excluded(self):
        for raw in ("not JSON", json.dumps(dict(ANSWER, segmentEvidence="fabricated"))):
            session = Mock()
            session.post.return_value = model_answer("gemini", raw)
            with self.assertRaises(m.Invalid):
                self.provider(session).analyze(PUBLIC, NEWS, SEGMENTS)
            session.post.assert_called_once()
        session = Mock()
        session.post.return_value = response(payload={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": "private reasoning", "thought": True}, {"text": json.dumps(ANSWER)}]}}]})
        result = self.provider(session).analyze(PUBLIC, NEWS, SEGMENTS)
        self.assertEqual(result["affectedEarningsRatio"], 0.3)
        self.assertNotIn("private reasoning", json.dumps(result))
        self.assertEqual(session.post.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"]["includeThoughts"], False)

    def test_only_public_fields_enter_the_prompt(self):
        session = Mock()
        session.post.return_value = model_answer("gemini", json.dumps(ANSWER))
        provider = self.provider(session, {"GEMINI_API_KEY": "test-secret"})
        public = dict(PUBLIC, account="PRIVATE", quantity="PRIVATE", list="PRIVATE", baseline=dict(BASELINE, averageCost="PRIVATE"))
        provider.analyze(public, NEWS, SEGMENTS)
        message = session.post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("PRIVATE", message)
        self.assertNotIn("test-secret", message)
        self.assertIn('"eps": 10', message)
        self.assertLessEqual(len(message.encode()), m.MAX_PROMPT_BYTES)

    def test_fourteen_day_cache_and_calculated_eps(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TEST.json"
            answer = m.validate_answer(json.dumps(ANSWER), NEWS, SEGMENTS)
            m.write_report(path, PUBLIC, dict(answer, provider="gemini-hydrogen", model="gemini-4-hydrogen"), NOW)
            self.assertEqual(json.loads(path.read_text())["scenarioEps"], 7)
            self.assertTrue(m.recent(path, NOW + dt.timedelta(days=13), BASELINE))
            self.assertFalse(m.recent(path, NOW + dt.timedelta(days=13), dict(BASELINE, eps=11)))
            self.assertFalse(m.recent(path, NOW + dt.timedelta(days=14), BASELINE))
            self.assertFalse(m.recent(path, NOW - dt.timedelta(days=1), BASELINE))

    def test_budget_reset_migration_and_pacific_window(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            m.write_json(path, {"week": NOW.strftime("%G-W%V"), "used": 12, "cursor": 42})
            state = m.budget_state(path, NOW)
            self.assertEqual(state["used"], 12)
            self.assertEqual(state["providers"]["gemini-flash"], 12)
            m.write_json(path, state)
            self.assertEqual(m.budget_state(path, NOW), state)
            next_day = NOW + dt.timedelta(days=1)
            self.assertEqual(m.budget_state(path, next_day)["used"], 0)
            state = m.empty_budget(NOW + dt.timedelta(hours=23))
            row = m.load_providers()[0]
            m.reserve_provider(state, row, NOW + dt.timedelta(hours=23), "public", Mock())
            m.write_json(path, state)
            next_state = m.budget_state(path, next_day)
            self.assertEqual(next_state["providers"], {})
            self.assertEqual(m.provider_usage(next_state, row, next_day)["used"], 1)
            m.write_json(path, dict(next_state, used=-1))
            with self.assertRaises(m.Invalid):
                m.budget_state(path, next_day)
        self.assertEqual(m.analysis_limit({"CONSERVATIVE_PE_AI_LIMIT": "99"}), m.DAILY_LIMIT)
        self.assertEqual(m.analysis_limit({"CONSERVATIVE_PE_AI_LIMIT": "12"}), 12)

    def test_priorities_and_no_report_rotation(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            selected = {symbol: dict(PUBLIC, symbol=symbol) for symbol in ("NEW", "CHANGE", "OLD", "LESSOLD", "FRESH")}
            answer = dict(m.validate_answer(json.dumps(ANSWER), NEWS, SEGMENTS), provider="gemini", model="gemini-3.8-flash")
            for symbol, age in (("CHANGE", 1), ("OLD", 25), ("LESSOLD", 18), ("FRESH", 13)):
                public = selected[symbol]
                if symbol == "CHANGE":
                    public = dict(public, baseline=dict(BASELINE, eps=12))
                m.write_report(output / (symbol + ".json"), public, answer, NOW - dt.timedelta(days=age))
            state = m.empty_budget(NOW)
            self.assertEqual([p["symbol"] for p in m.plan_reports(selected, state, 30, output, NOW)],
                             ["NEW", "CHANGE", "OLD", "LESSOLD"])
            state["attempted"]["NEW"] = state["day"]
            state["used"] = 29
            self.assertEqual([p["symbol"] for p in m.plan_reports(selected, state, 30, output, NOW)], ["CHANGE"])
            state["used"] = 30
            self.assertEqual(m.plan_reports(selected, state, 100, output, NOW), [])

    def test_partial_failure_no_news_and_exhaustion(self):
        sec, providers, publish = Mock(), Mock(), Mock()
        sec.company_name.return_value = PUBLIC["companyName"]
        sec.segments.return_value = SEGMENTS
        providers.available.return_value = ["gemini-hydrogen"]
        good = dict(m.validate_answer(json.dumps(ANSWER), NEWS, SEGMENTS), provider="gemini-hydrogen", model="gemini-4-hydrogen")
        providers.analyze.side_effect = [m.Invalid(), good, m.Exhausted()]
        chosen = [dict(PUBLIC, symbol=s) for s in ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH")]
        state = m.empty_budget(NOW)
        with TemporaryDirectory() as directory, patch.object(m, "news_items", side_effect=[[], NEWS, NEWS, NEWS, NEWS]):
            output = Path(directory)
            counts = m.refresh_reports(chosen, output, state, Mock(), sec, providers, publish, current=lambda: NOW)
            self.assertEqual(counts, dict(attempted=4, written=1, skipped=1, failed=2))
            self.assertEqual([p.name for p in output.iterdir()], ["THIRD.json"])
            self.assertEqual(publish.call_count, 4)
            self.assertEqual(state["used"], 4)
        self.assertTrue(m.exceeds_failure_threshold(counts))
        self.assertFalse(m.exceeds_failure_threshold({"attempted": 10, "failed": 3}))
        self.assertTrue(m.exceeds_failure_threshold({"attempted": 10, "failed": 4}))

    def test_probe_uses_identical_prompt_and_no_publisher(self):
        session = Mock()
        session.post.side_effect = [model_answer("gemini", json.dumps(ANSWER)),
                                    model_answer("gemini", json.dumps(ANSWER)),
                                    model_answer("groq", json.dumps(ANSWER)),
                                    model_answer("groq", json.dumps(ANSWER))]
        provider = self.provider(session)
        sec = Mock()
        sec.company_name.return_value = PUBLIC["companyName"]
        sec.segments.return_value = SEGMENTS
        with patch.object(m, "news_items", return_value=NEWS), patch.object(m, "write_json") as write, patch.object(m, "publish_budget") as publish:
            result = m.probe({"TEST": PUBLIC}, Mock(), sec, provider, current=lambda: NOW)
        write.assert_not_called()
        publish.assert_not_called()
        self.assertIn("0.3 / medium", result)
        self.assertEqual(session.post.call_count, 4)
        bodies = [c.kwargs["json"] for c in session.post.call_args_list]
        prompts = [b["contents"][0]["parts"][0]["text"] if "contents" in b else b["messages"][0]["content"] for b in bodies]
        self.assertEqual(len(set(prompts)), 1)
        self.assertEqual(sum(provider.used.values()), 4)

    def test_probe_symbols_are_balanced_sorted_and_backfilled(self):
        def selected(us, tw):
            return {s: dict(PUBLIC, symbol=s, market=market)
                    for market, symbols in (("US", us), ("TW", tw)) for s in symbols}

        us = ["E", "C", "A", "F", "B", "D"]
        tw = ["6666.TWO", "2222.TW", "4444.TW", "1111.TW", "3333.TW", "5555.TW"]
        for a, b, expected in (
            (us, tw, ["A", "B", "C", "1111.TW", "2222.TW"]),
            (us, [], ["A", "B", "C", "D", "E"]),
            ([], tw, ["1111.TW", "2222.TW", "3333.TW", "4444.TW", "5555.TW"]),
            (["E"], tw, ["E", "1111.TW", "2222.TW", "3333.TW", "4444.TW"]),
            (us, ["6666.TWO"], ["A", "B", "C", "D", "6666.TWO"]),
            (["A"], ["1111.TW"], ["A", "1111.TW"]),
            ([], [], []),
        ):
            with self.subTest(us=a, tw=b):
                self.assertEqual(m.probe_symbols(selected(a, b)), expected)

    def test_all_provider_failure_codes_in_both_modes_and_safe_summary(self):
        secret = "FAKE-SECRET-DO-NOT-LOG"
        private = "PRIVATE-RESPONSE-DO-NOT-LOG"
        prompt_marker = "PRIVATE-PROMPT-DO-NOT-LOG"
        header_marker = "PRIVATE-HEADER-DO-NOT-LOG"
        public = dict(PUBLIC, companyName=prompt_marker)
        cases = [
            ("gemini", response(429, payload={"error": private}, headers={"X-Test": header_marker}), "http-429"),
            ("gemini", response(503, payload={"error": private}), "http-503"),
            ("gemini", requests.Timeout(secret), "timeout"),
            ("gemini", requests.ConnectionError(secret), "connection"),
            ("gemini", response(payload={"candidates": [{"finishReason": "MAX_TOKENS"}]}), "incomplete:MAX_TOKENS"),
            ("groq", response(payload={"choices": [{"finish_reason": "length"}]}), "incomplete:length"),
            ("groq", response(payload={"choices": [{"finish_reason": private}]}), "incomplete:unknown"),
            ("groq", response(payload={"choices": [{}]}), "incomplete:missing"),
            ("gemini", response(payload={"error": private}), "invalid-json"),
            ("gemini", Mock(status_code=200, **{"json.side_effect": ValueError(private)}), "invalid-json"),
            ("gemini", model_answer("gemini", private), "invalid-json"),
            ("gemini", model_answer("gemini", "[]"), "validation:schema"),
        ]
        for change, reason in (
            ({"concern": private + " https://example.com"}, "concern-text"),
            ({"evidence": None}, "evidence-text"),
            ({"affectedEarningsRatio": True}, "ratio"),
            ({"confidence": private}, "confidence"),
            ({"news": []}, "news-count"),
            ({"news": [{"url": private}]}, "citation-schema"),
            ({"news": [{"url": "https://example.com", "date": NEWS[0]["date"]}]}, "citation-not-in-input"),
            ({"segmentEvidence": private}, "segment-not-in-input"),
            ({"affectedEarningsRatio": 0.41}, "ratio-exceeds-segment"),
        ):
            cases.append(("groq", model_answer("groq", json.dumps(dict(ANSWER, **change))), "validation:" + reason))
        for family, outcome, reason in cases:
            for mode in ("probe", "normal"):
                with self.subTest(family=family, reason=reason, mode=mode):
                    session = Mock()
                    attempts = 3 if reason in ("http-503", "timeout", "connection") else 1
                    session.post.side_effect = [outcome] * attempts
                    config = [row for row in m.load_providers() if row["id"] ==
                              ("gemini-hydrogen" if family == "gemini" else "groq-qwen")]
                    provider = self.provider(session, {config[0]["secret"]: secret}, config=config)
                    key = config[0]["id"]
                    with patch.object(m, "evidence_for", return_value=(public, NEWS, SEGMENTS)):
                        if mode == "probe":
                            content = m.probe({"TEST": public}, Mock(), Mock(), provider, current=lambda: NOW)
                            self.assertIn(f"| TEST | {reason} |", content)
                        else:
                            with self.assertRaises((m.Invalid, m.Transient)):
                                provider.analyze(public, NEWS, SEGMENTS)
                            content = m.provider_failure_summary(provider)
                            self.assertIn(f"| {key} | {reason} | {attempts} |", content)
                    self.assertEqual(provider.failures, {key: {reason: attempts}})
                    self.assertEqual(session.post.call_count, attempts)
                    self.assertEqual(provider.retries.get(key, 0), attempts - 1)
                    self.assertEqual(bool(provider.disabled), reason.startswith("http-") or reason in ("timeout", "connection"))
                    with TemporaryDirectory() as directory:
                        path = Path(directory) / "summary"
                        m.summary({"GITHUB_STEP_SUMMARY": str(path)}, content)
                        saved = path.read_text(encoding="utf-8")
                    for forbidden in (secret, private, prompt_marker, header_marker, "Authorization", "x-goog-api-key"):
                        self.assertNotIn(forbidden, saved)

    def test_probe_disabled_cells_do_not_count_additional_requests(self):
        session = Mock()
        session.post.return_value = response(429)
        provider = self.provider(session, {"GROQ_API_KEY": "fake"})
        with patch.object(m, "evidence_for", return_value=(PUBLIC, NEWS, SEGMENTS)):
            content = m.probe({s: dict(PUBLIC, symbol=s) for s in ("AAA", "BBB")}, Mock(), Mock(), provider,
                              current=lambda: NOW)
        self.assertIn("| AAA | http-429 |", content)
        self.assertIn("| BBB | disabled |", content)
        self.assertEqual(provider.failures, {"groq-qwen": {"http-429": 1}})
        session.post.assert_called_once()

    def test_normal_main_summary_counts_fallback_and_repeated_invalid_answers(self):
        session = Mock()
        session.post.side_effect = [
            response(503), response(429), model_answer("gemini", "not JSON"), model_answer("gemini", "not JSON"),
        ]
        stamp = m.now()
        state = m.empty_budget(stamp)
        provider = m.Providers(session, {"GEMINI_API_KEY": "fake"}, state=state,
                               sleep=Mock(), clock=lambda: 0, current=lambda: stamp)
        selected = [dict(PUBLIC, symbol=s) for s in ("AAA", "BBB")]
        with TemporaryDirectory() as directory:
            output = Path(directory) / "reports"
            path = Path(directory) / "summary"
            env = {"CONSERVATIVE_PE_AI_ENABLED": "1", "CONSERVATIVE_PE_AI_FREE_TIER": "1",
                   "GITHUB_STEP_SUMMARY": str(path)}
            with (patch.dict(os.environ, env, clear=True),
                  patch.object(sys, "argv", ["build_conservative_pe.py", "--selector", "unused"]),
                  patch.object(m, "public_symbols", return_value=["AAA", "BBB"]),
                  patch.object(m.subprocess, "run", return_value=Mock(stdout=json.dumps(selected))),
                  patch.object(m, "budget_state", return_value=state),
                  patch.object(m.requests, "Session", return_value=session),
                  patch.object(m, "Providers", return_value=provider),
                  patch.object(m, "publish_budget"), patch.object(m, "OUTPUT", output),
                  patch.object(m, "evidence_for", return_value=(PUBLIC, NEWS, SEGMENTS))):
                m.main()
            content = path.read_text(encoding="utf-8")
        self.assertIn("| gemini-hydrogen | http-429 | 1 |", content)
        self.assertIn("| gemini-hydrogen | http-503 | 1 |", content)
        self.assertIn("| gemini-hydrogen | 2 | 1 | 2 |", content)
        self.assertIn("| gemini-flash | invalid-json | 2 |", content)
        self.assertNotIn("not JSON", content)
        self.assertEqual(session.post.call_count, 4)

    def test_retry_waits_reserve_before_each_post_and_probe_success(self):
        session, publish = Mock(), Mock()
        provider = self.provider(session, {"GEMINI_API_KEY": "fake"}, publish=publish)
        key = "gemini-hydrogen"
        outcomes = iter([response(503, headers={"Retry-After": "10"}),
                         response(502, headers={"Retry-After": "61"}),
                         model_answer("gemini", json.dumps(ANSWER))])

        def post(*args, **kwargs):
            self.assertEqual(publish.call_count, session.post.call_count)
            self.assertEqual(provider.state["providers"][key], session.post.call_count)
            self.assertEqual(kwargs["timeout"], 90)
            return next(outcomes)

        session.post.side_effect = post
        provider.rows = {key: provider.rows[key]}
        with patch.object(m, "evidence_for", return_value=(PUBLIC, NEWS, SEGMENTS)):
            content = m.probe({"TEST": PUBLIC}, Mock(), Mock(), provider, current=lambda: NOW)
        self.assertIn("重試 2 次後成功", content)
        self.assertIn(f"| {key} | 3 | 2 | 1 | 1 |", content)
        self.assertEqual(provider.failures, {key: {"http-503": 1, "http-502": 1}})
        # Retry-After 10 is subject to the provider's 15-second pacing; >60 uses backoff.
        self.assertEqual([c.args[0] for c in provider.sleep.call_args_list], [0, 15, 40])
        self.assertNotIn(key, provider.disabled)

    def test_retry_defaults_exhaustion_and_last_reason(self):
        for first in (response(500), response(503, headers={"Retry-After": "invalid"}),
                      requests.Timeout("secret"), requests.ConnectionError("secret")):
            session = Mock()
            session.post.side_effect = [first, response(504), response(503)]
            provider = self.provider(session, {"GEMINI_API_KEY": "fake"})
            key = "gemini-hydrogen"
            provider.rows = {key: provider.rows[key]}
            with patch.object(m, "evidence_for", return_value=(PUBLIC, NEWS, SEGMENTS)):
                content = m.probe({"TEST": PUBLIC}, Mock(), Mock(), provider, current=lambda: NOW)
            self.assertIn("| TEST | http-503 |", content)
            self.assertEqual([c.args[0] for c in provider.sleep.call_args_list], [0, 20, 40])
            self.assertEqual(provider.disabled, {key})
            self.assertEqual(provider.retries, {key: 2})
            self.assertEqual(session.post.call_count, 3)

    def test_retry_after_valid_date_and_immediate_4xx(self):
        session = Mock()
        header = email.utils.format_datetime(NOW + dt.timedelta(seconds=35))
        session.post.side_effect = [response(503, headers={"Retry-After": header}),
                                    response(401)]
        provider = self.provider(session)
        with self.assertRaisesRegex(m.ProviderTransient, "http-401"):
            provider.call("gemini-hydrogen", "public")
        self.assertEqual([c.args[0] for c in provider.sleep.call_args_list], [0, 35])
        self.assertEqual(provider.disabled, {"gemini-hydrogen"})
        self.assertEqual(session.post.call_count, 2)

    def test_retry_does_not_reserve_when_deadline_or_quota_insufficient(self):
        for limited in ("deadline", "quota"):
            session, publish = Mock(), Mock()
            provider = self.provider(session, {"GEMINI_API_KEY": "fake"}, publish=publish)
            key = "gemini-hydrogen"
            if limited == "deadline":
                provider.deadline = m.MIN_REMAINING + 10
            else:
                provider.rows[key] = dict(provider.rows[key], daily_limit=1)
            session.post.return_value = response(503)
            with self.assertRaisesRegex(m.ProviderTransient, "http-503"):
                provider.call(key, "public")
            session.post.assert_called_once()
            publish.assert_called_once()
            self.assertEqual(provider.used, {key: 1})
            self.assertEqual(provider.retries, {})
            self.assertNotIn(key, provider.disabled)

    def test_timeout_config_and_reasoning_output(self):
        config = m.load_providers()
        for row, timeout in zip(config, (90, 180, 180, 180)):
            session = Mock()
            raw = json.dumps(ANSWER)
            if row["type"] == "gemini":
                session.post.return_value = model_answer("gemini", raw)
            else:
                session.post.return_value = response(payload={"choices": [{"finish_reason": "stop",
                    "message": {"reasoning": "secret reasoning", "content": "<think>{bad}</think>" + raw}}]})
            provider = self.provider(session, config=[row])
            result = provider.analyze(PUBLIC, NEWS, SEGMENTS)
            self.assertEqual(result["affectedEarningsRatio"], 0.3)
            self.assertNotIn("secret reasoning", json.dumps(result))
            kwargs = session.post.call_args.kwargs
            self.assertEqual(kwargs["timeout"], timeout)
            if row["id"] == "groq-qwen":
                self.assertEqual(kwargs["json"]["reasoning_format"], "hidden")
                self.assertEqual(kwargs["json"]["reasoning_effort"], "low")
                self.assertEqual(kwargs["json"]["max_completion_tokens"], 4500)
            if row["id"] == "cloudflare-qwen":
                self.assertEqual(kwargs["json"]["reasoning_effort"], "medium")
                self.assertEqual(kwargs["json"]["max_tokens"], 4000)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "providers.json"
            for timeout in (9, 301, True, 10.5, "180"):
                m.write_json(path, [dict(config[0], timeout_seconds=timeout)])
                with self.assertRaises(m.Invalid):
                    m.load_providers(path)
            for timeout in (10, 300):
                m.write_json(path, [dict(config[0], timeout_seconds=timeout)])
                self.assertEqual(m.load_providers(path)[0]["timeout_seconds"], timeout)
            m.write_json(path, [dict(configured("cloudflare-qwen")[0], daily_limit=8)])
            with self.assertRaises(m.Invalid):
                m.load_providers(path)

    def test_groq_estimate_prompt_trimming_and_daily_budget(self):
        row = configured("groq-qwen")[0]
        self.assertEqual(m.request_tokens(row, "中"), 2 + 128 + 4500)
        self.assertEqual(m.request_tokens(row, "abc"), 2 + 128 + 4500)
        self.assertEqual(m.request_tokens(row, "a" * m.MAX_PROMPT_BYTES), 7728)
        self.assertEqual(row["daily_limit"], 25)
        # Force the TPM constraint to bind before the shared byte limit.
        tight = dict(row, max_output_tokens=6500)
        news = NEWS + [dict(NEWS[0], title="中" * 1000, url="https://example.com/extra")]
        shared, _ = m.build_prompt(PUBLIC, news, SEGMENTS)
        self.assertGreater(m.request_tokens(tight, shared), m.GROQ_MINUTE_TOKENS)
        prompt, sources = m.build_prompt(PUBLIC, news, SEGMENTS, tight)
        self.assertEqual(sources, NEWS)
        self.assertLessEqual(m.request_tokens(tight, prompt), m.GROQ_MINUTE_TOKENS)
        session = Mock()
        session.post.return_value = model_answer("groq", json.dumps(ANSWER))
        provider = self.provider(session, config=[tight])
        provider.analyze(PUBLIC, news, SEGMENTS)
        self.assertEqual(session.post.call_args.kwargs["json"]["messages"][0]["content"], prompt)
        usage = m.provider_usage(provider.state, tight, NOW)
        self.assertEqual(usage["tokens"], m.request_tokens(tight, prompt))
        provider.state["windows"][m.quota_key(tight)]["tokens"] = m.GROQ_DAILY_TOKENS - 1
        self.assertFalse(m.provider_available(provider.state, tight, NOW, prompt))
        with self.assertRaises(m.Exhausted):
            provider.call(tight["id"], prompt)
        session.post.assert_called_once()

    def test_config_validation_and_news_pacing(self):
        config = m.load_providers()
        self.assertEqual([r["id"] for r in config], ["gemini-hydrogen", "groq-qwen", "cloudflare-qwen", "gemini-flash"])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "providers.json"
            m.write_json(path, [dict(config[0], base_url="https://unreviewed.example")])
            with self.assertRaises(m.Invalid):
                m.load_providers(path)
        session, sleep = Mock(), Mock()
        pacer = m.NewsPacer(session, sleep=sleep, clock=lambda: 0)
        pacer.get("a")
        pacer.get("b")
        self.assertEqual(sleep.call_args_list[-1].args, (2,))

    def test_selector_cap_fails_before_any_write_or_http(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for symbol in ("TEST", "AAPL", "2330.TW"):
                (root / (symbol + ".json")).write_text("{}")
            (root / "not-json.csv").write_text("")
            with patch.object(m, "FINANCIALS", root), patch.object(m.subprocess, "run") as run, \
                    patch.object(m, "write_json") as write, patch.object(m.requests, "Session") as session, \
                    patch.dict(os.environ, {"CONSERVATIVE_PE_AI_ENABLED": "1", "CONSERVATIVE_PE_AI_FREE_TIER": "1"}, clear=True), \
                    patch.object(sys, "argv", ["builder", "--selector", "unused"]):
                run.return_value.stdout = json.dumps([PUBLIC] * 601)
                with self.assertRaises(m.Invalid):
                    m.main()
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), ["2330.TW", "AAPL", "TEST"])
                write.assert_not_called()
                session.assert_not_called()


class InputTests(unittest.TestCase):
    def test_public_selection_and_w6_baseline_contract(self):
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
