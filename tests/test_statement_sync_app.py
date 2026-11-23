import base64
import os
import unittest
from unittest.mock import patch

try:
    from statement_sync import app as service
    from test_statement_parser import MMS, UHS, report
    from test_statement_sinopac import report as sinopac_report
except ImportError as missing:  # the statement-sync workflow installs these; other suites skip
    raise unittest.SkipTest(f"statement sync test dependencies unavailable: {missing.name}")

OWNER = "Ua0037dbd5037b64fc76804c60520ad76"
SECRET = "s" * 40
ENV = {"STATEMENT_SHARED_SECRET": SECRET, "STATEMENT_OWNER_ID": OWNER, "SUPABASE_URL": "https://x.supabase.co",
       "SUPABASE_SERVICE_ROLE_KEY": "key", "STATEMENT_PDF_PASSWORDS": "other\nA123456789\n"}


class FakeRepository:
    def __init__(self, status="ok"):
        self.status, self.trades, self.messages, self.applied, self.snapshots = status, [], [], [], []

    def save_trades(self, owner, broker, message_id, trades):
        self.trades.extend(trades)

    def save_snapshot(self, owner, broker, message_id, report_date, snapshot):
        self.snapshots.append((broker, report_date, snapshot["market"], dict(snapshot["positions"])))

    def save_message(self, owner, message_id, **fields):
        self.messages.append(dict(fields, message_id=message_id))

    def apply(self, owner, broker, market):
        self.applied.append((broker, market))
        return {"status": self.status}


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.client = service.app.test_client()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, pdf, fake, secret=SECRET, message="msg-1"):
        with patch.object(service, "repository", return_value=fake):
            return self.client.post("/ingest", headers={"X-Statement-Secret": secret}, json={
                "messageId": message, "from": "report@cathaysec.com.tw", "receivedAt": "2026-11-19T12:00:00Z",
                "pdf": base64.b64encode(pdf).decode()})

    def test_stores_and_applies_trades_then_marks_done(self):
        fake = FakeRepository()
        response = self.post(report(rows=(UHS, MMS)), fake)
        self.assertEqual(response.get_json(), {"code": "ok", "trades": 2, "done": True})
        self.assertEqual(len(fake.trades), 2)
        self.assertEqual(fake.applied, [("cathay", "US")])
        self.assertEqual(fake.messages[-1]["status"], "ok")
        self.assertEqual(fake.messages[-1]["report_date"], "2026-11-19")

    def test_sinopac_report_stores_inventory_snapshot_and_applies_taiwan(self):
        fake = FakeRepository()
        self.assertEqual(self.post(sinopac_report(), fake).get_json(), {"code": "ok", "trades": 1, "done": True})
        self.assertEqual(fake.applied, [("sinopac", "TW")])
        self.assertEqual([s[:3] for s in fake.snapshots], [("sinopac", "2026-11-23", "TW")])
        self.assertEqual(set(fake.snapshots[0][3]), {"031863.TW", "2308.TW"})

    def test_no_linked_account_still_counts_as_handled(self):
        fake = FakeRepository("no-account")
        self.assertEqual(self.post(report(), fake).get_json()["code"], "no-account")
        self.assertTrue(self.post(report(), fake).get_json()["done"])

    def test_wrong_secret_is_refused_before_reading(self):
        fake = FakeRepository()
        response = self.post(report(), fake, secret="x" * 40)
        self.assertEqual(response.status_code, 403)
        self.assertEqual((fake.trades, fake.messages), ([], []))

    def test_unreadable_or_locked_pdfs_are_retried_and_others_ignored(self):
        fake = FakeRepository()
        with patch.dict(os.environ, {"STATEMENT_PDF_PASSWORDS": "nope"}):
            locked = self.post(report(), fake).get_json()
        self.assertEqual((locked["code"], locked["done"]), ("password", False))
        ignored = self.post(b"%PDF-1.4 not really", fake, message="msg-2").get_json()
        self.assertEqual((ignored["code"], ignored["done"]), ("ignored", True))
        self.assertEqual(fake.trades, [])

    def test_sender_list_needs_the_secret(self):
        self.assertEqual(self.client.get("/senders", headers={"X-Statement-Secret": "x" * 40}).status_code, 403)
        senders = self.client.get("/senders", headers={"X-Statement-Secret": SECRET}).get_json()["senders"]
        self.assertIn("sinotrade.com.tw", senders)
        self.assertIn("cathaysec.com.tw", senders)

    def test_invalid_base64_is_rejected(self):
        with patch.object(service, "repository", return_value=FakeRepository()):
            response = self.client.post("/ingest", headers={"X-Statement-Secret": SECRET}, json={"messageId": "m", "pdf": "@@"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
