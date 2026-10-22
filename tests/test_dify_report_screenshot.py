import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# CI 不一定裝 playwright；本測試用假物件取代
if "playwright.sync_api" not in sys.modules:
    fake_pkg = types.ModuleType("playwright")
    fake_api = types.ModuleType("playwright.sync_api")
    fake_api.sync_playwright = lambda: None
    sys.modules["playwright"] = fake_pkg
    sys.modules["playwright.sync_api"] = fake_api

import dify_report  # noqa: E402


class FakeFrame:
    def __init__(self, text="", url="https://www.wsj.com/", fail=False):
        self.text = text
        self.url = url
        self.fail = fail

    def evaluate(self, _script):
        if self.fail:
            raise RuntimeError("frame detached")
        return self.text


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status = status
        self.status_code = status
        self.ok = status < 400
        self.text = ""

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.payload


class FakePage:
    def __init__(self, frames, status=200, scroll_y=650):
        self.frames = frames
        self._status = status
        self._scroll_y = scroll_y
        self.screenshots = []
        self.goto_urls = []

    def add_init_script(self, _script):
        pass

    def goto(self, url, **_kwargs):
        self.goto_urls.append(url)
        return types.SimpleNamespace(status=self._status)

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, script, *_args):
        if "scrollY" in script and "scrollTo" not in script:
            return self._scroll_y
        return None

    def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)


def fake_playwright(page):
    """讓 sync_playwright() 永遠回傳同一個假頁面。"""

    class PW:
        def __call__(self):
            return self

        def __enter__(self):
            context = types.SimpleNamespace(new_page=lambda: page)
            browser = types.SimpleNamespace(
                new_context=lambda **_kw: context,
                close=lambda: None,
            )
            return types.SimpleNamespace(
                firefox=types.SimpleNamespace(launch=lambda **_kw: browser)
            )

        def __exit__(self, *_exc):
            return False

    return PW()


def snapshot_payload(hours_ago, status="200", available=True):
    ts = (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).strftime("%Y%m%d%H%M%S")
    return {
        "archived_snapshots": {
            "closest": {
                "available": available,
                "status": status,
                "timestamp": ts,
                "url": "http://web.archive.org/...",
            }
        }
    }


class BlockedPageTest(unittest.TestCase):
    def test_detects_verification_page(self):
        self.assertTrue(dify_report.is_blocked_page(
            "The Wall Street Journal\nVerification Required\nSlide right"
        ))

    def test_normal_page_is_not_blocked(self):
        self.assertFalse(dify_report.is_blocked_page("Stocks rally as ..."))
        self.assertFalse(dify_report.is_blocked_page(None))

    def test_http_error_status_is_blocked(self):
        page = types.SimpleNamespace(frames=[FakeFrame("")])
        response = types.SimpleNamespace(status=403)
        self.assertIn("403", dify_report.detect_blocked_page(page, response))

    def test_verification_text_inside_iframe_is_blocked(self):
        page = types.SimpleNamespace(
            frames=[FakeFrame(""), FakeFrame("Verification Required")]
        )
        response = types.SimpleNamespace(status=200)
        self.assertIsNotNone(dify_report.detect_blocked_page(page, response))

    def test_captcha_iframe_url_is_blocked_even_if_text_unreadable(self):
        page = types.SimpleNamespace(frames=[
            FakeFrame("Top stories"),
            FakeFrame(fail=True, url="https://geo.captcha-delivery.com/x"),
        ])
        self.assertIsNotNone(
            dify_report.detect_blocked_page(page, None)
        )

    def test_detached_frame_is_ignored(self):
        page = types.SimpleNamespace(
            frames=[FakeFrame(fail=True), FakeFrame("Markets")]
        )
        response = types.SimpleNamespace(status=200)
        self.assertIsNone(dify_report.detect_blocked_page(page, response))


class WaybackSnapshotTest(unittest.TestCase):
    def call(self, payload):
        with mock.patch.object(
            dify_report.requests, "get",
            return_value=FakeResponse(payload),
        ):
            return dify_report.get_wayback_snapshot(
                "wsj.com", "https://www.wsj.com/", max_age_hours=36
            )

    def test_builds_toolbar_free_url(self):
        url, snapshot_time = self.call(snapshot_payload(5))
        self.assertTrue(url.startswith("https://web.archive.org/web/"))
        self.assertIn("if_/https://www.wsj.com/", url)
        self.assertLess(
            datetime.now(timezone.utc) - snapshot_time, timedelta(hours=6)
        )

    def test_stale_snapshot_rejected(self):
        with self.assertRaises(RuntimeError):
            self.call(snapshot_payload(50))

    def test_missing_or_bad_snapshot_rejected(self):
        with self.assertRaises(RuntimeError):
            self.call({"archived_snapshots": {}})
        with self.assertRaises(RuntimeError):
            self.call(snapshot_payload(1, status="404"))
        with self.assertRaises(RuntimeError):
            self.call(snapshot_payload(1, available=False))


