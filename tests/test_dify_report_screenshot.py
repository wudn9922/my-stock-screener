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


def snapshot_payload(hours_ago, status="200", available=True, now=None):
    ts = (
        (now or datetime.now(timezone.utc)) - timedelta(hours=hours_ago)
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


def cdx_payload(captures, now=None):
    now = now or datetime.now(timezone.utc)
    return [["timestamp", "statuscode", "mimetype"]] + [
        [
            (now - timedelta(hours=hours)).strftime("%Y%m%d%H%M%S"),
            status,
            mimetype,
        ]
        for hours, status, mimetype in captures
    ]


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
    def setUp(self):
        self.now = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
        clock = mock.patch.object(dify_report, "datetime", wraps=datetime)
        self.addCleanup(clock.stop)
        clock.start().now.return_value = self.now

    def call(self, payload, captures=()):
        def fake_get(url, **_kwargs):
            if url == dify_report.WAYBACK_AVAILABLE_API:
                return FakeResponse(payload)
            self.assertEqual(url, dify_report.WAYBACK_CDX_API)
            return FakeResponse(cdx_payload(captures, now=self.now))

        with mock.patch.object(
            dify_report.requests, "get", side_effect=fake_get,
        ):
            return dify_report.get_wayback_snapshot(
                "wsj.com", "https://www.wsj.com/", max_age_hours=36
            )

    def test_builds_toolbar_free_url(self):
        url, snapshot_time = self.call(snapshot_payload(5, now=self.now))
        self.assertTrue(url.startswith("https://web.archive.org/web/"))
        self.assertIn("if_/https://www.wsj.com/", url)
        self.assertLess(
            self.now - snapshot_time, timedelta(hours=6)
        )

    def test_stale_snapshot_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "超過上限 36 小時"):
            self.call(
                snapshot_payload(50, now=self.now),
                captures=[(50, "200", "text/html")],
            )

    def test_missing_or_bad_snapshot_rejected(self):
        with self.assertRaises(RuntimeError):
            self.call({"archived_snapshots": {}})
        with self.assertRaises(RuntimeError):
            self.call(snapshot_payload(1, status="404"))
        with self.assertRaises(RuntimeError):
            self.call(snapshot_payload(1, available=False))

    def test_uses_newest_valid_html_skipping_newer_bad_captures(self):
        # CDX 的列順序不保證是由新到舊；最近一筆是空的 204 存檔。
        captures = [
            (5, "200", "text/html"),
            (1, "204", "text/html"),
            (3, "200", "text/html"),
            (2, "200", "application/pdf"),
        ]
        with mock.patch.object(
            dify_report.requests, "get",
            return_value=FakeResponse(cdx_payload(captures, now=self.now)),
        ) as get, mock.patch("builtins.print") as log:
            url, snapshot_time = dify_report.get_wayback_snapshot(
                "wsj.com", "https://www.wsj.com/", max_age_hours=36
            )

        self.assertEqual(snapshot_time, self.now - timedelta(hours=3))
        self.assertEqual(
            url, "https://web.archive.org/web/20261025063000if_/https://www.wsj.com/"
        )
        get.assert_called_once_with(
            dify_report.WAYBACK_CDX_API,
            params={
                "url": "wsj.com",
                "from": "20261023213000",
                "to": "20261025093000",
                "output": "json",
                "fl": "timestamp,statuscode,mimetype",
            },
            timeout=30,
        )
        logs = "\n".join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn("20261025063000", logs)
        self.assertIn("略過 2 筆較新存檔", logs)
        self.assertIn("HTTP 204", logs)
        self.assertIn("mimetype application/pdf", logs)

    def test_capture_at_max_age_is_accepted(self):
        with mock.patch.object(dify_report, "WAYBACK_MAX_AGE_HOURS", 12):
            with mock.patch.object(
                dify_report.requests, "get",
                return_value=FakeResponse(cdx_payload(
                    [(12, "200", "text/html")], now=self.now
                )),
            ) as get:
                _, snapshot_time = dify_report.get_wayback_snapshot(
                    "wsj.com", "https://www.wsj.com/"
                )
        self.assertEqual(snapshot_time, self.now - timedelta(hours=12))
        self.assertEqual(get.call_args.kwargs["params"]["from"], "20261024213000")

    def test_cdx_error_retries_then_falls_back_to_available(self):
        with mock.patch.object(
            dify_report.requests, "get", side_effect=[
                FakeResponse(status=503),
                dify_report.requests.Timeout("CDX timeout"),
                FakeResponse(status=503),
                FakeResponse(snapshot_payload(5, now=self.now)),
            ],
        ) as get, mock.patch.object(dify_report.time, "sleep") as sleep:
            url, snapshot_time = dify_report.get_wayback_snapshot(
                "wsj.com", "https://www.wsj.com/", max_age_hours=36
            )

        self.assertEqual(snapshot_time, self.now - timedelta(hours=5))
        self.assertIn("20261025043000if_/https://www.wsj.com/", url)
        self.assertEqual(
            [call.args[0] for call in get.call_args_list],
            [dify_report.WAYBACK_CDX_API] * 3 + [dify_report.WAYBACK_AVAILABLE_API],
        )
        self.assertTrue(all(
            call.kwargs["timeout"] == 30 for call in get.call_args_list
        ))
        self.assertEqual(sleep.call_args_list, [mock.call(5), mock.call(5)])

    def test_cdx_retry_success_does_not_call_available(self):
        with mock.patch.object(
            dify_report.requests, "get", side_effect=[
                FakeResponse(status=503),
                FakeResponse(cdx_payload([(2, "200", "text/html")], now=self.now)),
            ],
        ) as get, mock.patch.object(dify_report.time, "sleep") as sleep:
            _, snapshot_time = dify_report.get_wayback_snapshot(
                "wsj.com", "https://www.wsj.com/", max_age_hours=36
            )
        self.assertEqual(snapshot_time, self.now - timedelta(hours=2))
        self.assertEqual(get.call_count, 2)
        self.assertTrue(all(
            call.args[0] == dify_report.WAYBACK_CDX_API for call in get.call_args_list
        ))
        sleep.assert_called_once_with(5)

    def test_unusable_cdx_captures_fall_back_to_available(self):
        _, snapshot_time = self.call(
            snapshot_payload(5, now=self.now),
            captures=[(1, "204", "text/html"), (2, "200", "application/json")],
        )
        self.assertEqual(snapshot_time, self.now - timedelta(hours=5))


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
            return_value=FakeResponse(cdx_payload([(2, "200", "text/html")])),
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
        # 截圖本身不發佈；去重複與發佈由 capture_prepare_and_publish 負責
        publish.assert_not_called()

    def test_wsj_goes_through_wayback(self):
        page = SequencedPage(first_is_blocked=False)
        captured, failures, _ = self.capture(page)

        self.assertEqual([c["name"] for c in captured], ["WSJ", "Barron's"])
        self.assertEqual(failures, [])
        self.assertTrue(
            page.goto_urls[0].startswith("https://web.archive.org/web/")
        )
        # 2026-11-05 起 Barron's 直接連線回 401，比照 WSJ 改截 Wayback
        self.assertTrue(
            page.goto_urls[1].startswith("https://web.archive.org/web/")
        )
        self.assertTrue(page.goto_urls[1].endswith("if_/https://www.barrons.com"))
        self.assertTrue(all(c.get("snapshot") for c in captured))

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
    ITEMS = [{"name": "WSJ", "path": "docs/wsj.jpg", "url": None,
              "publish_path": "docs/news/wsj-20261106-0123456789ab.jpg"}]

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
        # 專屬檔名、不帶 ?v=：舊 LINE 訊息重新讀取時仍是當天那張
        self.assertEqual(
            items[0]["url"],
            "https://raw.githubusercontent.com/o/r/generated/docs/news/wsj-20261106-0123456789ab.jpg",
        )

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
        self.assertIn("/o/r/main/docs/news/wsj-20261106-0123456789ab.jpg", items[0]["url"])
        self.assertIn(["git", "add", "-A", "-f", "--", "docs/news"], calls)


