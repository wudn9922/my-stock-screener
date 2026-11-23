"""Receives broker statement PDFs from the owner's Gmail Apps Script and applies their trades.

POST /ingest with header X-Statement-Secret and JSON {messageId, from, receivedAt, pdf(base64)}.
The response's `done` tells the script whether to label the e-mail as handled: true once the
trades are stored (even if no account is linked yet) or the PDF is not a statement; false for
a wrong password or a layout we cannot read yet, so the e-mail is sent again after a fix.
Logs carry only a result code, counts and timing: no symbols, quantities or amounts.
"""

import base64
import binascii
import hmac
import json
import os
import re
import time

from flask import Flask, jsonify, request

from .parser import SENDER_DOMAINS, StatementError, parse_statement
from .repository import Repository, RepositoryError

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
OWNER = re.compile(r"^U[0-9a-f]{32}$")
APPLY_DONE = {"ok", "no-account", "oversell", "conflict"}


def env(name):
    return os.environ.get(name, "").strip()


def repository():
    return Repository(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"))


@app.get("/health")
def health():
    return jsonify(status="ok")


def authorized():
    secret = env("STATEMENT_SHARED_SECRET")
    return len(secret.encode()) >= 32 and hmac.compare_digest(request.headers.get("X-Statement-Secret", "").encode(), secret.encode())


@app.get("/senders")
def senders():
    """Broker sender domains for the Gmail search, so a new broker never needs an Apps Script edit."""
    if not authorized():
        return jsonify(code="forbidden"), 403
    return jsonify(senders=sorted(SENDER_DOMAINS))


@app.post("/ingest")
def ingest():
    started = time.monotonic()
    code, trades, done = "service", 0, False
    try:
        if not authorized():
            code = "forbidden"
            return jsonify(code=code, done=False), 403
        owner = env("STATEMENT_OWNER_ID")
        if not OWNER.match(owner) or not env("SUPABASE_URL") or not env("SUPABASE_SERVICE_ROLE_KEY"):
            return jsonify(code=code, done=False), 503
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("messageId"), str) or not 1 <= len(body["messageId"]) <= 200:
            code = "invalid"
            return jsonify(code=code, done=False), 400
        try:
            data = base64.b64decode(body.get("pdf") or "", validate=True)
        except (binascii.Error, ValueError, TypeError):
            code = "invalid"
            return jsonify(code=code, done=False), 400
        received = body.get("receivedAt") if isinstance(body.get("receivedAt"), str) else None
        passwords = [p for p in env("STATEMENT_PDF_PASSWORDS").splitlines() if p.strip()]
        repo = repository()
        message = body["messageId"]
        try:
            statement = parse_statement(data, passwords, str(body.get("from") or ""))
        except StatementError as error:
            code = "ignored" if error.code == "not-statement" else error.code
            done = code == "ignored"
            repo.save_message(owner, message, broker=None, received_at=received, report_date=None,
                              status=code, detail=error.detail, trades=0)
            return jsonify(code=code, detail=error.detail, done=done)
        trades = len(statement.trades)
        repo.save_trades(owner, statement.broker, message, statement.trades)
        markets = {t.market for t in statement.trades}
        if statement.snapshot:
            repo.save_snapshot(owner, statement.broker, message, statement.report_date, statement.snapshot)
            markets.add(statement.snapshot["market"])
        results = [repo.apply(owner, statement.broker, market) for market in sorted(markets)]
        statuses = [r["status"] for r in results] or ["ok"]
        code = next((s for s in statuses if s != "ok"), "ok")
        done = all(s in APPLY_DONE for s in statuses)
        repo.save_message(owner, message, broker=statement.broker, received_at=received, report_date=statement.report_date,
                          status=code, detail=statement.account_hint, trades=trades)
        return jsonify(code=code, trades=trades, done=done)
    except RepositoryError:
        code = "service"
        return jsonify(code=code, done=False), 503
    finally:
        print(json.dumps({"step": "ingest", "code": code, "trades": trades, "done": done,
                          "elapsedMs": int((time.monotonic() - started) * 1000)}), flush=True)
