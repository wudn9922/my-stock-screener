"""
FinMind API client shared by build_fundamentals_tw_finmind.py (growth history, one dataset) and
build_financials_tw_finmind.py (statement history, three datasets): one request = one dataset of one stock
(GET /api/v4/data), token, pacing, retries and FinMind's refusal / limit answers. Standard library only.

  Answers: HTTP 200 with a list in "data" (msg success; an empty list is a real "no data"); HTTP 402 / 429 or a
  msg about the limit → RateLimited (stop, keep the progress); HTTP 401 / 403 or a msg about the token →
  AuthError (stop the run); anything else → FinMindError (transient = worth a retry: 5xx, transport errors).
  Env (read by the callers): FINMIND_TOKEN, sent as "Authorization: Bearer"; it raises the hourly limit.
"""
import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

API_URL = "https://api.finmindtrade.com/api/v4/data"
USER_AGENT = "my-stock-screener fundamentals (github.com/wudn9922/my-stock-screener)"
HOURLY_LIMIT = {False: 300, True: 600}
RATE_SAFETY = 0.95
RETRIES = 2
RETRY_WAIT = 20.0
# Answers that mean "stop for now" / "the token is wrong" (FinMind: {"msg": "...", "status": 402})
LIMIT_MARKERS = ("UPPER LIMIT", "TOO MANY", "RATE LIMIT", "EXCEED", "LIMIT REACHED", "上限", "超過")
AUTH_MARKERS = ("TOKEN", "UNAUTHORIZED", "NOT AUTHORIZED", "PERMISSION", "LOGIN", "權限")


class RateLimited(RuntimeError):
    """FinMind says the request limit is reached: stop and keep the progress."""


class AuthError(RuntimeError):
    """FinMind refused the token / the request (HTTP 401 / 403)."""


class LimitReached(RuntimeError):
    """--limit requests are used up (checked before every attempt, retries included)."""


class FinMindError(ValueError):
    """An answer this parser does not understand (details for the log); transient = worth a retry."""

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


def parse_response(status, body):
    """
    HTTP status + body → the list of rows. RateLimited (402 / 429 / a msg about the limit), AuthError
    (401 / 403 / a msg about the token), FinMindError otherwise (transient for 5xx); the message has the
    status, FinMind's msg and the start of the body, enough to read in a log.
    """
    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    try:
        document = json.loads(text) if text.strip() else None
    except ValueError:
        document = None
    if isinstance(document, dict):
        msg, inner = str(document.get("msg") or ""), document.get("status")
    else:
        msg, inner = "", None
    data = document.get("data") if isinstance(document, dict) else None
    # An empty list is a real "no data" only with msg success: any other msg explains a refusal (not a stock
    # without data), and must not be stored as "fetched, nothing published".
    if status == 200 and isinstance(data, list) and inner in (None, 200, "200") and (data or msg.strip().lower() in ("", "success")):
        return data
    shown = msg or " ".join(text.split())[:200]
    upper = shown.upper()
    detail = f"HTTP {status}, status {inner!r}, msg {shown!r}"
    if status in (402, 429) or inner in (402, 429, "402", "429") or any(marker in upper for marker in LIMIT_MARKERS):
        raise RateLimited(f"FinMind request limit: {detail}")
    if status in (401, 403) or inner in (401, 403, "401", "403") or any(marker in upper for marker in AUTH_MARKERS):
        raise AuthError(f"FinMind refused the request: {detail}")
    if status == 200 and isinstance(document, dict) and data is None:
        raise FinMindError(f"no 'data' list in the answer ({detail}; keys {sorted(document)[:8]})")
    raise FinMindError(detail, transient=status is None or status >= 500 or document is None and status == 200)


class Http:
    """urllib GET → (status, body bytes); HTTP errors return their status and body (FinMind explains in JSON)."""

    def __init__(self, timeout=60):
        self.timeout = timeout

    @staticmethod
    def _body(response):
        body = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
        return body

    def get(self, url, headers):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, self._body(response)
        except urllib.error.HTTPError as error:
            try:
                return error.code, self._body(error)
            except (OSError, ValueError):
                return error.code, b""


def request_url(dataset, code, start_date):
    return API_URL + "?" + urllib.parse.urlencode({"dataset": dataset, "data_id": code, "start_date": start_date})


def request_interval(token):
    """Seconds between two requests: the hourly limit (600 with a token, else 300) x RATE_SAFETY, spread evenly."""
    return 3600.0 / (HOURLY_LIMIT[bool(token)] * RATE_SAFETY)


def limit_used(counter):
    """True when the --limit requests are all used (counter["limit"] None: no limit)."""
    return counter.get("limit") is not None and counter["requests"] >= counter["limit"]


def fetch_rows(http, dataset, code, start_date, token, sleep, log, counter):
    """One stock of one dataset → FinMind rows (transient errors retried RETRIES times); RateLimited / AuthError /
    FinMindError / LimitReached (counter["limit"] requests used up). counter: {"requests": n, "limit": n or None}."""
    headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    problem = None
    for attempt in range(RETRIES + 1):
        if limit_used(counter):
            raise LimitReached(f"--limit {counter['limit']} requests")
        counter["requests"] += 1
        try:
            status, body = http.get(request_url(dataset, code, start_date), headers)
        except (OSError, HTTPException, ValueError) as error:
            problem = f"{type(error).__name__}: {error}"
        else:
            try:
                return parse_response(status, body)
            except FinMindError as error:
                if not error.transient:
                    raise
                problem = str(error)
        if attempt < RETRIES:
            used_up = limit_used(counter)
            log(f"FinMind {dataset} {code}: {problem}" + ("" if used_up else f"; retrying in {RETRY_WAIT:.0f}s"))
            if not used_up:
                sleep(RETRY_WAIT)
    raise FinMindError(f"{problem} (after {RETRIES + 1} attempts)", transient=True)