class NewsDedupTest(unittest.TestCase):
    """每天送出的圖另存不覆寫的檔名；相同或失敗的來源只寫「今日無新聞」。"""

    NOW = datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.news = os.path.join(self.root, "news")

    def shot(self, name, content):
        path = os.path.join(self.root, f"{name}.jpg")
        with open(path, "wb") as f:
            f.write(content)
        return path

    def prepare(self, captured, failures, state, now=None):
        return dify_report.prepare_news_items(captured, failures, now or self.NOW, state, news_dir=self.news)

    def test_new_image_gets_an_immutable_dated_fingerprint_name(self):
        state = {"version": 1, "sources": {}}
        item = {"name": "Barron's", "path": self.shot("barrons", b"day1"), "url": None, "snapshot": "x"}
        send, no_news = self.prepare([item], [], state)
        self.assertEqual(no_news, [])
        self.assertEqual(len(send), 1)
        digest = dify_report.file_sha256(item["path"])
        expected = f"{self.news}/barrons-20261106-{digest[:12]}.jpg"
        self.assertEqual(send[0]["publish_path"], expected)
        with open(expected, "rb") as f:
            self.assertEqual(f.read(), b"day1")
        self.assertEqual(state["sources"]["Barron's"]["sha256"], digest)
        self.assertEqual(state["sources"]["Barron's"]["failStreak"], 0)

    def test_identical_image_is_not_sent_again(self):
        state = {"version": 1, "sources": {}}
        first = {"name": "WSJ", "path": self.shot("wsj", b"same"), "url": None}
        self.prepare([first], [], state)
        send, no_news = self.prepare([dict(first)], [], state, self.NOW + timedelta(days=1))
        self.assertEqual(send, [])
        self.assertEqual(no_news, [{"name": "WSJ", "reason": "same", "streak": 0}])
        self.assertEqual(dify_report.no_news_lines(no_news), "📰 WSJ：今日無新聞")

    def test_changed_image_is_sent_with_a_new_name_and_old_file_kept(self):
        state = {"version": 1, "sources": {}}
        path = self.shot("wsj", b"day1")
        first, _ = self.prepare([{"name": "WSJ", "path": path, "url": None}], [], state)
        self.shot("wsj", b"day2")
        second, _ = self.prepare([{"name": "WSJ", "path": path, "url": None}], [], state, self.NOW + timedelta(days=1))
        self.assertNotEqual(first[0]["publish_path"], second[0]["publish_path"])
        self.assertTrue(os.path.exists(first[0]["publish_path"]))
        self.assertIn("-20261107-", second[0]["publish_path"])

    def test_failures_count_consecutive_days_and_notice_only_on_day_one_and_seven(self):
        state = {"version": 1, "sources": {}}
        lines = []
        for day in range(1, 9):
            _, no_news = self.prepare([], [{"name": "Barron's", "reason": "401"}], state,
                                      self.NOW + timedelta(days=day))
            lines.append(dify_report.no_news_lines(no_news))
        self.assertIn("網站擋下截圖", lines[0])
        self.assertNotIn("連續", lines[0])
        self.assertTrue(all(line == "📰 Barron's：今日無新聞" for line in lines[1:6] + lines[7:]))
        self.assertIn("已連續 7 天", lines[6])
        # 恢復成功後連續失敗歸零
        item = {"name": "Barron's", "path": self.shot("barrons", b"back"), "url": None}
        self.prepare([item], [], state)
        self.assertEqual(state["sources"]["Barron's"]["failStreak"], 0)

    def test_prune_keeps_recent_and_unrelated_files(self):
        os.makedirs(self.news)
        names = ["wsj-20261006-aaaaaaaaaaaa.jpg", "wsj-20261007-bbbbbbbbbbbb.jpg",
                 "wsj-20261106-cccccccccccc.jpg", "keep-me.jpg"]
        for name in names:
            open(os.path.join(self.news, name), "wb").close()
        removed = dify_report.prune_news_files(self.NOW, self.news, keep_days=30)
        self.assertEqual(removed, [f"{self.news}/wsj-20261006-aaaaaaaaaaaa.jpg"])
        self.assertEqual(sorted(os.listdir(self.news)), sorted(names[1:]))

    def test_state_roundtrip_and_corrupt_state_starts_fresh(self):
        path = os.path.join(self.root, "state.json")
        dify_report.save_news_state({"version": 1, "sources": {"WSJ": {"sha256": "x"}}}, path)
        self.assertEqual(dify_report.load_news_state(path)["sources"]["WSJ"]["sha256"], "x")
        with open(path, "w") as f:
            f.write("{broken")
        self.assertEqual(dify_report.load_news_state(path), {"version": 1, "sources": {}})

    def test_full_flow_publishes_state_even_without_new_images(self):
        old_cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        os.makedirs("docs")
        with open("docs/wsj.jpg", "wb") as f:
            f.write(b"wsj")
        captured = [{"name": "WSJ", "path": "docs/wsj.jpg", "url": None}]
        failures = [{"name": "Barron's", "reason": "HTTP 401"}]
        with mock.patch.object(dify_report, "capture_and_upload_screenshots",
                               return_value=(captured, failures)), \
             mock.patch.object(dify_report, "publish_screenshots") as publish:
            send, no_news = dify_report.capture_prepare_and_publish(self.NOW)
            self.assertEqual([i["name"] for i in send], ["WSJ"])
            self.assertTrue(os.path.exists(send[0]["publish_path"]))
            self.assertEqual([n["name"] for n in no_news], ["Barron's"])
            publish.assert_called_once_with(send)
            # 隔天同一張 WSJ：不送圖，但仍發佈狀態檔
            send2, no_news2 = dify_report.capture_prepare_and_publish(self.NOW + timedelta(days=1))
        self.assertEqual(send2, [])
        self.assertEqual([(n["name"], n["reason"]) for n in no_news2], [("WSJ", "same"), ("Barron's", "failed")])
        self.assertEqual(publish.call_count, 2)
        state = dify_report.load_news_state()
        self.assertEqual(state["sources"]["Barron's"]["failStreak"], 2)
