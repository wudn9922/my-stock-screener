"""Publish Taiwan warrant terms by Atlas underlying symbol from exchange and MOPS releases."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import html
import json
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unicodedata
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from lxml import etree
from lxml import html as lxml_html


MASTERS = {
    "TWSE": "https://openapi.twse.com.tw/v1/opendata/t187ap37_L",
    "TPEx": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O",
}
ISSUE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_warrant_issue"
DIRECTORIES = {
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L": ("TW", "公司代號", "公司簡稱"),
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O": ("TWO", "SecuritiesCompanyCode", "CompanyAbbreviation"),
    "https://openapi.twse.com.tw/v1/opendata/t187ap47_L": ("TW", "基金代號", "基金簡稱"),
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes": ("TWO", "SecuritiesCompanyCode", "CompanyName"),
}
MOPS = "https://mopsov.twse.com.tw/mops/web"
CODE = re.compile(r"[0-9]{5}[0-9A-Z]")
TICKER = re.compile(r"[0-9]{4,6}[A-Z]?")
SYMBOL = re.compile(r"(?:[0-9]{4,6}[A-Z]?\.(?:TW|TWO)|\^[A-Z0-9]{1,12}|IX[0-9]{4})")
KINDS = {"認購": "call", "認售": "put"}
INDEX_NAMES = {"臺股指數": "^TWII", "台股指數": "^TWII", "櫃買指數": "^TWOII"}
MISSING = {"", "-", "--"}
OUTPUT = Path("docs/report/warrants")
DIRECTORY = Path("atlas/public/symbols/taiwan.json")


@dataclass(frozen=True)
class Warrant:
    market: str
    code: str
    name: str
    kind: str
    underlying_name: str
    strike_price: float | None
    exercise_ratio: float | None
    expiry: date
    last_trade: date

    @property
    def key(self):
        return self.market, self.code, self.name


def field(row, key):
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Missing or invalid {key}")
    return value.strip()


def exchange_date(text):
    if not re.fullmatch(r"[0-9]{7,8}", text):
        raise ValueError(f"Invalid exchange date: {text!r}")
    year = int(text[:-4]) + (1911 if len(text) == 7 else 0)
    return date(year, int(text[-4:-2]), int(text[-2:]))


def positive_decimal(text, divisor=1):
    if text in MISSING:
        return None
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text):
        raise ValueError(f"Invalid warrant price or ratio: {text!r}")
    value = Decimal(text.replace(",", "")) / divisor
    if value == 0:
        return None
    if not 0 < value < Decimal("1e12"):
        raise ValueError(f"Warrant price or ratio out of range: {text!r}")
    return float(value)


def release(payload, date_key, code_key, through):
    if not isinstance(payload, list) or not payload or any(not isinstance(row, dict) for row in payload):
        raise ValueError("Expected a non-empty exchange release array")
    dates = {exchange_date(field(row, date_key)) for row in payload}
    if len(dates) != 1 or max(dates) > through:
        raise ValueError("Exchange response has mixed or future release dates")
    records = {}
    for row in payload:
        code = field(row, code_key)
        if not CODE.fullmatch(code) or code in records:
            raise ValueError(f"Invalid or duplicate warrant code: {code!r}")
        records[code] = row
    return dates.pop(), records


def parse_master(payload, market, through):
    source_date, rows = release(payload, "出表日期", "權證代號", through)
    warrants = []
    for code, row in rows.items():
        last_trade = exchange_date(field(row, "最後交易日"))
        expiry = exchange_date(field(row, "履約截止日"))
        if last_trade > expiry:
            raise ValueError(f"{code} has a last trading date after expiry")
        if last_trade < through:
            continue
        kind = KINDS.get(field(row, "權證類型"))
        if kind is None:
            continue
        name = field(row, "權證簡稱")
        underlying = field(row, "標的證券/指數")
        if not name or not underlying:
            raise ValueError(f"{code} is missing its name or underlying")
        warrants.append(Warrant(
            market, code, name, kind, underlying,
            positive_decimal(field(row, "最新履約價格(元)/履約指數")),
            positive_decimal(field(row, "最新標的履約配發數量(每仟單位權證)"), 1000),
            expiry, last_trade,
        ))
    if not warrants:
        raise ValueError(f"{market} release contains no supported warrants through {through}")
    return source_date.isoformat(), warrants


def normalize_name(name):
    name = unicodedata.normalize("NFKC", html.unescape(name)).strip()
    return re.sub(r"\(原名:[^()]+\)$", "", name).strip()


def option_text(option):
    return " ".join(option.text_content().split())


def parse_issuer_names(body):
    options = lxml_html.fromstring(body).xpath("//select[@name='co_id']/option[@value]")
    issuers = {option.get("value"): option_text(option) for option in options}
    if not issuers or any(not re.fullmatch(r"[0-9A-Z]{4}", code) or not name for code, name in issuers.items()):
        raise ValueError("Unexpected MOPS issuer selector")
    return issuers


def parse_issuer_options(body, issuer_code, warrant_class):
    if "</html>" not in body.lower():
        raise ValueError("Incomplete MOPS response")
    doc = lxml_html.fromstring(body)
    def selected(name):
        return doc.xpath(f"//select[@name='{name}']/option[@selected]/@value")
    if selected("co_id") != [issuer_code] or selected("warrant_class") != [warrant_class]:
        raise ValueError("MOPS returned a different issuer or warrant class")
    result = {}
    for field_name in ("warrant_id", "stock_no"):
        selects = doc.xpath(f"//select[@name='{field_name}']")
        if len(selects) != 1:
            raise ValueError(f"Missing MOPS {field_name} selector")
        result[field_name] = [
            (option.get("value").strip(), option_text(option))
            for option in selects[0].xpath("./option[@value]")
            if option.get("value").strip() and option_text(option)
        ]
    return result["warrant_id"], result["stock_no"]


def parse_detail(body, code, underlying_name):
    doc = lxml_html.fromstring(body)
    fields = {}
    underlying = []
    for table in doc.xpath("//table[not(.//table)]"):
        rows = [[option_text(cell) for cell in tr.xpath("./td|./th")] for tr in table.xpath(".//tr")]
        for i, cells in enumerate(rows):
            if len(cells) == 2 and cells[0] in ("權證代號", "申請機構名稱"):
                fields[cells[0]] = cells[1]
            if cells and cells[0] == "標的代號":
                underlying = [row for row in rows[i + 1:] if len(row) >= 2]
    if fields.get("權證代號") != code or not fields.get("申請機構名稱"):
        raise ValueError("MOPS record does not match the requested warrant")
    ticker = underlying[0][0] if len(underlying) == 1 and normalize_name(underlying[0][1]) == normalize_name(underlying_name) else None
    return fields["申請機構名稱"], ticker


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, params=None, *, timeout=45, max_bytes=64 * 1024 * 1024):
    request = Request(url, data=urlencode(params).encode() if params is not None else None,
                      headers={"User-Agent": "Atlas Warrant Reference Data", "Accept": "application/json, text/html"})
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
    if not body or len(body) > max_bytes:
        raise ValueError("Exchange response is empty or exceeds the size limit")
    return body.decode("utf-8-sig")


def fetch_mops_options(item):
    issuer_code, warrant_class = item
    params = {
        "step": "2", "firstin": "true", "TYPEK": "", "co_id": issuer_code,
        "warrant_class": warrant_class, "publish_date": "", "close_date": "",
        "warrant_id": "", "stock_no": "",
    }
    body = fetch(f"{MOPS}/ajax_t110sb02", params, max_bytes=4 * 1024 * 1024)
    return parse_issuer_options(body, issuer_code, warrant_class)


def fetch_detail(warrant):
    market, form = ("sii", "q1") if warrant.market == "TWSE" else ("otc", "q2")
    params = {
        "step": "1", "firstin": "1", "off": "1", "keyword4": "", "TYPEK2": market,
        "queryName": "GOOD_ID", "inpuType": "co_id", "TYPEK": "all", "GOOD_ID": warrant.code,
    }
    body = fetch(f"{MOPS}/ajax_t05st48_{form}", params, timeout=15, max_bytes=1_000_000)
    if not lxml_html.fromstring(body).xpath("//form[@name='autoForm' and @action='/mops/web/ajax_t05st48']"):
        raise ValueError("MOPS detail form was not returned")
    detail = fetch(f"{MOPS}/ajax_t05st48", {
        "run": "", "step": "1", "TYPEK": market, "GOOD_ID": warrant.code, "firstin": "true", "off": "1",
    }, timeout=15, max_bytes=1_000_000)
    return parse_detail(detail, warrant.code, warrant.underlying_name)


class Underlyings:
    def __init__(self, directory):
        self.names = defaultdict(set)
        self.codes = defaultdict(set)
        for row in directory["entries"]:
            name, symbol, code = row["name"], row["symbol"], row["ticker"]
            if not SYMBOL.fullmatch(symbol):
                continue
            self.names[normalize_name(name)].add(symbol)
            self.codes[code].add(symbol)

    def add_official_directory(self, payload, suffix, code_key, name_key, wanted):
        if not isinstance(payload, list) or not payload or any(not isinstance(row, dict) for row in payload):
            raise ValueError("Unexpected official underlying directory")
        official = {}
        for row in payload:
            ticker, name = field(row, code_key), field(row, name_key)
            if TICKER.fullmatch(ticker) and name:
                official[ticker] = f"{ticker}.{suffix}", normalize_name(name)
        if not official:
            raise ValueError("Official directory contains no underlying codes")
        for ticker, (symbol, name) in official.items():
            if name in wanted or ticker in self.codes:
                self.names[name].add(symbol)
                self.codes[ticker].add(symbol)

    def resolve(self, name, code=None):
        original = normalize_name(name)
        if original in INDEX_NAMES:
            return INDEX_NAMES[original]
        candidates = self.names[original]
        if code is not None and re.fullmatch(r"IX[0-9]{4}", code):
            return code
        if code is not None and TICKER.fullmatch(code):
            by_code = self.codes[code]
            same_name = candidates & by_code
            if len(same_name) == 1:
                return next(iter(same_name))
            if len(by_code) == 1 and not candidates:
                return next(iter(by_code))
            return None
        return next(iter(candidates)) if len(candidates) == 1 else None


def read_previous(output, as_of):
    identities = {}
    for path in sorted(output.glob("*.json")):
        previous = json.loads(path.read_text())
        if previous["version"] != 1 or previous["underlying"] != path.stem or not SYMBOL.fullmatch(path.stem):
            raise ValueError(f"Invalid previous warrant file: {path.name}")
        for market, source_date in as_of.items():
            if previous["asOf"][market] > source_date:
                raise ValueError(f"{market} release would move backwards")
        for row in previous["warrants"]:
            identities[row["market"], row["code"], row["name"]] = (path.stem, row["issuer"])
    return identities


def documents(warrants, as_of, through, issues, underlyings, issuers, previous, details=None):
    groups = defaultdict(list)
    unknown_underlyings = set()
    details = details or {}
    for warrant in warrants:
        issue = issues.get(warrant.code) if warrant.market == "TPEx" else None
        code = None
        if issue and normalize_name(field(issue, "Name")) == normalize_name(warrant.name):
            if exchange_date(field(issue, "ListedDate")) > through:
                continue
            code = field(issue, "UnderlyingStockCode")
        previous_symbol, previous_issuer = previous.get(warrant.key, (None, None))
        detail_issuer, detail_code = details.get(warrant.key, (None, None))
        official_code = detail_code or code
        underlying = underlyings.resolve(warrant.underlying_name, official_code)
        if not underlying and (not official_code or previous_symbol and previous_symbol.split('.')[0] == official_code):
            underlying = previous_symbol
        if not underlying:
            unknown_underlyings.add(warrant.underlying_name)
            continue
        issuer_matches = issuers.get((warrant.code, normalize_name(warrant.name)), set())
        issuer = detail_issuer or (next(iter(issuer_matches)) if len(issuer_matches) == 1 else None)
        if not issuer_matches and not issuer:
            issuer = previous_issuer
        suffix = "TW" if warrant.market == "TWSE" else "TWO"
        groups[underlying].append({
            "code": warrant.code, "symbol": f"{warrant.code}.{suffix}", "name": warrant.name,
            "type": warrant.kind, "strikePrice": warrant.strike_price, "exerciseRatio": warrant.exercise_ratio,
            "expiryDate": warrant.expiry.isoformat(), "lastTradingDate": warrant.last_trade.isoformat(),
            "issuer": issuer, "market": warrant.market,
        })
    result = {
        symbol: {"version": 1, "underlying": symbol, "asOf": as_of,
                 "warrants": sorted(rows, key=lambda row: (row["market"], row["code"]))}
        for symbol, rows in groups.items()
    }
    return result, sorted(unknown_underlyings)


def write_documents(output, contents):
    if not contents or any(not SYMBOL.fullmatch(symbol) for symbol in contents):
        raise ValueError("Refusing to publish an empty or invalid warrant catalogue")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        staged = Path(tmp)
        for symbol, body in contents.items():
            (staged / f"{symbol}.json").write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.mkdir(exist_ok=True)
        for path in staged.iterdir():
            path.replace(output / path.name)
        for path in output.glob("*.json"):
            if path.stem not in contents:
                path.unlink()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=OUTPUT)
    args = cli.parse_args()
    through = datetime.now(ZoneInfo("Asia/Taipei")).date()
    as_of, warrants = {}, []
    for market, url in MASTERS.items():
        as_of[market], rows = parse_master(json.loads(fetch(url)), market, through)
        if len(rows) < 1000:
            raise ValueError(f"{market} master shrank to {len(rows)} current warrants")
        warrants.extend(rows)
    _, issues = release(json.loads(fetch(ISSUE_URL)), "Date", "Code", through)
    underlyings = Underlyings(json.loads(DIRECTORY.read_text()))
    wanted = {normalize_name(row.underlying_name) for row in warrants}
    for url, (suffix, code_key, name_key) in DIRECTORIES.items():
        try:
            underlyings.add_official_directory(json.loads(fetch(url)), suffix, code_key, name_key, wanted)
        except (OSError, ValueError) as exc:
            print(f"Official directory {suffix}: {exc}. Using checked-in directory for this source.", file=sys.stderr)
    previous = read_previous(args.output, as_of)
    issuers = defaultdict(set)
    issuer_names = parse_issuer_names(fetch(f"{MOPS}/t110sb02", max_bytes=1_000_000))
    # MOPS exposes the warrant selector per issuer and type, so one request covers many codes.
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {
            pool.submit(fetch_mops_options, (code, kind)): (code, name)
            for code, name in issuer_names.items() for kind in ("1", "2")
        }
        for task, (code, name) in tasks.items():
            try:
                options, stocks = task.result()
            except (OSError, ValueError, etree.LxmlError) as exc:
                print(f"MOPS issuer {code}: {exc}", file=sys.stderr)
                continue
            for key, label in options:
                if CODE.fullmatch(key):
                    issuers[key, normalize_name(label)].add(name)
            for ticker, label in stocks:
                symbol = underlyings.resolve(label, ticker)
                if symbol:
                    underlyings.names[normalize_name(label)].add(symbol)
    if not issuers:
        raise ValueError("MOPS returned no warrant issuer identities")
    details = {}
    # A bounded supplemental lookup covers display names missing from the directory.
    candidates = {}
    for row in warrants:
        if row.key not in previous and not underlyings.resolve(row.underlying_name):
            candidates.setdefault(normalize_name(row.underlying_name), row)
    supplemental = list(candidates.values())
    picked = {row.key for row in supplemental}
    for row in warrants:
        if len(supplemental) >= 24:
            break
        known_issuer = previous.get(row.key, (None, None))[1] or issuers.get((row.code, normalize_name(row.name)))
        if row.key not in picked and not known_issuer:
            supplemental.append(row)
    for row in supplemental[:24]:
        try:
            issuer, ticker = fetch_detail(row)
            details[row.key] = (issuer, ticker)
            if ticker:
                symbol = underlyings.resolve(row.underlying_name, ticker)
                if symbol:
                    underlyings.names[normalize_name(row.underlying_name)].add(symbol)
        except (OSError, ValueError, etree.LxmlError) as exc:
            print(f"MOPS warrant {row.code}: {exc}", file=sys.stderr)
    contents, missing = documents(warrants, as_of, through, issues, underlyings, issuers, previous, details)
    write_documents(args.output, contents)
    rows = [row for doc in contents.values() for row in doc["warrants"]]
    print(f"Published {len(rows)} warrants across {len(contents)} underlyings. Missing issuer: "
          f"{sum(row['issuer'] is None for row in rows)}. Unmapped underlyings: {missing}")


if __name__ == "__main__":
    main()
