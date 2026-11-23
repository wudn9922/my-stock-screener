"""Broker e-mail statement (PDF) parsing.

Deliberately strict: every trade row must pass the report's own arithmetic (quantity × price ≈
amount; amount ± fee ± tax = net), and anything unexpected raises StatementError so that
nothing is written. Positions, prices and names never appear in errors or logs.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import io
import json
import os
from pathlib import Path
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
    # Full long position list printed by the broker after the day's trades: {"market", "positions": {symbol: qty}}.
    snapshot: dict | None = None


BROKER_MARKS = (("cathay", ("國泰綜合證券", "Cathay Securities")), ("sinopac", ("永豐金", "SinoPac", "sinotrade")), ("kgi", ("凱基證券", "KGI")))
SENDER_DOMAINS = {"cathaysec.com.tw": "cathay", "sinopac.com": "sinopac", "sinotrade.com.tw": "sinopac",
                  "kgi.com": "kgi", "kgi.com.tw": "kgi"}
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


INTEGER = re.compile(r"^[\d,]+(\.\d+)?$")
TW_SIDES = {"現買": "buy", "現賣": "sell"}
TW_UNSUPPORTED = ("資買", "資賣", "券買", "券賣", "資券")
_CATALOG = None


def tw_catalog():
    """Ticker → Yahoo symbol from the site's official TWSE/TPEx directory (stocks and ETFs)."""
    global _CATALOG
    if _CATALOG is None:
        path = Path(os.environ.get("STATEMENT_TW_CATALOG") or Path(__file__).resolve().parents[1] / "atlas/public/symbols/taiwan.json")
        entries = json.loads(path.read_text())["entries"]
        _CATALOG = {e["ticker"]: e["symbol"] for e in entries if re.fullmatch(r"\d{4,6}[A-Z]?\.(TW|TWO)", e.get("symbol", ""))}
    return _CATALOG


def tw_symbol(code):
    if code in tw_catalog():
        return tw_catalog()[code]
    if re.fullmatch(r"0[1-9]\d{4}", code):  # TWSE-listed warrant
        return code + ".TW"
    if re.fullmatch(r"7\d{5}", code):  # TPEx warrant
        return code + ".TWO"
    raise StatementError("unrecognized", "symbol")


def _bands(words, anchors, stop, default):
    middles = [(a["top"] + a["bottom"]) / 2 for a in anchors]
    gaps = [b - a for a, b in zip(middles, middles[1:])]
    half = (statistics.median(gaps) if gaps else default) / 2
    for index, anchor in enumerate(anchors):
        low, high = middles[index] - half, min(middles[index] + half, stop)
        yield anchor, sorted((w for w in words if w is not anchor and low <= (w["top"] + w["bottom"]) / 2 < high), key=lambda w: w["x0"])


def _sinopac_trade(anchor, row, report_date):
    texts = [w["text"] for w in row]
    if any(t.startswith(TW_UNSUPPORTED) for t in texts):
        raise StatementError("unsupported", "margin")
    side_word = next((w for w in row if w["text"] in TW_SIDES), None)
    code = next((m[1] for t in texts for m in [re.search(r"\((\d{4,6}[A-Z]?)\)", t)] if m), None)
    order = next((w["text"] for w in row if w["x0"] > anchor["x1"] and not INTEGER.match(w["text"])), None)
    seq = next((w["text"] for w in reversed(row) if w["x1"] < anchor["x0"] and re.fullmatch(r"\d{1,4}", w["text"])), "")
    if not (side_word and code and order):
        raise StatementError("unrecognized", "row")
    numbers = [_number(w["text"]) for w in row if w["x0"] > side_word["x1"] and INTEGER.match(w["text"])]
    if len(numbers) != 8:
        raise StatementError("unrecognized", "row")
    quantity, price, loan, net, fee, tax, interest, lending = numbers
    side = TW_SIDES[side_word["text"]]
    gross = (quantity * price).quantize(Decimal("1"))
    expected = gross + fee + tax if side == "buy" else gross - fee - tax
    if quantity <= 0 or quantity != quantity.to_integral_value() or loan or interest or lending or not _close(expected, net, Decimal("1")):
        raise StatementError("unrecognized", "arithmetic")
    return Trade(key=f"{report_date}:{order}:{seq}", trade_date=report_date, market="TW", symbol=tw_symbol(code),
                 side=side, quantity=quantity, price=price, amount=net, currency="TWD"), gross


