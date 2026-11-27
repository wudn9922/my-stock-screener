import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("conservative_pe", Path(__file__).resolve().parents[1] / "scripts/build_conservative_pe.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.news = [{"url": "https://news.example/a", "date": "2026-11-25", "title": "Public headline"}]
        self.answer = dict(concern="風險", evidence="假設", affectedEarningsRatio=0.3, confidence="high",
                           news=[{"url": self.news[0]["url"], "date": self.news[0]["date"]}], segmentEvidence=None)
        self.public = {"symbol": "TEST", "market": "US", "baseline": {"eps": 10, "basis": "annual", "end": "2025-12-31"}}

    def test_citations_and_low_confidence(self):
        result = m.validate_answer(json.dumps(self.answer), self.news, None)
        self.assertEqual(result["confidence"], "low")
        for patch in ({"affectedEarningsRatio": True}, {"affectedEarningsRatio": 1.1},
                      {"news": [{"url": "https://invented.example/", "date": "2026-11-25"}]},
                      {"segmentEvidence": "invented"}):
            with self.assertRaises(m.Invalid):
                m.validate_answer(json.dumps(dict(self.answer, **patch)), self.news, None)

    def test_fallback_only_transient(self):
        providers = m.Providers(Mock(), {"GEMINI_API_KEY": "fake"}, sleep=lambda _: None)
        providers.call = Mock(side_effect=[m.Transient(), (json.dumps(self.answer), "qwen-test")])
        self.assertEqual(providers.analyze(self.public, self.news, None)["provider"], "groq")
        self.assertEqual(providers.call.call_args_list[0].args[1], providers.call.call_args_list[1].args[1])
        providers.call = Mock(return_value=("not-json", "gemini-test"))
        with self.assertRaises(m.Invalid):
            providers.analyze(self.public, self.news, None)
        self.assertEqual(providers.call.call_count, 1)

    def test_http_status(self):
        session = Mock()
        providers = m.Providers(session, {"GEMINI_API_KEY": "fake"}, sleep=lambda _: None, clock=lambda: 0)
        session.post.return_value = Mock(status_code=429, headers={"Retry-After": "45"})
        with self.assertRaises(m.Transient):
            providers.call("gemini", "public")
        self.assertEqual(providers.next["gemini"], 45)
        session.post.return_value = Mock(status_code=401)
        with self.assertRaises(m.Invalid):
            providers.call("gemini", "public")

    def test_wrong_owner_fails_closed(self):
        with self.assertRaises(m.Invalid):
            m.AccountSelection({"SUPABASE_URL": "https://example.com", "SUPABASE_USER_ID": "U" + "a"*32,
                                "ADMIN_LINE_USER_ID": "U" + "b"*32, "SUPABASE_SERVICE_ROLE_KEY": "fake"}, Mock())

    def test_seven_day_cache(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TEST.json"
            current = dt.datetime(2026, 11, 27, tzinfo=dt.timezone.utc)
            answer = m.validate_answer(json.dumps(self.answer), self.news, None)
            m.write_report(path, self.public, dict(answer, provider="gemini", model="test"), current)
            self.assertEqual(json.loads(path.read_text())["scenarioEps"], 7)
            self.assertTrue(m.recent(path, current + dt.timedelta(days=6)))
            self.assertFalse(m.recent(path, current + dt.timedelta(days=7)))


if __name__ == "__main__":
    unittest.main()
