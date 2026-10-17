#!/usr/bin/env python3
"""
Q4 EPS from the 8-K earnings release (EX-99.1) of US issuers, for the Q4 periods that have none.

A table is used only when it agrees with what the file already holds for that quarter:
  - exactly one column shows the stored Q4 net income, after a unit scale of 1, 1e3, 1e6 or 1e9
    (within NI_TOLERANCE);
  - the diluted EPS in that column has the sign of net income;
  - when the table shows diluted weighted shares: |net income / shares - EPS| <= max(5% of |EPS|, 0.02).
Tables from one filing must agree on diluted EPS.

Modes:
  backtest  run the same extraction on Q4 periods that already hold a diluted EPS and report the
            errors; writes nothing.
  diagnose  inspect filings and tables for the no-table rows in a prior report; writes per-row
            diagnostic JSON to stdout and does not write files.
  fill      write accepted values into null fields only (never overwrites) and record the source of
            each Q4 in q4EpsSources.

SEC access: User-Agent from SEC_USER_AGENT (or the repo default), at most about 5 requests per second,
stop at the first HTTP 403, and --budget requests per run. docs/report/fundamentals/q4-8k-state.json
records finished tickers so the next run continues where this one stopped.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib import error, request

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
import seed_financials_sec as seed  # noqa: E402

REPO = os.path.dirname(os.path.dirname(SCRIPTS))
FIN_DIR = os.path.join(REPO, "docs", "report", "financials")
STATE_PATH = os.path.join(REPO, "docs", "report", "fundamentals", "q4-8k-state.json")
DIAGNOSE_SOURCE = os.path.join(REPO, "docs", "report", "q4-8k", "input", "run-17", "q4-8k-report.json")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"

SCALES = (1, 1e3, 1e6, 1e9)
NI_TOLERANCE = 0.002       # relative: stored Q4 net income against the table (plus half a unit of its scale)
SHARES_TOLERANCE = 0.05    # relative: net income / shares against EPS (absolute floor EPS_FLOOR)
EPS_FLOOR = 0.02
FILING_DAYS = (15, 150)    # an 8-K with Item 2.02 this many days after the quarter end
MAX_FILINGS = 3
MAX_EXHIBITS = 4
EXTRA_DOCS = 2             # other .htm documents of the filing, read only when no EX-99 table shows the quarter
REQUEST_INTERVAL = 0.2     # seconds between SEC requests
RETRIES = 3
DASHES = ("", "-", "--", "—", "–")

NI_RE = re.compile(r"net\s*(\(loss\)\s*|loss\s*)?(income|earnings)|net\s+loss")
# non-GAAP and adjusted rows sit next to the GAAP rows in most releases and must never be picked
NON_GAAP = ("non-gaap", "non gaap", "nongaap", "adjusted", "excluding", "pro forma")
NI_AVOID = ("per share", "noncontrolling", "non-controlling", "minority", "continuing", "discontinued",
            "before", "comprehensive") + NON_GAAP
EPS_AVOID = ("weighted", "shares", "number of", "average", "continuing") + NON_GAAP   # total EPS, like the net income row


class Forbidden(RuntimeError):
    """SEC answered HTTP 403: the run stops at once."""


class OutOfBudget(RuntimeError):
    """The --budget of SEC requests is used up: the run pauses and the next run continues."""


class NetworkError(RuntimeError):
    """SEC could not be reached after the retries."""


class _TableParser(HTMLParser):
    """Rows of cell texts for every table of an HTML document (no nested tables)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._table, self._row, self._cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def tables_of(text):
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    return parser.tables