def parse_sinopac(pages):
    text = "\n".join(page[0] for page in pages)
    compact = re.sub(r"\s+", "", text)
    if "預估收付" not in compact and "庫存明細" not in compact:
        raise StatementError("not-statement")
    if "幣別" in compact and not re.search(r"幣別[:：]台幣", compact):
        raise StatementError("unsupported", "currency")
    inventory_date = re.search(r"庫存明細到(\d{4})/(\d{2})/(\d{2})", compact)
    hint = re.search(r"證券帳號\s*[:：]\s*([0-9A-Z*][0-9A-Z*\-]{4,20})", text)
    trades, totals, positions, dates = [], {"buy": Decimal(0), "sell": Decimal(0)}, {}, set()
    for _, words, width in pages:
        inventory_top = min((w["top"] for w in words if w["text"].startswith("庫存明細")), default=float("inf"))
        totals_top = min((w["top"] for w in words if "總計" in w["text"]), default=inventory_top)
        anchors = sorted((w for w in words if re.fullmatch(r"20\d{6}", w["text"]) and w["x0"] < width * 0.3 and w["top"] < totals_top),
                         key=lambda w: w["top"])
        for anchor, row in _bands(words, anchors, totals_top, 24):
            day = f"{anchor['text'][:4]}-{anchor['text'][4:6]}-{anchor['text'][6:]}"
            dates.add(day)
            trade, gross = _sinopac_trade(anchor, row, day)
            trades.append(trade)
            totals[trade.side] += gross
        stock_rows = sorted((w for w in words if w["top"] > inventory_top and w["x0"] < width * 0.12
                             and re.fullmatch(r"\d{4,6}[A-Z]?", w["text"])), key=lambda w: w["top"])
        for anchor, row in _bands(words, stock_rows, float("inf"), 10):
            code = anchor["text"]
            values = [_number(w["text"]) for w in row if INTEGER.match(w["text"]) and w["x0"] > anchor["x1"]]
            if len(values) < 3:
                raise StatementError("unrecognized", "inventory")
            held, margin, _short = values[-3:]
            if held + margin > 0:
                symbol = tw_symbol(code)
                if symbol in positions:
                    raise StatementError("unrecognized", "duplicate")
                positions[symbol] = held + margin
    if inventory_date:
        report_date = f"{inventory_date[1]}-{inventory_date[2]}-{inventory_date[3]}"
        dates.add(report_date)
    if len(dates) != 1:
        raise StatementError("unrecognized", "date")
    keys = [t.key for t in trades]
    if len(set(keys)) != len(keys):
        raise StatementError("unrecognized", "duplicate")
    for side, label in (("buy", "買進總計"), ("sell", "賣出總計")):
        match = re.search(label + r"[:：]\$?([\d,]+)", compact)
        if match and _number(match[1]) != totals[side]:
            raise StatementError("unrecognized", "total")
    if not trades and not inventory_date:
        raise StatementError("unrecognized", "rows")
    report_date = dates.pop()
    snapshot = {"market": "TW", "positions": positions} if inventory_date else None
    return Statement("sinopac", report_date, hint[1] if hint else "", tuple(trades), snapshot)


PARSERS = {"cathay": parse_cathay, "sinopac": parse_sinopac}


def parse_statement(data, passwords=(), sender=""):
    pages = open_pages(data, passwords)
    broker = detect_broker("\n".join(page[0] for page in pages), sender)
    if broker is None:
        raise StatementError("not-statement")
    if broker not in PARSERS:
        raise StatementError("unsupported", "broker:" + broker)
    return PARSERS[broker](pages)