class SequencedPage(FakePage):
    """第一次造訪（WSJ）顯示驗證頁，第二次（Barron's）顯示正常頁。"""

    def __init__(self, first_is_blocked=True):
        super().__init__([])
        self.visit = 0
        self.first_is_blocked = first_is_blocked

    def goto(self, url, **kwargs):
        self.visit += 1
        self.goto_urls.append(url)

        if self.visit == 1 and self.first_is_blocked:
            self.frames = [FakeFrame("Verification Required")]
        else:
            self.frames = [FakeFrame("Top stories")]

        return types.SimpleNamespace(status=200)


class CaptureFlowTest(unittest.TestCase):
    def capture(self, page):
        with mock.patch.object(
            dify_report, "sync_playwright", fake_playwright(page)
        ), mock.patch.object(
            dify_report.requests, "get",
            return_value=FakeResponse(snapshot_payload(2)),
        ), mock.patch.object(
            dify_report, "publish_screenshots"
        ) as publish, mock.patch.object(dify_report.os, "makedirs"):
            captured, failures = dify_report.capture_and_upload_screenshots()

        return captured, failures, publish

    def test_blocked_wsj_is_reported_and_not_sent(self):
        page = SequencedPage()
        captured, failures, publish = self.capture(page)

        self.assertEqual([c["name"] for c in captured], ["Barron's"])
        self.assertEqual([f["name"] for f in failures], ["WSJ"])
        # 驗證頁沒有被截圖，只截了 Barron's 一張，且是 JPEG
        self.assertEqual(len(page.screenshots), 1)
        self.assertEqual(page.screenshots[0]["type"], "jpeg")
        publish.assert_called_once()

    def test_wsj_goes_through_wayback(self):
        page = SequencedPage(first_is_blocked=False)
        captured, failures, _ = self.capture(page)

        self.assertEqual([c["name"] for c in captured], ["WSJ", "Barron's"])
        self.assertEqual(failures, [])
        self.assertTrue(
            page.goto_urls[0].startswith("https://web.archive.org/web/")
        )
        self.assertEqual(page.goto_urls[1], "https://www.barrons.com")

    def test_unscrollable_page_is_rejected(self):
        page = FakePage([FakeFrame("Top stories")], scroll_y=0)
        captured, failures, _ = self.capture(page)

        self.assertEqual(captured, [])
        self.assertEqual(len(failures), 2)
        self.assertEqual(page.screenshots, [])


class LineMessagesTest(unittest.TestCase):
    URLS = ["https://x/wsj.jpg?v=1", "https://x/barrons.jpg?v=1"]

    def test_text_plus_images_is_two_messages(self):
        messages = dify_report.build_line_messages("report", self.URLS)
        self.assertEqual([m["type"] for m in messages], ["text", "flex"])
        images = messages[1]["contents"]["body"]["contents"]
        self.assertEqual([i["url"] for i in images], self.URLS)

    def test_no_images_is_one_message(self):
        messages = dify_report.build_line_messages("report", [None])
        self.assertEqual([m["type"] for m in messages], ["text"])

    def test_plain_image_fallback(self):
        messages = dify_report.build_line_messages(
            "report", self.URLS, as_flex=False
        )
        self.assertEqual(
            [m["type"] for m in messages], ["text", "image", "image"]
        )

    def test_flex_rejected_falls_back_to_images(self):
        responses = [FakeResponse(status=400), FakeResponse(status=200)]
        sent = []

        def fake_push(messages):
            sent.append([m["type"] for m in messages])
            return responses.pop(0)

        with mock.patch.object(dify_report, "LINE_ACCESS_TOKEN", "t"), \
             mock.patch.object(dify_report, "LINE_USER_ID", "U1"), \
             mock.patch.object(dify_report, "push_line_messages", fake_push):
            dify_report.send_line_report("report", self.URLS)

        self.assertEqual(
            sent, [["text", "flex"], ["text", "image", "image"]]
        )


if __name__ == "__main__":
    unittest.main()


class PublishScreenshotsTest(unittest.TestCase):
    ITEMS = [{"name": "WSJ", "path": "docs/wsj.jpg", "url": None}]

    def publish(self, env, returncode=0):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return types.SimpleNamespace(returncode=returncode)

        items = [dict(item) for item in self.ITEMS]

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(dify_report.subprocess, "run", fake_run):
            dify_report.publish_screenshots(items)

        return items, calls

    def test_generated_mode_publishes_via_script_and_uses_generated_url(self):
        items, calls = self.publish({
            "GENERATED_BRANCH_MODE": "1",
            "GITHUB_REPOSITORY": "o/r",
        })

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], [
            "scripts/generated_branch.py", "publish", "screenshots",
        ])
        self.assertTrue(items[0]["url"].startswith(
            "https://raw.githubusercontent.com/o/r/generated/docs/wsj.jpg?v="
        ))

    def test_generated_mode_failure_omits_image_url(self):
        items, _ = self.publish(
            {"GENERATED_BRANCH_MODE": "1", "GITHUB_REPOSITORY": "o/r"},
            returncode=1,
        )
        self.assertIsNone(items[0]["url"])

    def test_normal_mode_pushes_current_branch(self):
        items, calls = self.publish({
            "GENERATED_BRANCH_MODE": "0",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_REF_NAME": "main",
        })

        self.assertTrue(any(c[:2] == ["git", "push"] for c in calls))
        self.assertIn("/o/r/main/docs/wsj.jpg", items[0]["url"])