class _RawTableParser(HTMLParser):
    """Capture the source markup for the same table sequence returned by tables_of."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tables = []
        self._table = None

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        if tag == "table":
            # Match _TableParser's behavior for nested tables: the nested table replaces its parent.
            self._table = [raw]
        elif self._table is not None:
            self._table.append(raw)

    def handle_startendtag(self, tag, attrs):
        raw = self.get_starttag_text()
        if tag == "table":
            self._table = [raw]
            self.tables.append("".join(self._table))
            self._table = None
        elif self._table is not None:
            self._table.append(raw)

    def handle_endtag(self, tag):
        if self._table is None:
            return
        self._table.append(f"</{tag}>")
        if tag == "table":
            self.tables.append("".join(self._table))
            self._table = None

    def handle_data(self, data):
        if self._table is not None:
            self._table.append(data)

    def handle_entityref(self, name):
        if self._table is not None:
            self._table.append(f"&{name};")

    def handle_charref(self, name):
        if self._table is not None:
            self._table.append(f"&#{name};")

    def handle_comment(self, data):
        if self._table is not None:
            self._table.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        if self._table is not None:
            self._table.append(f"<!{decl}>")


def html_tables_of(text):
    """Return trimmed source <table> fragments in the same order as tables_of."""
    parser = _RawTableParser()
    parser.feed(text)
    parser.close()
    return [table.strip() for table in parser.tables]


def number(text):
    """'1,234' → 1234, '(567' or '(567)' → -567, '$ 0.25' → 0.25, dashes and words → None."""
    compact = re.sub(r"[\s$,]", "", text or "")
    if compact in DASHES:
        return None
    negative = compact[:1] in ("(", "-", "−")
    digits = compact.strip("()-−")
    if not re.fullmatch(r"\d+(\.\d+)?", digits):
        return None
    value = float(digits)
    return -value if negative else value


def row_of(cells):
    """(label, numeric values in column order; None where a cell is a dash) of one table row."""
    label, values = None, []
    for cell in cells:
        text = cell.strip()
        if text in ("", "$", "(", ")", "%"):
            continue
        if label is None and number(text) is None and re.search(r"[A-Za-z]", text):
            label = text
        elif label is not None:
            values.append(number(text))
    return label, values


def is_net_income(label):
    return bool(NI_RE.search(label)) and not any(word in label for word in NI_AVOID)


def is_eps(label, kind):
    return (kind in label and ("per share" in label or "eps" in label or "earnings per" in label)
            and not any(word in label for word in EPS_AVOID))


def is_diluted_shares(label):
    return "diluted" in label and ("weighted" in label or "shares" in label) and "per share" not in label


def _pick(rows, predicate):
    for label, values in rows:
        if predicate(label):
            return values
    return None


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
QUARTER_WORDING = re.compile(
    r"\b(?:three\s+months|3\s+months|quarter|fourth\s+quarter|q4|thirteen\s+weeks|fourteen\s+weeks|13\s+weeks|14\s+weeks)\b",
    re.I,
)
YEAR_WORDING = re.compile(r"\b(?:twelve\s+months|year\s+ended|fiscal\s+year|full\s+year|year\s+to\s+date|ytd)\b", re.I)
NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9])\(?\s*\$?\s*-?\d[\d,]*(?:\.\d+)?\s*\)?(?![A-Za-z0-9])")


def target_matches(value, target):
    """Return the (column value, scale) matches used by table_eps for one reported number."""
    if value is None or target is None:
        return []
    return [scale for scale in SCALES
            if abs(value * scale - target) <= max(NI_TOLERANCE * abs(target), 0.5 * scale)]


def table_is_nonempty(table):
    return any(cell.strip() for row in table for cell in row)


def short_table_rows(table, count=3):
    return [[cell[:240] for cell in row] for row in table[:count]]


def shows_period(text, end):
    """The table names the quarter's month and year, e.g. 'Three Months Ended January 31, 2026'."""
    when = date.fromisoformat(end)
    name = MONTHS[when.month - 1]
    pattern = rf"\b(?:{name}|{name[:3]})\.?\s+\d{{1,2}},?\s+{when.year}\b"
    return re.search(pattern, text, re.I) is not None


def shows_period_by_column(table, end):
    """A period header split over rows (months and days on one row, the year on the next) is read column by column."""
    width = max((len(row) for row in table), default=0)
    for j in range(width):
        column = " ".join(row[j] for row in table[:4] if j < len(row) and row[j])
        if shows_period(column, end):
            return True
    return False


def period_named(table, end):
    return shows_period(" ".join(" ".join(row) for row in table), end) or shows_period_by_column(table, end)


def split_factor(doc, filing_date):
    """Product of the split ratios dated after the filing: the stored EPS and shares are on today's basis."""
    factor = 1.0
    for event in (doc.get("split") or {}).get("events") or []:
        if isinstance(event, (list, tuple)) and len(event) >= 2:
            try:
                when, ratio = str(event[0]), float(event[1])
            except (TypeError, ValueError):
                continue
            if ratio > 0 and when > filing_date:
                factor *= ratio
    return factor


def table_eps(table, target, end):
    """
    One table → (result dict, None), (None, reason) when it is the quarter but fails a check, or (None, None)
    when it does not show this quarter's net income at all.
    """
    if not period_named(table, end):
        return None, None
    rows, heading_left = [], 0
    for cells in table:
        label, values = row_of(cells)
        if not label:
            continue
        low = label.lower()
        if "per share" in low and all(value is None for value in values):
            heading_left = 2                # "Net income (loss) per share:" heading; its Basic / Diluted rows follow
            continue
        if heading_left:
            heading_left -= 1
            # only the two rows under the heading, and never share counts (per-share values are far below 1000)
            if re.fullmatch(r"(basic|diluted)[\s:]*", low) and all(value is None or abs(value) < 1000 for value in values):
                low = f"{low.split()[0]} earnings per share"
        rows.append((low, values))
    eps_label, eps_row = next(((label, values) for label, values in rows if is_eps(label, "diluted")),
                              (None, None))
    if eps_row is None:
        return None, None
    hits = []                       # (column, unit scale, net values, net label) over every net income row
    for label, values in rows:
        if not is_net_income(label):
            continue
        for j, value in enumerate(values):
            for scale in SCALES:
                if value is not None and abs(value * scale - target) <= max(NI_TOLERANCE * abs(target), 0.5 * scale):
                    hits.append((j, scale, values, label))
    if not hits:
        return None, None
    if len({(j, scale) for j, scale, _, _ in hits}) != 1:
        return None, "net income matches more than one column or unit"
    j, scale, net_row, net_label = hits[0]
    if len(net_row) != len(eps_row):
        return None, "net income and EPS rows have different column counts"
    eps = eps_row[j]
    if eps is None:
        return None, "EPS is empty in the matched column"
    if eps == 0:
        return None, "EPS is zero"
    if abs(eps) >= 1000:
        return None, "EPS is out of range (a share count?)"
    net = net_row[j] * scale
    if (eps > 0) != (net > 0):
        return None, "EPS sign differs from net income"
    shares_row = _pick(rows, is_diluted_shares)
    diluted_shares = None
    if shares_row is not None and len(shares_row) == len(net_row) and shares_row[j]:
        if shares_row[j] <= 0:
            return None, "shares are not positive"
        implied = net_row[j] / shares_row[j]          # both in the table's own unit, so the scale cancels
        if abs(implied - eps) > max(SHARES_TOLERANCE * abs(eps), EPS_FLOOR):
            return None, "net income divided by shares differs from EPS"
        diluted_shares = shares_row[j] * scale
    basic_row = _pick(rows, lambda label: is_eps(label, "basic"))
    basic = basic_row[j] if basic_row is not None and len(basic_row) == len(net_row) else None
    per_share_rows = [[label, values] for label, values in rows if "per share" in label or "earnings per" in label]
    return {"epsDiluted": eps, "epsBasic": basic, "dilutedShares": diluted_shares, "scale": scale,
            "net": net, "netLabel": net_label, "epsLabel": eps_label, "perShareRows": per_share_rows[:8]}, None


