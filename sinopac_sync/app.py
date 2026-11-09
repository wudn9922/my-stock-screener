"""Single worker, no SDK import/login until an authenticated, claimed request."""

import contextlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from .core import synchronize, read_positions, SyncError, MESSAGES
from .repository import Repository

# Secret Manager env injection is read once at instance startup.
CONFIG = {
    k: os.environ.get(k, "")
    for k in (
        "SJ_API_KEY",
        "SJ_SEC_KEY",
        "SINOPAC_SHARED_SECRET",
        "SINOPAC_OWNER_ID",
        "SINOPAC_ACCOUNT_ID",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
    )
}


@contextlib.contextmanager
def quiet_sdk():
    saved = [os.dup(1), os.dup(2)]
    with open(os.devnull, "w") as sink:
        try:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
        finally:
            for target, descriptor in zip((1, 2), saved):
                os.dup2(descriptor, target)
                os.close(descriptor)


def positions():
    with quiet_sdk():
        import shioaji as sj

        return read_positions(
            lambda: sj.Shioaji(simulation=False),
            sj.Unit,
            CONFIG["SJ_API_KEY"],
            CONFIG["SJ_SEC_KEY"],
        )


app = Flask(__name__)


@app.post("/sync")
def sync():
    started = time.monotonic()
    code = "service"
    try:
        secret = CONFIG["SINOPAC_SHARED_SECRET"]
        scheduler = False
        authorization = request.headers.get("Authorization", "")
        if (
            authorization.startswith("Bearer ")
            and os.environ.get("SINOPAC_SCHEDULER_EMAIL")
            and os.environ.get("SINOPAC_AUDIENCE")
        ):
            try:
                from google.oauth2 import id_token
                from google.auth.transport.requests import Request

                claims = id_token.verify_oauth2_token(
                    authorization[7:], Request(), os.environ["SINOPAC_AUDIENCE"]
                )
                scheduler = (
                    claims.get("email_verified") is True
                    and claims.get("email") == os.environ["SINOPAC_SCHEDULER_EMAIL"]
                )
            except Exception:
                scheduler = False
        if len(secret.encode()) < 32 or (
            not scheduler
            and not hmac.compare_digest(
                request.headers.get("X-Sinopac-Secret", "").encode(), secret.encode()
            )
        ):
            raise SyncError("forbidden")
        if not all(CONFIG.values()) or not re.fullmatch(
            r"U[0-9a-f]{32}", CONFIG["SINOPAC_OWNER_ID"]
        ):
            raise SyncError("service")
        account_uuid = uuid.UUID(CONFIG["SINOPAC_ACCOUNT_ID"])
        if str(account_uuid) != CONFIG[
            "SINOPAC_ACCOUNT_ID"
        ] or account_uuid.version not in range(1, 6):
            raise SyncError("service")
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise SyncError("forbidden")
        caller = body.get("ownerId")
        if (
            caller != CONFIG["SINOPAC_OWNER_ID"]
            or body.get("accountId") != CONFIG["SINOPAC_ACCOUNT_ID"]
        ):
            raise SyncError("forbidden")
        calendar = json.load(
            open(os.environ.get("SINOPAC_CALENDAR_PATH", "/app/calendar.json"))
        )
        catalog = json.load(
            open(os.environ.get("SINOPAC_CATALOG_PATH", "/app/catalog.json"))
        )
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        result = synchronize(
            Repository(CONFIG["SUPABASE_URL"], CONFIG["SUPABASE_SERVICE_ROLE_KEY"]),
            CONFIG["SINOPAC_OWNER_ID"],
            CONFIG["SINOPAC_ACCOUNT_ID"],
            caller,
            now.date().isoformat(),
            calendar,
            positions,
            catalog,
            now.isoformat(),
        )
        code = result["code"]
        return jsonify(result)
    except Exception as error:
        code = error.code if isinstance(error, SyncError) else "service"
        return jsonify(code=code, message=MESSAGES[code]), (
            403
            if code == "forbidden"
            else 429 if code == "rate" else 409 if code == "conflict" else 503
        )
    finally:
        print(
            json.dumps(
                {
                    "step": "sync",
                    "code": code,
                    "elapsedMs": int((time.monotonic() - started) * 1000),
                }
            ),
            flush=True,
        )
