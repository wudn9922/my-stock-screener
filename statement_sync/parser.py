"""Broker e-mail statement (PDF) parsing.

Deliberately strict: every trade row must pass the report's own arithmetic (quantity × price ≈
amount; amount ± fee ± tax = net), and anything unexpected raises StatementError so that
nothing is written. Positions, prices and names never appear in errors or logs.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import io
import re
import statistics

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect


class StatementError(Exception):
    """code: password | not-statement | unsupported | unrecognized."""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:200]


@dataclass(frozen=True)
class Trade:
    key: str
    trade_date: str
    market: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    amount: Decimal  # buy: cost incl. fee and tax; sell: proceeds after fee and tax
    currency: str


@dataclass(frozen=True)
class Statement:
    broker: str
    report_date: str
    account_hint: str
    trades: tuple


BROKER_MARKS = (("cathay", ("國泰綜合證券", "Cathay Securities")), ("sinopac", ("永豐金證券", "SinoPac")), ("kgi", ("凱基證券", "KGI")))
SENDER_DOMAINS = {"cathaysec.com.tw": "cathay", "sinopac.com": "sinopac", "kgi.com": "kgi", "kgi.com.tw": "kgi"}
NUMBER = re.compile(r"^[\d,]+\.\d+$")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
SIDES = {"買進": "buy", "買入": "buy", "賣出": "sell"}
MARKETS = {"美國": "US"}
OTHER_MARKETS = ("香港", "日本", "新加坡", "英國", "德國", "中國", "澳洲", "加拿大")
CURRENCIES = {"USD", "TWD", "HKD", "JPY", "CNY", "SGD", "GBP", "EUR", "AUD", "CAD"}


def _number(text):
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise StatementError("unrecognized", "number") from None


def open_pages(data, passwords):
    """Returns [(text, words, width)] per page, trying no password and then each configured one."""
    if not data.startswith(b"%PDF"):
        raise StatementError("not-statement")
    for password in ["", *passwords]:
        try:
            with pdfplumber.open(io.BytesIO(data), password=password) as pdf:
                return [(page.extract_text() or "", page.extract_words(), page.width) for page in pdf.pages]
        except Exception as error:  # noqa: BLE001 - pdfplumber wraps pdfminer's errors
            cause = error.args[0] if error.args else None
            if not isinstance(error, PDFPasswordIncorrect) and not isinstance(cause, PDFPasswordIncorrect):
                raise StatementError("not-statement") from None
    raise StatementError("password")


def detect_broker(text, sender=""):
    for broker, marks in BROKER_MARKS:
        if any(mark in text for mark in marks):
            return broker
    domain = sender.rsplit("@", 1)[-1].strip(" >").lower()
    for suffix, broker in SENDER_DOMAINS.items():
        if domain == suffix or domain.endswith("." + suffix):
            return broker
    return None


def _columns(numbers):
    """Groups numeric words into table columns by horizontal centre; each column top → bottom."""
    columns = []
    for x, top, value in sorted(numbers):
        if columns and x - columns[-1]["x"] < 12:
            columns[-1]["items"].append((top, value))
        else:
            columns.append({"x": x, "items": [(top, value)]})
    return [[value for _, value in sorted(column["items"])] for column in columns]


def _close(a, b, tolerance):
    return abs(a - b) <= tolerance


def _cathay_row(words, report_date):
    texts = [w["text"] for w in words]
    symbol = next((t.split("/", 1)[0] for t in texts if "/" in t and TICKER.match(t.split("/", 1)[0])), None)
    side = next((SIDES[t] for t in texts if t in SIDES), None)
    if any(t in OTHER_MARKETS for t in texts):
        raise StatementError("unsupported", "market")
    market = next((MARKETS[t] for t in texts if t in MARKETS), None)
    currencies = {t for t in texts if t in CURRENCIES}
    numbers = [((w["x0"] + w["x1"]) / 2, w["top"], _number(w["text"])) for w in words if NUMBER.match(w["text"])]
    columns = _columns(numbers)
    if not (symbol and side and market and currencies == {"USD"}) or [len(c) for c in columns] != [1, 2, 1, 1, 1, 2]:
        raise StatementError("unrecognized", "row")
    (quantity,), (price, rate), (amount,), (fee,), (tax,), (net, settled) = columns
    expected = amount - fee - tax if side == "sell" else amount + fee + tax
    if (
        quantity <= 0 or rate != 1
        or not _close(quantity * price, amount, max(Decimal("0.01"), amount * Decimal("0.0005")))
        or not _close(expected, net, Decimal("0.011"))
        or not _close(net, settled, Decimal("0.011"))
    ):
        raise StatementError("unrecognized", "arithmetic")
    reference = words[0]["text"]
    return Trade(
        key=f"{report_date}:{reference}", trade_date=report_date, market=market,
        symbol=re.sub(r"^([A-Z]{1,6})\.([A-Z]{1,2})$", r"\1-\2", symbol), side=side,
        quantity=quantity, price=price, amount=net, currency="USD",
    )


def parse_cathay(pages):
    text = "\n".join(page[0] for page in pages)
    compact = re.sub(r"\s+", "", text)
    if "客戶日買賣報告書" not in compact and "ClientDailyTradeReport" not in compact:
        raise StatementError("not-statement")
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日的買賣報告書", compact) or re.search(r"for(\d{4})/(\d{2})/(\d{2})", compact)
    if not match:
        raise StatementError("unrecognized", "date")
    report_date = f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}"
    hint = re.search(r"帳號\s*[:：]\s*([A-Z0-9*]{4,20})(?![A-Z0-9*])", text)
    sections = re.findall(r"([一-鿿]{2,8})交易明細", compact)
    if any(not section.endswith("海外股票") for section in sections):
        raise StatementError("unsupported", "section")
    trades = []
    for _, words, width in pages:
        stop = min((w["top"] for w in words if w["text"].startswith(("小計", "Subtotal"))), default=float("inf"))
        anchors = sorted((w for w in words if re.fullmatch(r"\d{8}", w["text"]) and w["x0"] < width * 0.2 and w["top"] < stop),
                         key=lambda w: w["top"])
        if not anchors:
            continue
        middles = [(a["top"] + a["bottom"]) / 2 for a in anchors]
        gaps = [b - a for a, b in zip(middles, middles[1:])]
        half = (statistics.median(gaps) if gaps else 24) / 2
        for index, anchor in enumerate(anchors):
            low = middles[index] - half
            high = min(middles[index] + half, stop)
            row = [anchor] + sorted((w for w in words if w is not anchor and low <= (w["top"] + w["bottom"]) / 2 < high),
                                    key=lambda w: w["x0"])
            trades.append(_cathay_row(row, report_date))
    if sections and not trades:
        raise StatementError("unrecognized", "rows")
    keys = [t.key for t in trades]
    if len(set(keys)) != len(keys):
        raise StatementError("unrecognized", "duplicate")
    subtotal = re.search(r"Subtotal[:：]?USD([\d,]+\.\d+)", compact)
    if subtotal and trades and len({t.side for t in trades}) == 1 and not _close(sum(t.amount for t in trades), _number(subtotal[1]), Decimal("0.011")):
        raise StatementError("unrecognized", "subtotal")
    return Statement("cathay", report_date, hint[1] if hint else "", tuple(trades))


PARSERS = {"cathay": parse_cathay}


def parse_statement(data, passwords=(), sender=""):
    pages = open_pages(data, passwords)
    broker = detect_broker("\n".join(page[0] for page in pages), sender)
    if broker is None:
        raise StatementError("not-statement")
    if broker not in PARSERS:
        raise StatementError("unsupported", "broker:" + broker)
    return PARSERS[broker](pages)