class Sec:
    """Minimal SEC client: identified User-Agent, pacing, 403 stops the run, budget of requests."""

    def __init__(self, user_agent, budget=None, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.user_agent = user_agent
        self.budget = budget
        self.opener = opener or self._open
        self.sleep = sleep
        self.clock = clock
        self.requests = 0
        self._last = None

    @staticmethod
    def _open(req):
        with request.urlopen(req, timeout=30) as response:
            return response.read()

    def get(self, url):
        """Body bytes, or None for 404."""
        if self.budget is not None and self.requests >= self.budget:
            raise OutOfBudget(url)
        if self._last is not None:
            wait = REQUEST_INTERVAL - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        req = request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"})
        for attempt in range(RETRIES):
            self.requests += 1
            self._last = self.clock()
            try:
                return self.opener(req)
            except error.HTTPError as exc:
                if exc.code == 403:
                    raise Forbidden(url) from exc
                if exc.code == 404:
                    return None
                if exc.code == 429 or exc.code >= 500:
                    self.sleep(2 * (attempt + 1))
                    continue
                raise
            except (error.URLError, TimeoutError, ConnectionError):
                self.sleep(2 * (attempt + 1))
        raise NetworkError(url)

    def get_json(self, url):
        body = self.get(url)
        return None if body is None else json.loads(body.decode("utf-8", "replace"))

    def get_text(self, url):
        body = self.get(url)
        return None if body is None else body.decode("utf-8", "replace")


def filing_rows(sec, cik):
    """Every filing of a CIK (recent block plus the older submission pages), newest first."""
    main = sec.get_json(SUBMISSIONS_URL.format(cik=int(cik)))
    if not main:
        return []
    filings = main.get("filings") or {}
    blocks = [filings.get("recent") or {}]
    for page in filings.get("files") or []:
        block = sec.get_json("https://data.sec.gov/submissions/" + page["name"])
        if block:
            blocks.append(block)
    rows = []
    for block in blocks:
        count = len(block.get("form") or [])
        for i in range(count):
            rows.append({name: (block.get(name) or [""] * count)[i]
                         for name in ("form", "filingDate", "accessionNumber", "items")})
    return rows


def candidate_filings(rows, fiscal_end):
    """8-K filings with Item 2.02 (results of operations) inside FILING_DAYS after the quarter end, nearest first."""
    picked = []
    for row in rows:
        if row["form"] != "8-K" or "2.02" not in (row["items"] or ""):
            continue
        try:
            days = (date.fromisoformat(row["filingDate"]) - fiscal_end).days
        except ValueError:
            continue
        if FILING_DAYS[0] <= days <= FILING_DAYS[1]:
            picked.append((days, row))
    return [row for _, row in sorted(picked, key=lambda item: item[0])]


def is_release_exhibit(item):
    name = item.get("name") or ""
    kind = (item.get("type") or "").upper()
    return name.lower().endswith((".htm", ".html")) and (
        kind.startswith("EX-99") or re.search(r"(ex|exhibit)[-_ ]?99", name, re.I) is not None)


def index_exhibits(page):
    """EX-99 document names from the Type column of a filing's index page (the file names do not say it)."""
    names = []
    for table in tables_of(page or ""):
        for row in table:
            cells = [cell.strip() for cell in row]
            kind = any(re.fullmatch(r"EX-99(\.\d+)?", cell, re.I) for cell in cells)
            name = next((cell for cell in cells if re.fullmatch(r"[\w.-]+\.html?", cell, re.I)), None)
            if kind and name and name not in names:
                names.append(name)
    return names


def from_filing(sec, cik, filing, target, end, debug=None):
    accession = filing["accessionNumber"]
    base = ARCHIVE_URL.format(cik=int(cik), folder=accession.replace("-", ""))
    index = sec.get_json(base + "index.json")
    if index is None:
        return None, "filing index not available"
    items = (index.get("directory") or {}).get("item") or []
    page = sec.get_text(base + accession + "-index.html") or sec.get_text(base + accession + "-index.htm")
    by_name = [item["name"] for item in items if is_release_exhibit(item)]
    exhibits = list(dict.fromkeys(index_exhibits(page) + by_name))[:MAX_EXHIBITS]
    # when no EX-99 table shows the quarter, the release may sit in another document of the same filing
    extra = [item["name"] for item in items if (item.get("name") or "").lower().endswith((".htm", ".html"))
             and item["name"] not in exhibits][:EXTRA_DOCS]
    if not exhibits and not extra:
        if debug is not None:
            debug.append({"index_items": [item.get("name") for item in items][:12]})
        return None, "no EX-99 exhibit in the filing"
    found, shown = [], []
    orphans = {"net_tables_without_period": 0, "period_in_previous_table": 0, "sample": []}
    reason = "no table shows this quarter's net income"
    for position, name in enumerate(exhibits + extra):
        if position >= len(exhibits) and found:
            break
        url = base + name
        text = sec.get_text(url)
        if text is None:
            continue
        previous = None
        for table in tables_of(text):
            result, why = table_eps(table, target, end)
            if result is not None:
                found.append(dict(result, accession=accession, url=url))
            elif why:
                reason = why
            elif debug is not None and period_named(table, end):
                shown += [[label.lower(), values] for label, values in (row_of(cells) for cells in table)
                          if label and is_net_income(label.lower())]
            elif debug is not None and any(label and is_net_income(label.lower())
                                           for label in (row_of(cells)[0] for cells in table)):
                # a net income table without its period name: is the name in the table just before it?
                orphans["net_tables_without_period"] += 1
                if previous is not None and period_named(previous, end):
                    orphans["period_in_previous_table"] += 1
                if len(orphans["sample"]) < 2:
                    orphans["sample"].append([" | ".join(cell for cell in row if cell)[:120] for row in table[:3]])
            previous = table
    if not found:
        if debug is not None:
            debug.append({"target": target, "period_net_rows": shown[:4], "orphans": orphans})
            text = sec.get_text(base + (exhibits + extra)[0]) or ""
            debug.append({"net_like_text": re.findall(r">\s*([^<>]{0,80}net[^<>]{0,60})<", text, re.I)[:6]})
        return None, reason
    if len({item["epsDiluted"] for item in found}) > 1:
        return None, "tables disagree on diluted EPS"
    return found[0], None


def find_quarter(sec, cik, filings, end, target, debug=None):
    """The first candidate filing that gives this quarter → (result, None), else (None, reason)."""
    reason = "no 8-K with Item 2.02 in the window"
    for filing in candidate_filings(filings, date.fromisoformat(end))[:MAX_FILINGS]:
        result, why = from_filing(sec, cik, filing, target, end, debug)
        if result is not None:
            return dict(result, end=end, filingDate=filing["filingDate"]), None
        reason = why or reason
    return None, reason


def diagnose_filing(sec, cik, filing, end, target, fixture_specs=None):
    """Return read-only table and document evidence for one candidate 8-K."""
    accession = filing["accessionNumber"]
    base = ARCHIVE_URL.format(cik=int(cik), folder=accession.replace("-", ""))
    index_url = base + "index.json"
    result = {"accession": accession, "filingDate": filing.get("filingDate"),
              "filingUrl": base, "indexUrl": index_url, "documents": []}
    index = sec.get_json(index_url)
    if index is None:
        result["error"] = "filing index not available"
        return result, []
    items = (index.get("directory") or {}).get("item") or []
    page_urls = [base + accession + "-index.html", base + accession + "-index.htm"]
    page = sec.get_text(page_urls[0]) or sec.get_text(page_urls[1])
    by_name = [item["name"] for item in items if is_release_exhibit(item)]
    exhibits = list(dict.fromkeys(index_exhibits(page) + by_name))[:MAX_EXHIBITS]
    extra = [item["name"] for item in items if (item.get("name") or "").lower().endswith((".htm", ".html"))
             and item["name"] not in exhibits][:EXTRA_DOCS]
    non_html = [{"name": item.get("name"), "type": item.get("type")}
                for item in items if not (item.get("name") or "").lower().endswith((".htm", ".html"))]
    result.update(indexItems=[{"name": item.get("name"), "type": item.get("type")}
                              for item in items[:40]],
                  nonHtmlItems=non_html[:20],
                  omittedHtmlItems=max(0, sum((item.get("name") or "").lower().endswith((".htm", ".html"))
                                              for item in items) - len(exhibits) - len(extra)))
    if not exhibits and not extra:
        result["error"] = "no HTML exhibit or extra document in the filing"
        return result, []

    for position, name in enumerate(exhibits + extra):
        url = base + name
        text = sec.get_text(url)
        if text is None:
            result["documents"].append({"name": name, "url": url, "error": "document not available",
                                        "tableCount": 0, "tables": [], "targetPresentAnywhere": False,
                                        "targetMatches": [], "rawTextMatches": []})
            continue
        tables = tables_of(text)
        document = {"name": name, "url": url, "isExhibit": position < len(exhibits),
                    "tableCount": len(tables), "tables": [],
                    "targetMatches": [], "netIncomeTargetMatches": [], "rawTextMatches": []}
        selected_fixtures = [spec for spec in fixture_specs or []
                             if spec.get("accession") == accession and spec.get("document") == name]
        raw_tables = html_tables_of(text) if selected_fixtures else []
        for spec in selected_fixtures:
            table_index = spec.get("tableIndex")
            if not isinstance(table_index, int) or table_index < 0 or table_index >= len(raw_tables):
                document.setdefault("fixtureErrors", []).append({
                    "file": spec.get("file"), "tableIndex": table_index,
                    "error": f"source table index unavailable; found {len(raw_tables)} tables"})
                continue
            source_url = spec.get("url") or url
            document.setdefault("fixtureHtml", []).append({
                "file": spec.get("file"), "class": spec.get("class"),
                "key": spec.get("key"), "end": spec.get("end"),
                "accession": accession, "document": name, "tableIndex": table_index,
                "sourceUrl": source_url,
                "html": f"<!-- Source URL: {source_url} -->\n{raw_tables[table_index]}"})
        for table_index, table in enumerate(tables):
            ni_labels, ni_rows, matches = [], [], []
            for row_index, cells in enumerate(table):
                label, values = row_of(cells)
                low = label.lower() if label else ""
                if label and NI_RE.search(low):
                    ni_labels.append(label)
                    closest = None
                    for column_index, value in enumerate(values):
                        if value is None or target is None:
                            continue
                        for scale in SCALES:
                            scaled = value * scale
                            relative_difference = abs(scaled - target) / max(abs(target), 1.0)
                            candidate = {"column": column_index, "value": value, "scale": scale,
                                         "scaledValue": scaled,
                                         "relativeDifference": relative_difference}
                            if closest is None or relative_difference < closest["relativeDifference"]:
                                closest = candidate
                    ni_rows.append({"row": row_index, "label": label,
                                    "acceptedNiLabel": is_net_income(low), "values": values,
                                    "closestToTarget": closest})
                for column_index, value in enumerate(values):
                    scales = target_matches(value, target)
                    if not scales:
                        continue
                    match = {"row": row_index, "column": column_index, "label": label,
                             "value": value, "scales": scales,
                             "niLike": bool(label and NI_RE.search(low)),
                             "acceptedNiLabel": bool(label and is_net_income(low))}
                    matches.append(match)
                    document["targetMatches"].append(dict(match, table=table_index))
                    if match["niLike"]:
                        document["netIncomeTargetMatches"].append(dict(match, table=table_index))
            named = period_named(table, end)
            quarter_wording = bool(QUARTER_WORDING.search(" ".join(" ".join(row) for row in table)))
            variant_period = (not named and quarter_wording
                              and str(date.fromisoformat(end).year) in " ".join(" ".join(row) for row in table))
            if not ni_labels and not named and not matches:
                continue
            preceding = []
            for prior_index in range(table_index - 1, -1, -1):
                prior = tables[prior_index]
                if not table_is_nonempty(prior):
                    continue
                prior_text = " ".join(" ".join(row) for row in prior)
                preceding.append({"table": prior_index, "periodNamed": period_named(prior, end),
                                  "quarterWording": bool(QUARTER_WORDING.search(prior_text)),
                                  "rows": short_table_rows(prior)})
                if len(preceding) == 3:
                    break
            info = {"table": table_index, "rows": short_table_rows(table),
                    "columnCounts": [len(row) for row in table],
                    "columnCount": max((len(row) for row in table), default=0),
                    "niLabels": list(dict.fromkeys(ni_labels)), "niRows": ni_rows,
                    "periodNamed": named,
                    "quarterWording": quarter_wording, "periodWordingHint": variant_period,
                    "targetMatches": matches,
                    "netIncomeTargetMatches": [match for match in matches if match["niLike"]],
                    "nearestPrecedingNonempty": preceding}
            document["tables"].append(info)
        for token in NUMBER_TOKEN.finditer(text):
            value = number(token.group(0))
            scales = target_matches(value, target)
            if scales:
                document["rawTextMatches"].append({"value": value, "scales": scales,
                                                   "context": text[max(0, token.start()-100):token.end()+100]
                                                   .replace("\n", " ")[:220]})
                if len(document["rawTextMatches"]) >= 12:
                    break
        document["targetPresentAnywhere"] = bool(document["targetMatches"] or document["rawTextMatches"])
        document["netIncomeTargetPresent"] = bool(document["netIncomeTargetMatches"])
        result["documents"].append(document)
    return result, []


def layout_class_hint(diagnosis):
    """A review hint only; final S1 classes are assigned from the collected evidence."""
    candidates = diagnosis.get("candidateFilings") or []
    if not candidates:
        return "L7"
    docs = [doc for filing in candidates for doc in filing.get("documents", [])]
    if not any(doc.get("tableCount", 0) for doc in docs):
        return "L6"
    for filing in candidates:
        for document in filing.get("documents", []):
            for table in document.get("tables", []):
                if table.get("netIncomeTargetMatches") and not table.get("periodNamed"):
                    if any(prior.get("periodNamed") and prior.get("quarterWording")
                           for prior in table.get("nearestPrecedingNonempty", [])):
                        return "L1"
                    if table.get("periodWordingHint"):
                        return "L2"
                if table.get("periodNamed") and table.get("netIncomeTargetMatches"):
                    if any(not match.get("acceptedNiLabel")
                           for match in table["netIncomeTargetMatches"]):
                        return "L3"
    has_net_income_matches = any(doc.get("netIncomeTargetPresent") for doc in docs)
    period_ni_tables = [table for filing in candidates for doc in filing.get("documents", [])
                        for table in doc.get("tables", []) if table.get("periodNamed") and table.get("niLabels")]
    tables = [table for filing in candidates for doc in filing.get("documents", [])
              for table in doc.get("tables", [])]
    if tables and all(YEAR_WORDING.search(" ".join(str(x) for x in table.get("rows", [])))
                      and not table.get("quarterWording") for table in tables):
        return "L5"
    if not has_net_income_matches and period_ni_tables:
        return "L4"
    return "L9"


def diagnose_quarter(sec, cik, filings, key, end, target, fixture_specs=None):
    """Inspect the same nearest Item 2.02 filings and documents used by find_quarter."""
    candidates = candidate_filings(filings, date.fromisoformat(end))[:MAX_FILINGS]
    result = {"key": key, "end": end, "targetNetIncome": target,
              "candidateFilingCount": len(candidates), "candidateFilings": []}
    if not candidates:
        result["classHint"] = "L7"
        result["hintNote"] = "No 8-K with Item 2.02 was found inside FILING_DAYS and MAX_FILINGS."
        return result, []
    for filing in candidates:
        detail, _ = diagnose_filing(sec, cik, filing, end, target, fixture_specs)
        result["candidateFilings"].append(detail)
    result["classHint"] = layout_class_hint(result)
    result["hintNote"] = "Heuristic only; assign the final S1 class after reviewing the tables and filing text."
    return result, []


def source_target(doc, end):
    """Find the stored Q4 net income for one report row without changing the source document."""
    quarter = doc.get("quarterly") or {}
    ends, fqs = quarter.get("end") or [], quarter.get("fq") or []
    values = quarter.get("values") or {}
    net_income = values.get("netIncome") or []
    for i, period_end in enumerate(ends):
        if period_end == end and i < len(fqs) and fqs[i] == 4:
            return net_income[i] if i < len(net_income) else None
    return None


def run_diagnose(args, sec):
    """Diagnose the no-table rows from a prior report; never writes financials or state."""
    with open(args.source_report, encoding="utf-8") as source:
        previous = json.load(source)
    fixture_selection_path = getattr(args, "fixture_selection", "")
    fixture_specs_by_row = {}
    if fixture_selection_path:
        with open(fixture_selection_path, encoding="utf-8") as source:
            fixture_selection = json.load(source)
        for spec in fixture_selection.get("cases", []):
            fixture_specs_by_row.setdefault((str(spec.get("key", "")).upper(), spec.get("end")), []).append(spec)
    all_source_rows = [row for row in previous.get("rows", [])
                       if row.get("reason") == "no table shows this quarter's net income"]
    source_rows = all_source_rows
    cik_of = seed.parse_ticker_map(sec.get_json(TICKERS_URL) or {})
    filings_by_key, doc_by_key, rows = {}, {}, []
    stop = None
    for item in source_rows:
        key, end = str(item.get("key", "")).upper(), item.get("end")
        if key not in doc_by_key:
            path = os.path.join(FIN_DIR, key + ".json")
            doc_by_key[key] = seed.read_json(path) if os.path.isfile(path) else None
        doc = doc_by_key[key]
        target = source_target(doc, end) if isinstance(doc, dict) else None
        record = {"key": key, "end": end, "storedEps": item.get("stored"),
                  "sourceReason": item.get("reason"), "inputDebugPresent": "debug" in item,
                  "targetNetIncome": target}
        cik = cik_of.get(key)
        if not isinstance(doc, dict) or target is None:
            record.update(classHint="L9", hintNote="Financials file or Q4 netIncome target is missing.",
                          candidateFilingCount=0, candidateFilings=[])
            rows.append(record)
            continue
        if cik is None:
            record.update(classHint="L9", hintNote="Ticker is missing from the SEC company ticker map.",
                          candidateFilingCount=0, candidateFilings=[])
            rows.append(record)
            continue
        try:
            if key not in filings_by_key:
                filings_by_key[key] = filing_rows(sec, cik) if cik is not None else []
            diagnosis, _ = diagnose_quarter(sec, cik, filings_by_key[key], key, end, target,
                                            fixture_specs_by_row.get((key, end), []))
            record.update(diagnosis)
            rows.append(record)
        except (OutOfBudget, Forbidden, NetworkError) as exc:
            stop = f"{type(exc).__name__}: {exc}"
            break
    hints = Counter(row.get("classHint", "L9") for row in rows)
    fixtures, fixture_errors = [], []
    for row in rows:
        for filing in row.get("candidateFilings", []):
            for document in filing.get("documents", []):
                fixtures.extend(document.pop("fixtureHtml", []))
                fixture_errors.extend({"key": row.get("key"), "end": row.get("end"),
                                       "accession": filing.get("accession"),
                                       "document": document.get("name"), **error}
                                      for error in document.pop("fixtureErrors", []))
    return {"mode": "diagnose", "sourceReport": os.path.relpath(args.source_report, REPO),
            "sourceRows": len(all_source_rows), "selectedSourceRows": len(source_rows),
            "diagnosed": len(rows), "classHintCounts": dict(sorted(hints.items())),
            "fixtureSelection": os.path.relpath(fixture_selection_path, REPO) if fixture_selection_path else None,
            "fixtureCount": len(fixtures), "fixtureErrors": fixture_errors, "fixtures": fixtures,
            "stopped": stop, "requests": sec.requests, "rows": rows}


def verify_quarter(sec, cik, filings, end, net, eps, doc):
    """
    Independent check: the next year's release shows this quarter again as its prior-year column.
    → {"status": "confirmed" | "conflict" | "unavailable", "epsDiluted": checked EPS on today's basis, "url"}.
    """
    when = date.fromisoformat(end)
    reason, debug = "no 8-K with Item 2.02 in the next year's window", []
    for filing in candidate_filings(filings, when + timedelta(days=365))[:MAX_FILINGS]:
        result, why = from_filing(sec, cik, filing, net, end, debug)
        if result is None:
            reason = why or reason
            continue
        checked = round(result["epsDiluted"] / split_factor(doc, filing["filingDate"]), 6)
        status = "confirmed" if within_tolerance(checked, eps) else "conflict"
        return {"status": status, "epsDiluted": checked, "url": result["url"]}
    return {"status": "unavailable", "reason": reason, "debug": debug[:2]}


def q4_periods(doc):
    """Every Q4 quarter that holds net income: index, end, stored net income, stored diluted EPS and its derived flag."""
    quarterly = doc.get("quarterly") or {}
    ends, fq = quarterly.get("end") or [], quarterly.get("fq") or []
    values = quarterly.get("values") or {}
    derived = quarterly.get("derived") or []

    def at(field, i):
        column = values.get(field) or []
        return column[i] if i < len(column) else None

    return [{"index": i, "end": end, "target": at("netIncome", i), "eps": at("epsDiluted", i),
             "derived": i < len(derived) and derived[i] == 1}
            for i, end in enumerate(ends) if i < len(fq) and fq[i] == 4 and at("netIncome", i)]


def process_ticker(sec, key, cik, doc, mode, rows):
    """Appends one row per period to rows; True when every period of the ticker was looked at."""
    periods = q4_periods(doc)
    if mode == "backtest":
        periods = [p for p in periods if p["eps"] is not None]
    else:
        periods = [p for p in periods if p["eps"] is None]
    if not periods:
        return True
    filings = filing_rows(sec, cik)
    for p in periods:
        debug = []
        result, reason = find_quarter(sec, cik, filings, p["end"], p["target"], debug)
        if result is not None:
            factor = split_factor(doc, result["filingDate"])
            if factor != 1:
                result = dict(result, asReported=result["epsDiluted"], splitFactor=factor,
                              epsDiluted=round(result["epsDiluted"] / factor, 6),
                              epsBasic=None if result["epsBasic"] is None else round(result["epsBasic"] / factor, 6),
                              dilutedShares=None if result["dilutedShares"] is None
                              else round(result["dilutedShares"] * factor, 1))
        if mode in ("backtest", "fill") and result is not None:
            result = dict(result, verified=verify_quarter(sec, cik, filings, p["end"], result["net"],
                                                          result["epsDiluted"], doc))
        rows.append({"key": key, "end": p["end"], "stored": p["eps"], "target": p["target"], "derived": p["derived"],
                     "result": result, "reason": reason, "debug": debug[:2]})
    return True


def apply_fill(doc, found):
    """
    Writes accepted values into null fields only; records the source of every filled quarter and a quality grade
    per written EPS field (the grade is what the consumers read): "A" when the next year's release confirms the
    value, "C" otherwise. C is never selected as a US Q4 EPS.
    """
    quarterly = doc["quarterly"]
    ends = quarterly["end"]
    values = quarterly.setdefault("values", {})
    sources = doc.setdefault("q4EpsSources", {})
    for i, end in enumerate(ends):
        result = found.get(end)
        if not result:
            continue
        grade = "A" if (result.get("verified") or {}).get("status") == "confirmed" else "C"
        written = []
        for field in ("epsDiluted", "epsBasic", "dilutedShares"):
            value = result.get(field)
            if value is None:
                continue
            column = values.setdefault(field, [None] * len(ends))
            if len(column) < len(ends):
                column.extend([None] * (len(ends) - len(column)))
            if column[i] is None:
                column[i] = value
                written.append(field)
        eps_fields = [field for field in written if field in ("epsDiluted", "epsBasic")]
        if eps_fields:
            quality = quarterly.setdefault("epsQuality", [])
            if len(quality) < len(ends):
                quality.extend([None] * (len(ends) - len(quality)))
            entry = dict(quality[i] or {})
            for field in eps_fields:
                entry[field] = grade
                entry[field + "Filed"] = result["filingDate"]
            quality[i] = entry
        sources[end] = {"method": "8-K EX-99.1", "accession": result["accession"], "url": result["url"],
                        "filingDate": result["filingDate"], "grade": grade}


def backtest_error(estimate, stored):
    return abs(estimate - stored) / max(abs(stored), 0.10)


def within_tolerance(estimate, stored):
    return abs(estimate - stored) <= max(0.02 * abs(stored), 0.011)


def load_state(path):
    doc = seed.read_json(path) if os.path.exists(path) else None
    if isinstance(doc, dict) and isinstance(doc.get("done"), dict):
        return doc
    return {"version": 1, "done": {}}


def summarize(rows, mode):
    accepted = [r for r in rows if r["result"]]
    out = {"mode": mode, "periods": len(rows), "accepted": len(accepted),
           "reasons": dict(Counter(r["reason"] for r in rows if not r["result"]).most_common())}
    if mode == "backtest" and rows:
        out["coverage"] = round(len(accepted) / len(rows), 4)
        if accepted:
            errors = [backtest_error(r["result"]["epsDiluted"], r["stored"]) for r in accepted]
            out["median_error"] = round(statistics.median(errors), 4)
            right = sum(within_tolerance(r["result"]["epsDiluted"], r["stored"]) for r in accepted)
            out["correct_share_of_all_periods"] = round(right / len(rows), 4)
            # stored Q4 values are mostly derived (annual minus Q1-Q3), so they are a noisy reference
            out["accepted_on_derived_stored"] = sum(bool(r.get("derived")) for r in accepted)
            checks = Counter((r["result"].get("verified") or {}).get("status", "not run") for r in accepted)
            out["independent_check"] = dict(checks)
            out["independent_samples"] = [
                {"key": r["key"], "end": r["end"], "target": r["target"], "debug": r["result"]["verified"]["debug"]}
                for r in accepted if (r["result"].get("verified") or {}).get("reason", "").startswith("no table")][:3]
            out["independent_unavailable_reasons"] = dict(Counter(
                (r["result"].get("verified") or {}).get("reason", "") for r in accepted
                if (r["result"].get("verified") or {}).get("status") == "unavailable").most_common())
            confirmed, conflict = checks.get("confirmed", 0), checks.get("conflict", 0)
            if confirmed + conflict:
                out["independent_precision"] = round(confirmed / (confirmed + conflict), 4)
    return out


def run(args, sec):
    if args.mode == "diagnose":
        return run_diagnose(args, sec)
    cik_of = seed.parse_ticker_map(sec.get_json(TICKERS_URL) or {})
    state = load_state(STATE_PATH)
    wanted = {t.strip().upper() for t in args.tickers.split(",") if t.strip()} if args.tickers else None
    keys = sorted(name[:-5] for name in os.listdir(FIN_DIR) if name.endswith(".json") and name != "index.json")
    keys = [k for k in keys if fin.market_of(k) == "US" and (wanted is None or k in wanted)]
    if args.limit:
        keys = keys[:args.limit]
    rows, stop, filled = [], None, 0
    today = date.today().isoformat()
    for key in keys:
        if args.mode == "fill" and key in state["done"]:
            continue
        path = os.path.join(FIN_DIR, key + ".json")
        doc = seed.read_json(path)
        cik = cik_of.get(key)
        if cik is None or not isinstance(doc, dict):
            continue
        ticker_rows, complete = [], False
        try:
            complete = process_ticker(sec, key, cik, doc, args.mode, ticker_rows)
        except (OutOfBudget, Forbidden, NetworkError) as exc:
            stop = f"{type(exc).__name__}: {exc}"
        rows.extend(ticker_rows)
        if args.mode == "fill":
            found = {r["end"]: r["result"] for r in ticker_rows if r["result"]}
            if found:
                apply_fill(doc, found)
                seed.write_atomic(path, doc)
                filled += len(found)
            if complete:
                state["done"][key] = {"checked": today, "filled": len(found)}
        if stop:
            break
    if args.mode == "fill":
        seed.write_atomic(STATE_PATH, state)
    report = summarize(rows, args.mode)
    mismatches = [{"key": r["key"], "end": r["end"], "stored": r["stored"], "estimate": r["result"]["epsDiluted"],
                   "basic": r["result"]["epsBasic"], "derived": r["derived"],
                   "verified": r["result"].get("verified"), "netLabel": r["result"].get("netLabel"),
                   "epsLabel": r["result"].get("epsLabel"), "perShareRows": r["result"].get("perShareRows"),
                   "url": r["result"]["url"]}
                  for r in rows if args.mode == "backtest" and r["result"]
                  and not within_tolerance(r["result"]["epsDiluted"], r["stored"])]
    samples = {}
    for r in rows:
        kind = (r["reason"] or "").split(":")[0][:40]
        if r["reason"] and kind not in samples:
            samples[kind] = {"key": r["key"], "end": r["end"], "stored": r["stored"], "debug": r["debug"]}
    report.update(filled=filled, tickers_done=len(state["done"]), stopped=stop, requests=sec.requests,
                  mismatches=mismatches[:15], samples=samples,
                  rows=[{k: r[k] for k in ("key", "end", "stored", "result", "reason")} for r in rows])
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Q4 EPS from 8-K earnings releases (see module docstring)")
    parser.add_argument("--mode", choices=("diagnose", "backtest", "fill"), default="backtest")
    parser.add_argument("--tickers", default="", help="comma separated; empty = every US file")
    parser.add_argument("--limit", type=int, default=0, help="only the first N files (0 = all)")
    parser.add_argument("--budget", type=int, default=6000, help="SEC requests this run")
    parser.add_argument("--report", default="", help="also write the report JSON to this path")
    parser.add_argument("--source-report", default=DIAGNOSE_SOURCE,
                        help="backtest report whose no-table rows are inspected in diagnose mode")
    parser.add_argument("--fixture-selection", default="",
                        help="optional JSON manifest selecting SEC table HTML to include in diagnose output")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    configured = (os.environ.get("SEC_USER_AGENT") or "").strip()
    user_agent = seed.resolve_user_agent({"SEC_USER_AGENT": configured} if configured else {})
    sec = Sec(user_agent, budget=args.budget)
    try:
        report = run(args, sec)
    except Forbidden as exc:
        if args.mode == "diagnose":
            print(json.dumps({"mode": "diagnose", "sourceReport": args.source_report,
                              "diagnosed": 0, "stopped": f"Forbidden: {exc}",
                              "requests": sec.requests, "rows": []}, ensure_ascii=False, indent=2), flush=True)
        else:
            print(f"STOPPED: SEC returned HTTP 403 for {exc}; files written so far are kept, re-run later.",
                  flush=True)
        return 2
    except NetworkError as exc:
        if args.mode == "diagnose":
            print(json.dumps({"mode": "diagnose", "sourceReport": args.source_report,
                              "diagnosed": 0, "stopped": f"NetworkError: {exc}",
                              "requests": sec.requests, "rows": []}, ensure_ascii=False, indent=2), flush=True)
        else:
            print(f"STOPPED: SEC could not be reached ({exc}); re-run later.", flush=True)
        return 2
    if args.mode == "diagnose":
        # stdout may be redirected by Actions to an artifact path; the script itself stays read-only.
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    else:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as file:
                file.write(text)
        summary = {k: v for k, v in report.items() if k != "rows"}
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.mode == "diagnose" and report.get("stopped"):
        print(f"STOPPED: diagnostic run ended with {report['stopped']}; do not retry after an SEC 403.",
              file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
