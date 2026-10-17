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
  gate      offline (no SEC): merge backtest reports given with --reports and write the S3 summary to
            --report. Exits 3 and writes nothing unless every (key, end) of --answer-set is in exactly
            one report row.

--answer-set PATH (backtest only) limits the run to the (key, end) rows of the answer set; the target is
the row's netIncome and the reference its value. --shard k/N (with --answer-set) takes the sorted unique
answer-set keys whose index mod N is k.

SEC access: User-Agent from SEC_USER_AGENT (or the repo default), at most about 5 requests per second,
stop at the first HTTP 403, and --budget requests per run. docs/report/fundamentals/q4-8k-state.json
records finished tickers so the next run continues where this one stopped.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, timedelta
from html import escape as html_escape
from html import unescape as html_unescape
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
STRICT_UNIT_CAP = 0.01     # the strict half-unit slack is at most this share of the target (S1-CF C1; table_eps, S2a-2 (e))
DEV_FIXTURES = os.path.join(REPO, "tests", "fixtures", "q4_8k", "s1_fixtures.json")   # gate: devTickers
GATE_EXIT = 3             # --mode gate: an answer-set (key, end) is in no report or in more than one (S3.3)
WILSON_Z = 1.96           # S3.3 Wilson 95% lower bound
FILING_DAYS = (15, 150)    # an 8-K with Item 2.02 this many days after the quarter end
MAX_FILINGS = 3
MAX_EXHIBITS = 4
EXTRA_DOCS = 2             # other .htm documents of the filing, read only when no EX-99 table shows the quarter
REQUEST_INTERVAL = 0.2     # seconds between SEC requests
RETRIES = 3
DASHES = ("", "-", "--", "—", "–")
GENERIC_REASON = "no table shows this quarter's net income"     # the from_filing text; a test pins it
NO_FILING_REASON = "no 8-K with Item 2.02 in the window"         # find_quarter when no candidate filing exists

NI_RE = re.compile(r"net\s*(\(loss\)\s*|loss\s*)?(income|earnings)|net\s+loss")
# non-GAAP and adjusted rows sit next to the GAAP rows in most releases and must never be picked
NON_GAAP = ("non-gaap", "non gaap", "nongaap", "adjusted", "excluding", "pro forma")
NI_AVOID = ("per share", "noncontrolling", "non-controlling", "minority", "continuing", "discontinued",
            "before", "comprehensive") + NON_GAAP
EPS_AVOID = ("weighted", "shares", "number of", "average", "continuing") + NON_GAAP   # total EPS, like the net income row
PER_SHARE_TOKENS = ("per share", "per common share", "per diluted share", "eps", "earnings per")   # diagnose only
HEADING_TOKENS = ("per share", "per common share", "per diluted share", "earnings per")           # diagnose only
S2_HEADING_WORDS = ("per share", "per common share")     # S2b L8 (decision §1 item 1): the heading words the S2 path reads
NON_GAAP_OR_CONTINUING = NON_GAAP + ("continuing",)                                              # diagnose only


class Forbidden(RuntimeError):
    """SEC answered HTTP 403: the run stops at once."""


class OutOfBudget(RuntimeError):
    """The --budget of SEC requests is used up: the run pauses and the next run continues."""


class NetworkError(RuntimeError):
    """SEC could not be reached after the retries."""


class _Table(list):
    """
    The rows of cell texts of one table, as tables_of has always returned them, plus its grid: spans[r][c] is the
    colspan of cell c in row r, and rowspan is True when some cell spans several rows (S2b L1 column alignment).
    """
    spans = None
    rowspan = False


def _attr_count(attrs, name):
    for key, value in attrs:
        if key == name and value and value.strip().isdigit():
            return max(1, min(int(value), 1000))
    return 1


class _TableParser(HTMLParser):
    """Rows of cell texts for every table of an HTML document (no nested tables)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._table, self._row, self._cell = [], None, None, None
        self._spans, self._row_spans, self._span, self._rowspan = None, None, 1, False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table, self._spans, self._rowspan = [], [], False
        elif tag == "tr" and self._table is not None:
            self._row, self._row_spans = [], []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._span = _attr_count(attrs, "colspan")
            self._rowspan = self._rowspan or _attr_count(attrs, "rowspan") > 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._row_spans.append(self._span)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._spans.append(self._row_spans)
            self._row = None
        elif tag == "table" and self._table is not None:
            table = _Table(self._table)
            table.spans, table.rowspan = self._spans, self._rowspan
            self.tables.append(table)
            self._table = self._spans = None

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
YEAR_WORDING_L5 = re.compile(                                                    # classify_row rule 8 only (S1-CF C10)
    r"\b(?:twelve\s+months|years?\s+ended|fiscal\s+years?|full\s+year|year\s+to\s+date|ytd|for\s+the\s+years?)\b", re.I)
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


def _diluted_heading_net(low, values):
    """S2b L8 item 3: a net income row under a diluted heading, with per-share values (under 1000, one not None)."""
    return (is_net_income(low) and not any(word in low for word in EPS_AVOID)
            and any(value is not None for value in values) and all(value is None or abs(value) < 1000 for value in values))


def _labelled_rows(table, s2=True, renamed=None):
    """
    (lower-cased label, values) of every labelled row; the Basic / Diluted rows under a per-share heading are renamed.
    s2=False keeps the S1 heading words ("per share" only), which the S1 classifier evidence was recorded with.
    s2=True also reads "per common share" headings (S2b L8 item 1), and renames the first net income row under a
    diluted heading to "diluted earnings per share" (S2b L8 item 3). renamed, when a list, receives the index of every
    row whose label was renamed.
    """
    heading_words = S2_HEADING_WORDS if s2 else ("per share",)
    rows, heading_left, heading_diluted, heading_renamed = [], 0, False, False
    for cells in table:
        label, values = row_of(cells)
        if not label:
            continue
        low = label.lower()
        if any(token in low for token in heading_words) and all(value is None for value in values):
            heading_left = 2                # "Net income (loss) per share:" heading; its Basic / Diluted rows follow
            heading_diluted = s2 and re.search(r"\bdiluted\b", low) is not None and re.search(r"\bbasic\b", low) is None
            heading_renamed = False
            continue
        if heading_left:
            heading_left -= 1
            # only the two rows under the heading, and never share counts (per-share values are far below 1000)
            if re.fullmatch(r"(basic|diluted)[\s:]*", low) and all(value is None or abs(value) < 1000 for value in values):
                low = f"{low.split()[0]} earnings per share"
                if renamed is not None:
                    renamed.append(len(rows))
            elif heading_diluted and not heading_renamed and _diluted_heading_net(low, values):
                low = "diluted earnings per share"          # at most one row per heading
                heading_renamed = True
                if renamed is not None:
                    renamed.append(len(rows))
        rows.append((low, values))
    return rows


YEAR_TOKEN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _grid(table):
    """Each row as [(text, first grid column, colspan)]; a cell without a colspan takes one column."""
    spans = getattr(table, "spans", None)
    grid = []
    for index, row in enumerate(table):
        widths = spans[index] if spans and index < len(spans) and len(spans[index]) == len(row) else [1] * len(row)
        position, cells = 0, []
        for text, width in zip(row, widths):
            cells.append((text, position, width))
            position += width
        grid.append(cells)
    return grid


def _header_row_indices(table):
    """Indices of the header rows: those before the first numeric row that is not a year row, at most 6 non-empty."""
    indices = []
    for index, cells in enumerate(table):
        _, values = row_of(cells)
        if any(value is not None for value in values) and not _year_row(cells):
            break
        if table_is_nonempty([cells]):
            indices.append(index)
            if len(indices) == 6:
                break
    return indices


def _period_columns(table):
    """
    The period columns of a header table (S2b L1): each cell of its last header row that names a year, with the text
    of every header cell above it that covers the same grid column. None when the grid is unknown (a rowspan).
    """
    if getattr(table, "rowspan", False):
        return None
    grid = _grid(table)
    header = _header_row_indices(table)
    leaf_row = next((index for index in reversed(header)
                     if any(YEAR_TOKEN.search(text) for text, _, _ in grid[index])), None)
    if leaf_row is None:
        return []
    columns = []
    for text, start, _ in grid[leaf_row]:
        if not YEAR_TOKEN.search(text):
            continue
        covering = [cell for index in header if index <= leaf_row
                    for cell, first, width in grid[index] if cell and first <= start < first + width]
        columns.append(" ".join(covering))
    return columns


def _look_back(hit, end, preceding, annual, target):
    """
    S2b L1 (handoff S2.1, S2.3; S1 decision §2 rule 6): the net income column of a table that does not name the period is
    the quarter only when the nearest preceding table that names the period with quarter wording has a period column
    for that column. → (True, None) to read the table, (False, reason) to reject it, (False, None) when no preceding
    table names the quarter.
    """
    j, _, values, _ = hit
    named = [previous for previous in preceding if period_named(previous, end)]
    header = next((previous for previous in named if QUARTER_WORDING.search(header_text(previous))), None)
    if header is None:
        if named and YEAR_WORDING.search(header_text(named[0])):
            return False, "period header is a full year, not a quarter"
        return False, None
    columns = _period_columns(header)
    if columns is None or len(columns) != len(values):
        return False, "period header not aligned"
    column = normalize_header(columns[j])
    if YEAR_WORDING.search(column):
        return False, "matched column header is a full year"
    if not (shows_period(column, end) and QUARTER_WORDING.search(column)):
        return False, "matched column header does not name the quarter"
    if annual is not None and abs(annual - target) <= NI_TOLERANCE * abs(target):
        return False, "net income equals the full-year net income"
    return True, None


def table_eps(table, target, end, annual=None, s2=True, preceding=()):
    """
    One table → (result dict, None), (None, reason) when it is the quarter but fails a check, or (None, None)
    when it does not show this quarter's net income at all. A table that does not name the period is read only
    through the look-back to a preceding table (S2b L1): preceding is up to 3 non-empty tables of the same document,
    nearest first. annual is the full-year net income of the quarter's fiscal year, or None: it rejects a quarter
    whose net income equals it (S2b L1 and L8, handoff S2.3). s2=False gives the S1 matcher only (no heading
    variants, no renamed rows, no look-back): the S1 classifier evidence uses it.
    """
    renamed = []
    rows = _labelled_rows(table, s2, renamed)
    eps_index = next((index for index, (label, _) in enumerate(rows) if is_eps(label, "diluted")), None)
    if eps_index is None:
        return None, None
    eps_label, eps_row = rows[eps_index]
    hits = []                       # (column, unit scale, net values, net label) over every net income row
    for cells in table:             # the raw cells, so that each value keeps the digits after its point (S2a-2 (e))
        raw_label, pairs = cells_with_decimals(cells)
        if not raw_label or not is_net_income(raw_label.lower()):
            continue
        values = [value for value, _ in pairs]
        for j, (value, decimals) in enumerate(pairs):
            for scale in strict_scales(value, decimals, target):
                hits.append((j, scale, values, raw_label.lower()))
    if not hits:
        return None, None
    if not period_named(table, end):                                  # S2b L1: read through the look-back only
        if not s2 or len({(j, scale) for j, scale, _, _ in hits}) != 1:
            return None, None
        ok, reason = _look_back(hits[0], end, preceding, annual, target)
        if not ok:
            return None, reason
    if len({(j, scale) for j, scale, _, _ in hits}) != 1:
        return None, "net income matches more than one column or unit"
    j, scale, net_row, net_label = hits[0]
    if eps_index in renamed and annual is not None and abs(annual - target) <= NI_TOLERANCE * abs(target):
        return None, "net income equals the full-year net income"           # S2b L8 check on a renamed row
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


def cells_with_decimals(cells):
    """(label, [(value, decimals)]) of one row: the same label and values as row_of, with the digits after the point."""
    label, pairs = None, []
    for cell in cells:
        text = cell.strip()
        if text in ("", "$", "(", ")", "%"):
            continue
        if label is None and number(text) is None and re.search(r"[A-Za-z]", text):
            label = text
        elif label is not None:
            pairs.append((number(text), _decimal_places(text)))
    return label, pairs


def _decimal_places(text):
    match = re.search(r"\.(\d+)", text or "")
    return len(match.group(1)) if match else 0


def strict_scales(value, decimals, target):
    """
    Every unit scale at which the reported value matches the target within half a unit of its last shown digit, and
    never looser than STRICT_UNIT_CAP of the target (S1-CF C1: a 0 or a single digit no longer matches a large target).
    """
    if value is None or target is None:
        return []
    return [scale for scale in SCALES
            if abs(value * scale - target) <= max(NI_TOLERANCE * abs(target),
                                                  min(0.5 * scale * 10 ** -decimals, STRICT_UNIT_CAP * abs(target)))]


MONEY_UNITS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
MONEY_WORD = re.compile(r"\$\s*\(?\s*(\d[\d,]*(?:\.\d+)?)\s*\)?\s*(thousand|million|billion)\b", re.I)
MONEY_FULL = re.compile(r"\$\s*\(?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?![\d,.])(?!\s*\)?\s*(?:thousand|million|billion)\b)",
                        re.I)
SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
MARKUP_COMMENT = re.compile(r"<!--.*?-->", re.S)
MARKUP_TAG = re.compile(r"<[^>]+>")


def visible_text(text):
    """The text a reader sees: no script, style, comments or tags; entities decoded; whitespace collapsed (S1-CF C4)."""
    text = MARKUP_TAG.sub(" ", MARKUP_COMMENT.sub(" ", SCRIPT_OR_STYLE.sub(" ", text)))
    return " ".join(html_unescape(text).split())


def money_match(visible, target):
    """
    The first money token of the visible text (prose such as "$18.7 million" or "$1,376,813") whose absolute value
    matches the target → (start, 160 characters of visible text around it), or None. Diagnose only (S1-CF C4).
    """
    if target is None:
        return None
    tokens = [(match.start(), match.group(1), MONEY_UNITS[match.group(2).lower()])
              for match in MONEY_WORD.finditer(visible)]
    tokens += [(match.start(), match.group(1), 1) for match in MONEY_FULL.finditer(visible)]
    for start, digits, scale in sorted(tokens):
        value = abs(float(digits.replace(",", "")))
        decimals = _decimal_places(digits)
        slack = max(NI_TOLERANCE * abs(target), min(0.5 * scale * 10 ** -decimals, STRICT_UNIT_CAP * abs(target)))
        if abs(value * scale - abs(target)) <= slack:
            window = max(0, start - 80)
            return start, visible[window:window + 160]
    return None


def diluted_per_share_labels(table):
    """Lower-cased labels of the diluted per-share rows: the EPS-table test of S1 decision §2 (diagnose only)."""
    entries = []
    for cells in table:
        label, values = row_of(cells)
        entries.append((label.lower() if label else None, values))
    headings = [index for index, (low, values) in enumerate(entries)
                if low and all(value is None for value in values) and any(token in low for token in HEADING_TOKENS)]
    has_diluted_shares = any(low and is_diluted_shares(low) for low, _ in entries)
    has_basic = any(low and re.search(r"\bbasic\b", low) for low, _ in entries)
    labels = []
    for index, (low, values) in enumerate(entries):
        if not low or any(word in low for word in EPS_AVOID):
            continue
        direct = "diluted" in low and any(token in low for token in PER_SHARE_TOKENS)
        under_heading = re.match(r"diluted\b", low) is not None and any(h < index <= h + 4 for h in headings)
        # S1-CF C6: a net income row under a per-share heading, with a diluted share row and no basic row (TXT layout)
        net_under_heading = (any(h < index <= h + 4 for h in headings) and is_net_income(low)
                             and all(value is None or abs(value) < 1000 for value in values)
                             and has_diluted_shares and not has_basic)
        if direct or under_heading or net_under_heading:
            labels.append(low)
    return labels


def _diluted_avoid_labels(table):
    """Diluted per-share rows that carry a non-GAAP or continuing-operations word (the L8 auto-negative test)."""
    labels = []
    for cells in table:
        label, _ = row_of(cells)
        low = label.lower() if label else ""
        if "diluted" in low and any(token in low for token in PER_SHARE_TOKENS) \
                and any(word in low for word in NON_GAAP_OR_CONTINUING):
            labels.append(low)
    return labels


YEAR_CELL = re.compile(r"(?:19|20)\d{2}")


def _year_row(cells):
    """True when every numeric cell of the row is a year and there is at least one (S1-CF C5: '2,013' is a value)."""
    numeric = [cell.strip() for cell in cells if number(cell) is not None]
    return bool(numeric) and all(YEAR_CELL.fullmatch(text) for text in numeric)


def header_text(table):
    """
    Text of the header rows: the rows before the first row with a numeric value that is not a year row (S1-CF C5).
    At most 6 non-empty rows, cut to 400 characters.
    """
    rows = [table[index] for index in _header_row_indices(table)]
    text = " ".join(" ".join(cell for cell in cells if cell.strip()) for cells in rows)
    return " ".join(text.split())[:400]


def _month_pattern(month):
    name = MONTHS[month - 1]
    return rf"(?:{name}|{name[:3]})"


VARIANT_QUARTER = re.compile(
    r"\b(?:three\s+months|3\s+months|quarters?|fourth\s+quarter|4th\s+quarter|q4|4q|thirteen\s+weeks|fourteen\s+weeks"
    r"|13\s+weeks|14\s+weeks)\b",
    re.I,
)
MONTH_MENTION = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|sept"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\.?\s*(\d{1,2})\b(?:,?\s*(\d{4})\b)?",
    re.I,
)
MONTH_NUMBER = {name[:3]: number for number, name in enumerate(MONTHS, start=1)}


def normalize_header(header):
    """A space at every lower-to-upper join ('EndedFebruary') and after the comma of '2,2013' (S1-CF C7)."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", header)
    return re.sub(r"\b(\d{1,2}),(\d{4})\b", r"\1, \2", text)


def variant_rule(header, end):
    """
    How a period header names the quarter end in a variant form: "i" to "vi" (S1 decision §2; S1-CF C7), or None when
    shows_period reads the header as it is or it does not name the quarter end at all.
    """
    if shows_period(header, end):
        return None
    text = normalize_header(header)
    when = date.fromisoformat(end)
    year = str(when.year)
    if shows_period(text, end):
        return "v"
    if VARIANT_QUARTER.search(text) and re.search(rf"\b{year}\b", text):
        return "i"
    month = _month_pattern(when.month)
    if re.search(rf"\b{month}\.?\s+{when.day}\b(?!,?\s*\d{{4}})", text, re.I) and re.search(rf"\b{year}\b", text):
        return "ii"
    for match in re.finditer(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)", text):
        if int(match.group(1)) != when.month or int(match.group(2)) != when.day:
            continue
        written = match.group(3)
        if (int(written) == when.year) if len(written) == 4 else (int(written) == when.year % 100):
            return "iii"
    if when.month == 1 and when.day <= 7 and re.search(r"\b(?:fourth quarter|q4|quarter)\b", text, re.I) \
            and re.search(rf"\b{when.year - 1}\b", text):
        return "iv"
    if (when.month == 1 or (when.month == 2 and when.day <= 7)) \
            and re.search(r"\b(?:fourth\s+quarter|4th\s+quarter|q4|4q)\b", text, re.I) \
            and re.search(rf"\b{when.year - 1}\b", text) and not re.search(rf"\b{year}\b", text):
        return "vi"
    return None


def other_quarter_end(header, end):
    """
    The header names another quarter end: a month other than the end month, or a first to third quarter (S1-CF C8).
    Dates within 7 days of the end, or of the end one year earlier, are not other quarter ends (the 52/53-week
    calendar and the prior-year comparative), so they are blanked before the month test.
    """
    when = date.fromisoformat(end)
    previous = date(when.year - 1, 2, 28) if (when.month, when.day) == (2, 29) else date(when.year - 1, when.month, when.day)

    def near(day):
        return abs((day - when).days) <= 7 or abs((day - previous).days) <= 7

    def blank(match):
        month = MONTH_NUMBER[match.group(1)[:3].lower()]
        years = [int(match.group(3))] if match.group(3) else [when.year, when.year - 1]
        for year in years:
            try:
                if near(date(year, month, int(match.group(2)))):
                    return " "
            except ValueError:
                continue
        return match.group(0)

    text = MONTH_MENTION.sub(blank, normalize_header(header))
    for month in range(1, 13):
        if month == when.month or (month == 12 and when.month == 1 and when.day <= 7):
            continue
        if re.search(rf"\b{_month_pattern(month)}\b", text, re.I):
            return True
    return re.search(r"\b(?:first quarter|second quarter|third quarter|q1|q2|q3|1q|2q|3q)\b", text, re.I) is not None


def _table_evidence(table, target, end, named):
    """The M1.3 fields of one recorded table (strict matches, EPS-table test, header wording, table_eps outcome)."""
    strict = []
    for row_index, cells in enumerate(table):
        label, pairs = cells_with_decimals(cells)
        low = label.lower() if label else ""
        for column_index, (value, decimals) in enumerate(pairs):
            scales = strict_scales(value, decimals, target)
            if scales:
                strict.append({"row": row_index, "column": column_index, "value": value, "decimals": decimals,
                               "scales": scales, "label": label, "niLike": bool(label and NI_RE.search(low)),
                               "acceptedNiLabel": bool(label and is_net_income(low))})
    accepted = sorted({(match["column"], scale) for match in strict if match["acceptedNiLabel"]
                       for scale in match["scales"]})
    header = header_text(table)
    eps_labels = diluted_per_share_labels(table)
    evidence = {"strictMatches": strict, "acceptedStrictColumns": [list(pair) for pair in accepted],
                "epsTable": bool(eps_labels), "dilutedPerShareLabels": eps_labels,
                "dilutedAvoidLabels": _diluted_avoid_labels(table),
                "headerText": header, "yearWording": YEAR_WORDING.search(header) is not None,
                "tableEps": None}
    if named and target is not None:
        result, reason = table_eps(table, target, end, s2=False)
        evidence["tableEps"] = {"result": result is not None, "reason": reason,
                                "epsRowFound": any(is_eps(label, "diluted")
                                                   for label, _ in _labelled_rows(table, s2=False))}
    return evidence


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


def from_filing(sec, cik, filing, target, end, debug=None, annual=None):
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
        previous, recent = None, []         # recent: up to 3 non-empty tables before this one, in document order
        for table in tables_of(text):
            result, why = table_eps(table, target, end, annual=annual, preceding=tuple(reversed(recent)))
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
            if table_is_nonempty(table):
                recent = (recent + [table])[-3:]
    if not found:
        if debug is not None:
            debug.append({"target": target, "period_net_rows": shown[:4], "orphans": orphans})
            text = sec.get_text(base + (exhibits + extra)[0]) or ""
            debug.append({"net_like_text": re.findall(r">\s*([^<>]{0,80}net[^<>]{0,60})<", text, re.I)[:6]})
        return None, reason
    if len({item["epsDiluted"] for item in found}) > 1:
        return None, "tables disagree on diluted EPS"
    return found[0], None


def find_quarter(sec, cik, filings, end, target, debug=None, annual=None):
    """
    The first candidate filing that gives this quarter → (result, None), else (None, reason). The reason is the first
    specific reason across the filings; the generic one only when no filing has a specific reason (S2a-2 (f)).
    annual is the full-year net income of the quarter's fiscal year (S2b L8 check), or None.
    """
    specific = generic = None
    for filing in candidate_filings(filings, date.fromisoformat(end))[:MAX_FILINGS]:
        result, why = from_filing(sec, cik, filing, target, end, debug, annual)
        if result is not None:
            return dict(result, end=end, filingDate=filing["filingDate"]), None
        if why and why != GENERIC_REASON:
            specific = specific or why
        else:
            generic = generic or why
    return None, specific or generic or NO_FILING_REASON


def diagnose_filing(sec, cik, filing, end, target, fixture_specs=None, texts=None):
    """
    Return read-only table and document evidence for one candidate 8-K. When texts is a dict, the text of every
    document read is kept in it under (accession, document name) as (url, text); it is never written to JSON.
    """
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
        if texts is not None:
            texts[(accession, name)] = (url, text)
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
                                  "yearWording": YEAR_WORDING.search(header_text(prior)) is not None,
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
            info.update(_table_evidence(table, target, end, named))
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
        document["rawStrictTextMatch"] = any(
            strict_scales(number(token.group(0)), _decimal_places(token.group(0)), target)
            for token in NUMBER_TOKEN.finditer(text))
        money = money_match(visible_text(text), target)
        document["rawMoneyMatch"] = money is not None
        document["rawMoneyContext"] = money[1] if money is not None else None
        document["targetPresentAnywhere"] = bool(document["targetMatches"] or document["rawTextMatches"])
        document["netIncomeTargetPresent"] = bool(document["netIncomeTargetMatches"])
        result["documents"].append(document)
    return result, []


TARGET_MARKER = "<!-- S1 target table -->"
FIXTURE_FILE_CAP = 65_536          # bytes, UTF-8, whole fixture file
FIXTURE_TOTAL_CAP = 1_200_000      # bytes of exported fixture HTML in one run
KEPT_ATTRIBUTES = ("colspan", "rowspan")
VOID_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
                       "track", "wbr"))


class _TrimParser(HTMLParser):
    """Rebuilds markup: no attributes except colspan and rowspan; comments and declarations are dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []

    def handle_starttag(self, tag, attrs):
        kept = "".join(f' {name}="{html_escape(value, quote=True)}"' for name, value in attrs
                       if name in KEPT_ATTRIBUTES and value is not None)
        self.out.append(f"<{tag}{kept}>")

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(data)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")


def trim_table_html(raw):
    """One source table fragment with its tags stripped of attributes (colspan and rowspan kept); text unchanged."""
    parser = _TrimParser()
    parser.feed(raw)
    parser.close()
    return "".join(parser.out).strip()


def export_fixture(text, url, table_indices, target_index):
    """
    Fixture for one row → (html, None), or (None, reason) for a fixtureError. The fixture starts with the source URL,
    then the tables of table_indices in ascending order, each trimmed, with TARGET_MARKER on the line before the
    target table. It is refused when it is over FIXTURE_FILE_CAP or its round trip fails: the tables it parses to
    must equal the source tables at table_indices, cell for cell, and the source must have as many raw table
    fragments as parsed tables.
    """
    source = tables_of(text)
    raw = html_tables_of(text)
    if len(raw) != len(source):
        return None, "round trip: raw and parsed table counts differ"
    indices = sorted(table_indices)
    if not indices or indices[0] < 0 or indices[-1] >= len(source) or target_index not in indices:
        return None, "table index unavailable"
    lines = [f"<!-- Source URL: {url} -->"]
    for index in indices:
        if index == target_index:
            lines.append(TARGET_MARKER)
        lines.append(trim_table_html(raw[index]))
    fixture = "\n".join(lines) + "\n"
    if len(fixture.encode("utf-8")) > FIXTURE_FILE_CAP:
        return None, "too large"
    if tables_of(fixture) != [source[index] for index in indices]:
        return None, "round trip failed"
    return fixture, None


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


def diagnose_quarter(sec, cik, filings, key, end, target, fixture_specs=None, texts=None):
    """Inspect the same nearest Item 2.02 filings and documents used by find_quarter."""
    candidates = candidate_filings(filings, date.fromisoformat(end))[:MAX_FILINGS]
    result = {"key": key, "end": end, "targetNetIncome": target,
              "candidateFilingCount": len(candidates), "candidateFilings": []}
    if not candidates:
        result["classHint"] = "L7"
        result["hintNote"] = "No 8-K with Item 2.02 was found inside FILING_DAYS and MAX_FILINGS."
        return result, []
    for filing in candidates:
        detail, _ = diagnose_filing(sec, cik, filing, end, target, fixture_specs, texts)
        result["candidateFilings"].append(detail)
    result["classHint"] = layout_class_hint(result)
    result["hintNote"] = "Heuristic only; assign the final S1 class after reviewing the tables and filing text."
    return result, []


EDGAR_BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K"
S1_CLASS_NAMES = {"L1": "period-in-earlier-table", "L2": "period-wording", "L3": "ni-label", "L4": "ni-value",
                  "L5": "no-quarter-column", "L6": "not-html-table", "L7": "no-release", "L8": "eps-row",
                  "L9": "other", "L10": "check-reject"}


def _clip(text, limit=300):
    return " ".join(str(text).split())[:limit]


def _classified(cls, note, accession=None, document=None, url=None, table=None, table_indices=None, eps_reason=None,
                variant_form=None):
    if table_indices is None:
        table_indices = [] if table is None else [table]
    return {"class": cls, "accession": accession, "document": document, "url": url, "tableIndex": table,
            "tableIndices": list(table_indices), "tableEpsReason": eps_reason, "note": _clip(note),
            "variantRule": variant_form}                                       # set for L2 only (S1-CF C12)


def _classified_row(record):
    record["classification"] = classify_row(record)
    return record


INDEX_PAGE = re.compile(r"^\d{10}-\d{2}-\d{6}-index(?:-headers)?\.html?$", re.I)     # EDGAR index pages (S1-CF C3)


def _with_recomputed_strict(row):
    """
    A copy of a diagnosed row whose recorded strict matches are recomputed with strict_scales (S1-CF C2): each match keeps
    only the scales it still meets, and acceptedStrictColumns is rebuilt from the matches under an accepted label.
    The input row is not changed.
    """
    copied = copy.deepcopy(row)
    target = copied.get("targetNetIncome")
    for filing in copied.get("candidateFilings") or []:
        for document in filing.get("documents") or []:
            for table in document.get("tables") or []:
                kept = []
                for match in table.get("strictMatches") or []:
                    scales = strict_scales(match.get("value"), match.get("decimals") or 0, target)
                    if scales:
                        kept.append(dict(match, scales=scales))
                table["strictMatches"] = kept
                accepted = {(match["column"], scale) for match in kept if match.get("acceptedNiLabel")
                            for scale in match["scales"]}
                table["acceptedStrictColumns"] = [[column, scale] for column, scale in sorted(accepted)]
    return copied


def classify_row(row):
    """
    S1 class of one diagnosed row, computed from its JSON evidence only (no SEC access, no stored text). The rules
    are tried in order and the first that holds gives the class; the first table that satisfies it is the evidence.
    The row is copied first: strict matches are recomputed (S1-CF C2) and EDGAR index pages are not release documents
    (S1-CF C3), so no rule sees either.
    """
    row = _with_recomputed_strict(row)
    cik = row.get("cik")
    browse = EDGAR_BROWSE.format(cik=cik) if cik is not None else None
    hint = row.get("hintNote") or "input missing"
    if row.get("targetNetIncome") is None or cik is None:
        return _classified("L9", hint, url=browse)
    filings = row.get("candidateFilings") or []
    if not filings:
        return _classified("L7", "no candidate filing: " + hint, url=browse)
    first = filings[0]
    end = row.get("end")
    pairs = [(filing, document) for filing in filings for document in filing.get("documents") or []
             if not INDEX_PAGE.match(document.get("name") or "")]
    located = [(filing, document, table) for filing, document in pairs for table in document.get("tables") or []]

    def found(cls, filing, document, table, note, table_indices=None, eps_reason=None, variant_form=None):
        return _classified(cls, note, accession=filing.get("accession"), document=document.get("name"),
                           url=document.get("url") or filing.get("filingUrl") or browse, table=table["table"],
                           table_indices=table_indices, eps_reason=eps_reason, variant_form=variant_form)

    def where(document, table):
        return f"table {table['table']} of {document.get('name')}"

    # rule 2 (L6): no candidate release document holds an HTML table (an index page alone gives the index note)
    if not any((document.get("tableCount") or 0) > 0 for _, document in pairs):
        note = (f"no HTML table in any of the {len(pairs)} documents of the candidate filings" if pairs
                else "no release document in the candidate filings (only EDGAR index pages)")
        return _classified("L6", note, accession=first.get("accession"), url=first.get("filingUrl") or browse)
    # rule 3 (L10): period-named EPS table, accepted net income, table_eps rejects it with a reason
    for filing, document, table in located:
        eps = table.get("tableEps") or {}
        if (table.get("periodNamed") and table.get("epsTable") and table.get("acceptedStrictColumns")
                and eps.get("result") is False and eps.get("reason")):
            return found("L10", filing, document, table,
                         f"{where(document, table)}: period-named EPS table; net income matches under an accepted "
                         f"label and table_eps rejects it ({eps['reason']})", eps_reason=eps["reason"])
    # rule 4 (L8): period-named EPS table, one accepted net income column, no diluted per-share row read
    for filing, document, table in located:
        eps = table.get("tableEps") or {}
        accepted = table.get("acceptedStrictColumns") or []
        if (table.get("periodNamed") and table.get("epsTable") and len(accepted) == 1 and eps
                and eps.get("reason") is None and eps.get("epsRowFound") is False):
            column, scale = accepted[0]
            return found("L8", filing, document, table,
                         f"{where(document, table)}: period-named EPS table; net income matches in column {column} "
                         f"at scale {scale:g}, and no diluted per-share row was read")
    # rule 5 (L3): period-named EPS table, net income only under a label that is not accepted
    for filing, document, table in located:
        if table.get("periodNamed") and table.get("epsTable") and not table.get("acceptedStrictColumns"):
            for match in table.get("strictMatches") or []:
                label = match.get("label") or ""
                if not match.get("acceptedNiLabel") and re.search(r"income|earnings|profit|loss", label, re.I):
                    return found("L3", filing, document, table,
                                 f"{where(document, table)}: period-named EPS table; net income matches only under "
                                 f"label '{label}', which is not an accepted net income label")
    # rule 6 (L1): net income in a table with no period header, and a period with quarter wording above it
    for filing, document, table in located:
        if table.get("periodNamed") or not table.get("acceptedStrictColumns"):
            continue
        nearest = (table.get("nearestPrecedingNonempty") or [])[:3]
        period = next((prior for prior in nearest if prior.get("periodNamed") and prior.get("quarterWording")), None)
        if period is not None:
            indices = sorted([prior["table"] for prior in nearest] + [table["table"]])
            return found("L1", filing, document, table,
                         f"{where(document, table)}: net income matches with no period header; table "
                         f"{period['table']} above it names the period with quarter wording",
                         table_indices=indices)
    # rule 7 (L2): net income in a table whose own header names the quarter in a variant form and no other quarter
    for filing, document, table in located:
        if table.get("periodNamed") or not table.get("acceptedStrictColumns"):
            continue
        header = table.get("headerText") or ""
        form = variant_rule(header, end)
        if form is not None and not other_quarter_end(header, end):
            return found("L2", filing, document, table,
                         f"{where(document, table)}: net income matches; header '{header[:120]}' names the quarter "
                         f"ending {end} in a variant form ({form}) and no other quarter end", variant_form=form)
    # rule 8 (L5): the net income tables of the release name a year period and no quarter (S1-CF C10). The relevant
    # tables have a net income value and a header that does not name another quarter end.
    relevant = [(filing, document, table) for filing, document, table in located
                if any(value is not None for ni_row in table.get("niRows") or [] for value in ni_row.get("values") or [])
                and not other_quarter_end(table.get("headerText") or "", end)]
    if relevant and all(YEAR_WORDING_L5.search(table.get("headerText") or "") and not table.get("quarterWording")
                        and not VARIANT_QUARTER.search(table.get("headerText") or "")
                        for _, _, table in relevant):
        filing, document, table = relevant[0]
        return found("L5", filing, document, table,
                     f"{where(document, table)}: every recorded table names a year period without quarter wording")
    # rule 9 (L4): no strict match under a net income label in a release table, and no prose money match (S1-CF C9)
    def ni_strict(match):
        low = (match.get("label") or "").lower()
        return bool(NI_RE.search(low)) and not any(token in low for token in HEADING_TOKENS)

    if (not any(ni_strict(match) for _, _, table in located for match in table.get("strictMatches") or [])
            and not any(document.get("rawMoneyMatch") for _, document in pairs)):
        closest = None
        for filing, document, table in located:
            for ni_row in table.get("niRows") or []:
                candidate = ni_row.get("closestToTarget")
                if candidate and (closest is None or candidate["relativeDifference"] < closest[0]["relativeDifference"]):
                    closest = (candidate, ni_row.get("label"), filing, document, table)
        if closest is not None:
            candidate, label, filing, document, table = closest
            return found("L4", filing, document, table,
                         f"no strict net income match in any table; closest net income-like value "
                         f"{candidate['scaledValue']:,.0f} (relative difference {candidate['relativeDifference']:.4f}) "
                         f"under label '{label}' in {where(document, table)}")
        filing, document = pairs[0]
        return _classified("L4", f"no strict net income match, and no net income-like row in any table of "
                                 f"{document.get('name')}", accession=filing.get("accession"),
                           document=document.get("name"), url=document.get("url") or browse)
    # rule 10 (L9): anything else; the first sub-case that applies gives the note
    for filing, document, table in located:                                       # (f), tried before (a) (S1-CF C11)
        header = table.get("headerText") or ""
        if (table.get("acceptedStrictColumns") and not table.get("periodNamed")
                and variant_rule(header, end) is not None and other_quarter_end(header, end)):
            return found("L9", filing, document, table,
                         f"(f) {where(document, table)}: accepted net income match; header names the quarter in a "
                         f"variant form and also another quarter end: '{header[:120]}'")
    for filing, document, table in located:                                       # (a)
        if (table.get("acceptedStrictColumns") and not table.get("periodNamed")
                and not any(prior.get("periodNamed") for prior in (table.get("nearestPrecedingNonempty") or [])[:3])):
            return found("L9", filing, document, table,
                         f"(a) {where(document, table)}: accepted net income match with no period header in this table "
                         f"or the 3 tables before it; header '{(table.get('headerText') or '')[:120]}'")
    for filing, document, table in located:                                       # (b)
        if table.get("periodNamed") and not table.get("epsTable") and table.get("acceptedStrictColumns"):
            return found("L9", filing, document, table,
                         f"(b) {where(document, table)}: net income is only in a period-named table without EPS rows")
    for filing, document, table in located:                                       # (c)
        eps = table.get("tableEps") or {}
        if (table.get("periodNamed") and table.get("epsTable") and len(table.get("acceptedStrictColumns") or []) > 1
                and eps and not eps.get("epsRowFound")):
            return found("L9", filing, document, table,
                         f"(c) {where(document, table)}: net income matches in more than one column of a period-named "
                         f"EPS table with no diluted row")
    for filing, document, table in located:                                       # (d)
        for match in table.get("strictMatches") or []:
            if not match.get("acceptedNiLabel"):
                return found("L9", filing, document, table,
                             f"(d) {where(document, table)}: strict match only under label '{match.get('label')}', "
                             f"which is not an accepted net income label")
    if not any(table.get("strictMatches") for _, _, table in located):
        for filing, document in pairs:
            if document.get("rawMoneyMatch"):
                return _classified("L9", f"(d) {document.get('name')}: prose money match only, not in a table cell: "
                                         f"'{document.get('rawMoneyContext') or ''}'",
                                   accession=filing.get("accession"), document=document.get("name"),
                                   url=document.get("url") or browse)
    filing, document = next(((f, d) for f, d, t in located if t.get("strictMatches") or t.get("periodNamed")),
                            pairs[0])
    strict_tables = [t["table"] for t in document.get("tables") or [] if t.get("strictMatches")]
    period_tables = [t["table"] for t in document.get("tables") or [] if t.get("periodNamed")]
    return _classified("L9", f"(e) {document.get('name')}: strict-match tables {strict_tables}, period-named tables "
                             f"{period_tables}; no sub-case (a) to (d) applies", accession=filing.get("accession"),
                       document=document.get("name"), url=document.get("url") or browse)


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


POSITIVE_CLASSES = ("L1", "L2", "L3", "L8")
FALLBACK_KINDS = ("next-quarter-table", "period-named-non-eps-ni")
NEGATIVE_TEXT = {
    "L1-year-header": "a twelve-month or year header is not the quarter, and no quarter column is read",
    "L8-non-gaap-diluted-row": "a non-GAAP or continuing diluted row is not the EPS row",
    "next-quarter-table": "a next-quarter header is not the target quarter (slack and L2)",
    "period-named-non-eps-ni": "net income in a period-named table with no EPS row is not an EPS row",
}


def load_selection(path):
    """The fixture manifest (S1 plan §M5). Without a manifest nothing is selected."""
    if not path:
        return {"autoPositives": {}, "explicitNegatives": [], "autoNegatives": []}
    with open(path, encoding="utf-8") as source:
        data = json.load(source)
    return {"autoPositives": data.get("autoPositives") or {},
            "explicitNegatives": data.get("explicitNegatives") or [],
            "autoNegatives": data.get("autoNegatives") or []}


def _auto_matches(record):
    """Tables of one diagnosed row that meet an auto-negative or fallback rule: (kind, accession, document, url, indices, target)."""
    found = []
    end = record.get("end")
    for filing in record.get("candidateFilings") or []:
        for document in filing.get("documents") or []:
            for table in document.get("tables") or []:
                accepted = table.get("acceptedStrictColumns") or []
                eps = table.get("tableEps") or {}
                nearest = (table.get("nearestPrecedingNonempty") or [])[:3]
                target = table["table"]
                place = (filing.get("accession"), document.get("name"), document.get("url"))
                period = next((prior for prior in nearest if prior.get("periodNamed")), None)
                if not table.get("periodNamed") and accepted and period is not None \
                        and period.get("yearWording") and not period.get("quarterWording"):
                    found.append(("L1-year-header", *place,
                                  sorted([prior["table"] for prior in nearest] + [target]), target))
                if (table.get("periodNamed") and len(accepted) == 1 and eps and eps.get("epsRowFound") is False
                        and table.get("dilutedAvoidLabels")):
                    found.append(("L8-non-gaap-diluted-row", *place, [target], target))
                if accepted and other_quarter_end(table.get("headerText") or "", end):
                    found.append(("next-quarter-table", *place, [target], target))
                if table.get("periodNamed") and not table.get("epsTable") and accepted:
                    found.append(("period-named-non-eps-ni", *place, [target], target))
    return found


def _entry(record, kind, role, cls, accession, document, url, indices, target, texts,
           negative_for=None, must_reject=None, auto_kind=None):
    """One fixture candidate of a row, exported from the document text kept for that row."""
    held = texts.get((accession, document))
    entry = {"kind": kind, "autoKind": auto_kind, "role": role, "key": record["key"], "end": record["end"],
             "class": cls, "negativeFor": negative_for, "mustReject": must_reject, "accession": accession,
             "document": document, "url": held[0] if held else url, "tableIndices": sorted(indices),
             "targetTableIndex": target, "storedEps": record.get("storedEps"), "html": None, "fixtureError": None}
    if held is None:
        entry["fixtureError"] = "document text is not among this row's candidate filings"
    else:
        entry["html"], entry["fixtureError"] = export_fixture(held[1], held[0], indices, target)
    return entry


def _row_candidates(record, texts, selection):
    """Every fixture candidate of one diagnosed row: its positive, its explicit negatives and its auto-negative tables."""
    key, end = record["key"], record["end"]
    cls = (record.get("classification") or {}).get("class")
    found = []
    classification = record.get("classification") or {}
    if cls in POSITIVE_CLASSES and classification.get("accession") and classification.get("document"):
        positive = _entry(record, "positive", "positive", cls, classification["accession"],
                          classification["document"], classification.get("url"), classification["tableIndices"],
                          classification["tableIndex"], texts)
        if cls == "L2" and classification.get("variantRule") == "vi":
            # S1-CF C13: rule vi is never an L2 auto-positive; it is skipped like a fixture error
            positive["fixtureError"] = "L2 variant rule vi is never an auto-positive (S1-CF C13)"
        found.append(positive)
    for spec in selection["explicitNegatives"]:
        if str(spec.get("key", "")).upper() == key and spec.get("end") == end:
            indices = sorted(spec["tableIndices"])
            found.append(_entry(record, "explicit", "negative", cls, spec["accession"], spec["document"], None,
                                indices, indices[-1], texts, negative_for=spec["negativeFor"],
                                must_reject=spec.get("mustReject")))
    for kind, accession, document, url, indices, target in _auto_matches(record):
        found.append(_entry(record, "auto", "negative", cls, accession, document, url, indices, target, texts,
                            negative_for=kind, must_reject=NEGATIVE_TEXT[kind], auto_kind=kind))
    return found


def _error_entry(key, end, kind, role, negative_for, accession, document, error):
    return {"kind": kind, "autoKind": negative_for, "role": role, "key": key, "end": end, "class": None,
            "negativeFor": negative_for, "mustReject": None, "accession": accession, "document": document,
            "url": None, "tableIndices": [], "targetTableIndex": None, "storedEps": None, "html": None,
            "fixtureError": error}


def select_fixtures(pool, selection):
    """
    Chooses the fixtures to write (S1 plan §M5) → (fixtures, fixtureErrors). Explicit negatives are written as listed.
    Positives: per class in L1, L2, L3, L8, the candidates sorted by (key, end), one per ticker; first those whose
    ticker is already selected (seeded with the explicit negatives' tickers), then the rest; a candidate with a
    fixture error is skipped. Auto-negatives: the first row in (key, end) order for each kind in autoNegatives, or
    the next unused fallback kind. The total is at most FIXTURE_TOTAL_CAP.
    """
    fixtures, total = [], [0]
    selected = {spec["key"] for spec in selection["explicitNegatives"]}

    def accept(entry):
        if entry["html"] is None or entry["fixtureError"]:
            return False
        size = len(entry["html"].encode("utf-8"))
        if total[0] + size > FIXTURE_TOTAL_CAP:
            entry["fixtureError"] = "exported fixture HTML would exceed 1.2 MB in total"
            return False
        total[0] += size
        fixtures.append(entry)
        return True

    def in_order(entries):
        return sorted(entries, key=lambda entry: (entry["key"], entry["end"]))

    for spec in selection["explicitNegatives"]:
        matches = [entry for entry in pool if entry["kind"] == "explicit" and entry["key"] == str(spec["key"]).upper()
                   and entry["end"] == spec["end"] and entry["accession"] == spec["accession"]
                   and entry["document"] == spec["document"]]
        if not matches:
            pool.append(_error_entry(spec["key"], spec["end"], "explicit", "negative", spec.get("negativeFor"),
                                     spec["accession"], spec["document"], "explicit negative was not read in this run"))
            continue
        accept(matches[0])
    for cls in POSITIVE_CLASSES:
        wanted = int(selection["autoPositives"].get(cls) or 0)
        candidates = in_order(entry for entry in pool if entry["kind"] == "positive" and entry["class"] == cls)
        picked, picked_keys = 0, set()
        for pass_number in (1, 2):
            for entry in candidates:
                if picked >= wanted:
                    break
                if entry["key"] in picked_keys or entry["fixtureError"] or entry["html"] is None:
                    continue
                if (entry["key"] in selected) != (pass_number == 1):
                    continue
                if accept(entry):
                    picked += 1
                    picked_keys.add(entry["key"])
                    selected.add(entry["key"])
    used = set()
    for kind in selection["autoNegatives"]:
        candidates = in_order(entry for entry in pool if entry["kind"] == "auto" and entry["autoKind"] == kind)
        if any(accept(entry) for entry in candidates):
            continue
        for fallback in FALLBACK_KINDS:
            if fallback in used:
                continue
            candidates = in_order(entry for entry in pool if entry["kind"] == "auto" and entry["autoKind"] == fallback
                                  and not (fallback == "next-quarter-table" and entry["key"] == "TXT"))
            chosen = next((entry for entry in candidates if accept(entry)), None)
            if chosen is not None:
                chosen["negativeFor"] = fallback
                chosen["mustReject"] = f"{NEGATIVE_TEXT[fallback]}; used because {kind} was not found"
                used.add(fallback)
                break
        else:
            pool.append(_error_entry("", "", "auto", "negative", kind, None, None,
                                     "auto-negative not found and no fallback kind left"))
    records = []
    for entry in fixtures:
        name = (f"{entry['key']}-{entry['end']}-{entry['class']}.html" if entry["role"] == "positive"
                else f"{entry['key']}-{entry['end']}-{entry['negativeFor']}-negative.html")
        records.append({"file": name, "key": entry["key"], "end": entry["end"], "class": entry["class"],
                        "role": entry["role"], "negativeFor": entry["negativeFor"],
                        "mustReject": entry["mustReject"], "accession": entry["accession"],
                        "document": entry["document"], "url": entry["url"],
                        "tableIndices": entry["tableIndices"], "targetTableIndex": entry["targetTableIndex"],
                        "bytes": None, "sha256": None, "roundTrip": True, "storedEps": entry["storedEps"],
                        "epsDilutedRead": None, "html": entry["html"]})
    errors = [{"key": entry["key"], "end": entry["end"], "class": entry["class"], "role": entry["role"],
               "negativeFor": entry["negativeFor"], "accession": entry["accession"], "document": entry["document"],
               "error": entry["fixtureError"]}
              for entry in pool if entry.get("fixtureError")]
    return records, errors


def run_diagnose(args, sec):
    """Diagnose the no-table rows from a prior report; never writes financials or state."""
    with open(args.source_report, encoding="utf-8") as source:
        previous = json.load(source)
    fixture_selection_path = getattr(args, "fixture_selection", "")
    selection = load_selection(fixture_selection_path)
    all_source_rows = [row for row in previous.get("rows", [])
                       if row.get("reason") == "no table shows this quarter's net income"]
    source_rows = all_source_rows
    cik_of = seed.parse_ticker_map(sec.get_json(TICKERS_URL) or {})
    filings_by_key, doc_by_key, rows, pool = {}, {}, [], []
    stop = None
    for item in source_rows:
        key, end = str(item.get("key", "")).upper(), item.get("end")
        if key not in doc_by_key:
            path = os.path.join(FIN_DIR, key + ".json")
            doc_by_key[key] = seed.read_json(path) if os.path.isfile(path) else None
        doc = doc_by_key[key]
        target = source_target(doc, end) if isinstance(doc, dict) else None
        cik = cik_of.get(key)
        record = {"key": key, "end": end, "storedEps": item.get("stored"),
                  "sourceReason": item.get("reason"), "inputDebugPresent": "debug" in item,
                  "targetNetIncome": target, "cik": cik}
        if not isinstance(doc, dict) or target is None:
            record.update(classHint="L9", hintNote="Financials file or Q4 netIncome target is missing.",
                          candidateFilingCount=0, candidateFilings=[])
            rows.append(_classified_row(record))
            continue
        if cik is None:
            record.update(classHint="L9", hintNote="Ticker is missing from the SEC company ticker map.",
                          candidateFilingCount=0, candidateFilings=[])
            rows.append(_classified_row(record))
            continue
        try:
            if key not in filings_by_key:
                filings_by_key[key] = filing_rows(sec, cik) if cik is not None else []
            texts = {}                      # document texts of this row only; dropped once its candidates are built
            diagnosis, _ = diagnose_quarter(sec, cik, filings_by_key[key], key, end, target, texts=texts)
            record.update(diagnosis)
            rows.append(_classified_row(record))
            pool.extend(_row_candidates(record, texts, selection))
        except (OutOfBudget, Forbidden, NetworkError) as exc:
            stop = f"{type(exc).__name__}: {exc}"
            break
    hints = Counter(row.get("classHint", "L9") for row in rows)
    fixtures, fixture_errors = select_fixtures(pool, selection)
    return {"mode": "diagnose", "sourceReport": os.path.relpath(args.source_report, REPO),
            "sourceRows": len(all_source_rows), "selectedSourceRows": len(source_rows),
            "diagnosed": len(rows), "classHintCounts": dict(sorted(hints.items())),
            "fixtureSelection": os.path.relpath(fixture_selection_path, REPO) if fixture_selection_path else None,
            "fixtureCount": len(fixtures), "fixtureErrors": fixture_errors, "fixtures": fixtures,
            "devTickers": sorted({fixture["key"] for fixture in fixtures}),
            "stopped": stop, "requests": sec.requests, "rows": rows}


def quarter_index(doc, end):
    ends = (doc.get("quarterly") or {}).get("end") or []
    return ends.index(end) if end in ends else None


def annual_net_income(doc, index):
    """The full-year net income of the fiscal year of quarter index (S2b L8 annual check), or None."""
    if index is None:
        return None
    fiscal = (doc.get("quarterly") or {}).get("fy") or []
    annual = doc.get("annual") or {}
    years, values = annual.get("fy") or [], (annual.get("values") or {}).get("netIncome") or []
    if index >= len(fiscal) or fiscal[index] not in years:
        return None
    position = years.index(fiscal[index])
    return values[position] if position < len(values) else None


def verify_quarter(sec, cik, filings, end, net, eps, doc):
    """
    Independent check: the next year's release shows this quarter again as its prior-year column.
    → {"status": "confirmed" | "conflict" | "unavailable", "epsDiluted": checked EPS on today's basis, "url"}.
    """
    when = date.fromisoformat(end)
    reason, debug = "no 8-K with Item 2.02 in the next year's window", []
    annual = annual_net_income(doc, quarter_index(doc, end))
    for filing in candidate_filings(filings, when + timedelta(days=365))[:MAX_FILINGS]:
        result, why = from_filing(sec, cik, filing, net, end, debug, annual)
        if result is None:
            reason = why or reason
            continue
        checked = round(result["epsDiluted"] / split_factor(doc, filing["filingDate"]), 6)
        status = "confirmed" if within_s3_tolerance(checked, eps) else "conflict"
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
        result, reason = find_quarter(sec, cik, filings, p["end"], p["target"], debug,
                                      annual=annual_net_income(doc, p["index"]))
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


def within_s3_tolerance(estimate, reference):
    """
    The S3 correctness tolerance (S3.3 "correct", S2a-2 (d) "confirmed"): |estimate - reference| <= max(0.011, 0.005 *
    |reference|), both on today's split basis.
    """
    return abs(estimate - reference) <= max(0.011, 0.005 * abs(reference))


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


def parse_shard(text):
    """'k/N' → (k, N) with N >= 1 and 0 <= k < N (S3.2)."""
    match = re.fullmatch(r"([0-9]+)/([0-9]+)", text.strip())
    if match is None or int(match.group(2)) < 1 or int(match.group(1)) >= int(match.group(2)):
        raise argparse.ArgumentTypeError(f"expected k/N with 0 <= k < N, got {text!r}")
    return int(match.group(1)), int(match.group(2))


def shard_keys(keys, shard=None):
    """The keys of one shard: the sorted unique keys whose index mod N is k (S3.2). No shard means every key."""
    ordered = sorted(set(keys))
    if shard is None:
        return ordered
    index, count = shard
    return [key for position, key in enumerate(ordered) if position % count == index]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_answer_set(path):
    """
    The answer set (S3.1) as {key: [row, ...]} with the rows of each key sorted by end. Every (key, end) must be
    unique, with a numeric value and netIncome. The file is read only.
    """
    with open(path, encoding="utf-8") as source:
        document = json.load(source)
    rows = document.get("rows") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{path}: no rows list")
    by_key, pairs = {}, set()
    for row in rows:
        pair = (row.get("key"), row.get("end"))
        if pair in pairs:
            raise ValueError(f"{path}: duplicate (key, end) {pair}")
        if not (_is_number(row.get("value")) and _is_number(row.get("netIncome"))):
            raise ValueError(f"{path}: {pair} has no numeric value and netIncome")
        pairs.add(pair)
        by_key.setdefault(row["key"], []).append(row)
    for key in by_key:
        by_key[key].sort(key=lambda row: row["end"])
    return by_key


def process_answer_rows(sec, key, cik, doc, answer_rows, rows):
    """
    The backtest of one ticker against the answer set (S3.2): only its (key, end) rows. The target is the row's
    netIncome and the stored reference is its value. The answer is a direct SEC fact, so it is never derived.
    """
    filings = filing_rows(sec, cik)
    for answer in answer_rows:
        end = answer["end"]
        debug = []
        result, reason = find_quarter(sec, cik, filings, end, answer["netIncome"], debug,
                                      annual=annual_net_income(doc, quarter_index(doc, end)))
        if result is not None:
            factor = split_factor(doc, result["filingDate"])
            if factor != 1:
                result = dict(result, asReported=result["epsDiluted"], splitFactor=factor,
                              epsDiluted=round(result["epsDiluted"] / factor, 6),
                              epsBasic=None if result["epsBasic"] is None else round(result["epsBasic"] / factor, 6),
                              dilutedShares=None if result["dilutedShares"] is None
                              else round(result["dilutedShares"] * factor, 1))
            result = dict(result, verified=verify_quarter(sec, cik, filings, end, result["net"],
                                                          result["epsDiluted"], doc))
        rows.append({"key": key, "end": end, "stored": answer["value"], "target": answer["netIncome"],
                     "derived": False, "result": result, "reason": reason, "debug": debug[:2]})
    return True


def _wilson_lower(correct, n):
    if not n:
        return None
    z2 = WILSON_Z * WILSON_Z
    p = correct / n
    return (p + z2 / (2 * n) - WILSON_Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


def _sign(value):
    return (value > 0) - (value < 0)


def gate_metrics(rows, answer_count):
    """S3.3 metrics over gate rows (one per answer-set row that some report holds), with per-FY precision."""
    extracted = [row for row in rows if row["estimate"] is not None]
    accepted = [row for row in extracted if row["status"] == "confirmed"]
    correct = [row for row in accepted if within_s3_tolerance(row["estimate"], row["answer"])]
    per_fy = {}
    for row in accepted:
        per_fy.setdefault(str(row["fy"]), []).append(row)
    fy_table = {}
    for fy in sorted(per_fy):
        group = per_fy[fy]
        ok = sum(within_s3_tolerance(row["estimate"], row["answer"]) for row in group)
        fy_table[fy] = {"accepted": len(group), "correct": ok, "precision": round(ok / len(group), 4),
                        "checked": len(group) >= 20}
    n = len(accepted)
    return {"answerRows": answer_count, "extracted": len(extracted), "accepted": n, "correct": len(correct),
            "precision": round(len(correct) / n, 4) if n else None,
            "wilsonLower95": round(_wilson_lower(len(correct), n), 4) if n else None,
            "signErrors": sum(_sign(row["estimate"]) != _sign(row["answer"]) for row in accepted if row["answer"] != 0),
            "conflicts": sum(row["status"] == "conflict" for row in extracted),
            "coverage": round(n / answer_count, 4) if answer_count else None,
            "perFY": fy_table}


def load_dev_tickers(path=None):
    with open(path or DEV_FIXTURES, encoding="utf-8") as source:
        return sorted({str(ticker).upper() for ticker in json.load(source)["devTickers"]})


def run_gate(args):
    """
    --mode gate (S3.3), offline. Every (key, end) of the answer set must be in exactly one row of the --reports;
    otherwise nothing is written and the exit code is GATE_EXIT. Report rows outside the answer set are counted
    as extraRows and left out of every metric.
    """
    answer = load_answer_set(args.answer_set)
    pairs = {(key, row["end"]): row for key, rows in answer.items() for row in rows}
    seen, reported = Counter(), []
    for path in args.reports:
        with open(path, encoding="utf-8") as source:
            report = json.load(source)
        for row in report.get("rows") or []:
            pair = (row.get("key"), row.get("end"))
            seen[pair] += 1
            reported.append((pair, row))
    missing = sorted(pair for pair in pairs if seen[pair] == 0)
    doubled = sorted(pair for pair in pairs if seen[pair] > 1)
    if missing or doubled:
        print(f"gate: {len(missing)} answer rows are in no report and {len(doubled)} are in more than one; "
              "no summary written", file=sys.stderr, flush=True)
        for label, listed in (("missing", missing), ("in more than one report", doubled)):
            for key, end in listed[:10]:
                print(f"  {label}: {key} {end}", file=sys.stderr, flush=True)
        return GATE_EXIT
    rows, extra = [], 0
    for pair, row in reported:
        if pair not in pairs:
            extra += 1
            continue
        answer_row, result = pairs[pair], row.get("result")
        verified = (result or {}).get("verified") or {}
        rows.append({"key": pair[0], "end": pair[1], "fy": answer_row.get("fy"), "answer": answer_row["value"],
                     "accession": answer_row.get("accession"), "estimate": (result or {}).get("epsDiluted"),
                     "url": (result or {}).get("url"), "reason": row.get("reason"),
                     "status": verified.get("status"), "checked": verified.get("epsDiluted"),
                     "checkUrl": verified.get("url")})
    dev = set(load_dev_tickers())
    non_dev_pairs = [pair for pair in pairs if pair[0] not in dev]
    non_dev = [row for row in rows if row["key"] not in dev]
    incorrect = [row for row in rows if row["status"] == "confirmed" and row["estimate"] is not None
                 and not within_s3_tolerance(row["estimate"], row["answer"])]
    signs = [row for row in rows if row["status"] == "confirmed" and row["answer"] != 0
             and _sign(row["estimate"]) != _sign(row["answer"])]
    conflicts = [row for row in rows if row["status"] == "conflict"]

    def listed(row):
        return {key: row[key] for key in ("key", "end", "estimate", "answer", "checked", "accession", "url",
                                          "checkUrl")}

    summary = {"mode": "gate", "answerSet": os.path.relpath(args.answer_set, REPO),
               "reports": list(args.reports), "answerRows": len(pairs), "reportRows": len(reported),
               "extraRows": extra, "devTickers": sorted(dev), "all": gate_metrics(rows, len(pairs)),
               "excludingDevTickers": gate_metrics(non_dev, len(non_dev_pairs)),
               "reasons": dict(Counter(row["reason"] for row in rows if row["estimate"] is None).most_common()),
               "incorrect": [listed(row) for row in incorrect], "signErrors": [listed(row) for row in signs],
               "conflicts": [listed(row) for row in conflicts]}
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as file:
            file.write(text + "\n")
    print(text, flush=True)
    return 0


def run(args, sec):
    if args.mode == "diagnose":
        return run_diagnose(args, sec)
    cik_of = seed.parse_ticker_map(sec.get_json(TICKERS_URL) or {})
    state = load_state(STATE_PATH)
    answer = load_answer_set(args.answer_set) if args.answer_set else None
    if answer is not None:
        keys = shard_keys(answer, args.shard)
    else:
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
            if answer is None:
                complete = process_ticker(sec, key, cik, doc, args.mode, ticker_rows)
            else:
                complete = process_answer_rows(sec, key, cik, doc, answer[key], ticker_rows)
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


def _inside_repo(path):
    real, root = os.path.realpath(path), os.path.realpath(REPO)
    return real == root or real.startswith(root + os.sep)


def build_s1_classes(report, env=None):
    """The s1-classes.json document (plan §M4): one record per diagnosed row, class counts and dispositions."""
    env = os.environ if env is None else env
    records = sorted(({"key": row.get("key"), "end": row.get("end"),
                       "class": (row.get("classification") or {}).get("class"),
                       "accession": (row.get("classification") or {}).get("accession"),
                       "url": (row.get("classification") or {}).get("url"),
                       "tableIndex": (row.get("classification") or {}).get("tableIndex"),
                       "tableEpsReason": (row.get("classification") or {}).get("tableEpsReason"),
                       "note": (row.get("classification") or {}).get("note")}
                      for row in report.get("rows") or []),
                     key=lambda record: (record["key"] or "", record["end"] or ""))
    counts = {cls: 0 for cls in S1_CLASS_NAMES}
    for record in records:
        counts[record["class"]] = counts.get(record["class"], 0) + 1
    fixtures = report.get("fixtures") or []
    positives = Counter(item.get("class") for item in fixtures if item.get("role") == "positive")
    dispositions = {"L1": "S2 handles", "L2": "S2 handles", "L3": "S2 handles", "L8": "S2 handles",
                    "L4": "out of scope", "L5": "out of scope", "L6": "out of scope", "L7": "out of scope",
                    "L10": "out of scope", "L9": "needs Opus"}
    classes = {}
    for cls in S1_CLASS_NAMES:
        disposition = dispositions[cls]
        if disposition == "S2 handles" and positives[cls] < 2:
            disposition = "out of scope"
        examples = [record["url"] for record in records if record["class"] == cls and record["url"]][:2]
        classes[cls] = {"name": S1_CLASS_NAMES[cls], "count": counts[cls], "examples": examples,
                        "disposition": disposition}
    row_count = len(records)
    other = counts.get("L9", 0)
    run_id = env.get("GITHUB_RUN_ID")
    return {"schemaVersion": 2, "generator": "classify_row",
            "status": "complete" if report.get("diagnosed") == report.get("sourceRows") and not report.get("stopped")
            else "incomplete",
            "diagnoseRun": {"runId": int(run_id) if run_id and run_id.isdigit() else None,
                            "mainCommit": env.get("GITHUB_SHA") or None,
                            "requests": report.get("requests"), "stopped": report.get("stopped")},
            "sourceReport": report.get("sourceReport"), "sourceRows": report.get("sourceRows"),
            "rowCount": row_count, "classCounts": counts,
            "otherLimit": {"limit": row_count // 10, "count": other, "passed": other <= row_count // 10},
            "classes": classes,
            "devTickers": sorted({str(item.get("key")) for item in fixtures}),
            "fixtureCount": len(fixtures), "fixtureErrors": report.get("fixtureErrors") or [],
            "rows": records}


def write_diagnose_outputs(report, out_dir):
    """Writes s1-classes.json, fixtures/*.html and fixtures/s1_fixtures.json under out_dir and nothing else."""
    fixtures_dir = os.path.join(out_dir, "fixtures")
    os.makedirs(fixtures_dir, exist_ok=True)
    manifest = []
    for item in report.get("fixtures") or []:
        data = item["html"].encode("utf-8")
        with open(os.path.join(fixtures_dir, item["file"]), "wb") as file:
            file.write(data)
        record = {key: value for key, value in item.items() if key != "html"}
        record.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest.append(record)
    with open(os.path.join(fixtures_dir, "s1_fixtures.json"), "w", encoding="utf-8") as file:
        json.dump({"schemaVersion": 2, "devTickers": sorted({str(r.get("key")) for r in manifest}),
                   "fixtures": manifest}, file, ensure_ascii=False, indent=2)
        file.write("\n")
    with open(os.path.join(out_dir, "s1-classes.json"), "w", encoding="utf-8") as file:
        json.dump(build_s1_classes(dict(report, fixtures=manifest)), file, ensure_ascii=False, indent=2)
        file.write("\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Q4 EPS from 8-K earnings releases (see module docstring)")
    parser.add_argument("--mode", choices=("diagnose", "backtest", "fill", "gate"), default="backtest")
    parser.add_argument("--tickers", default="", help="comma separated; empty = every US file")
    parser.add_argument("--limit", type=int, default=0, help="only the first N files (0 = all)")
    parser.add_argument("--budget", type=int, default=6000, help="SEC requests this run")
    parser.add_argument("--report", default="", help="also write the report JSON to this path")
    parser.add_argument("--source-report", default=DIAGNOSE_SOURCE,
                        help="backtest report whose no-table rows are inspected in diagnose mode")
    parser.add_argument("--fixture-selection", default="",
                        help="optional JSON manifest selecting SEC table HTML to include in diagnose output")
    parser.add_argument("--out-dir", default="",
                        help="diagnose only: write s1-classes.json and fixtures/ here (must be outside the repository)")
    parser.add_argument("--verbose-report", default="",
                        help="diagnose only: write the full per-row evidence JSON to this path")
    parser.add_argument("--answer-set", default="",
                        help="backtest only: the answer set (S3.1); only its (key, end) rows are processed")
    parser.add_argument("--shard", type=parse_shard, default=None,
                        help="with --answer-set: k/N, the sorted answer-set keys with index mod N equal to k")
    parser.add_argument("--reports", nargs="+", default=[],
                        help="gate only: backtest reports to merge")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.mode == "gate":
        if not args.answer_set or not args.reports or args.out_dir:
            print("--mode gate needs --answer-set and --reports, and no --out-dir", file=sys.stderr, flush=True)
            return 2
        try:
            return run_gate(args)
        except (OSError, ValueError) as exc:
            print(f"gate: {exc}", file=sys.stderr, flush=True)
            return 2
    if args.reports:
        print("--reports is only valid with --mode gate", file=sys.stderr, flush=True)
        return 2
    if args.answer_set:
        if args.mode != "backtest" or args.tickers:
            print("--answer-set needs --mode backtest and no --tickers", file=sys.stderr, flush=True)
            return 2
        try:
            load_answer_set(args.answer_set)
        except (OSError, ValueError) as exc:
            print(f"--answer-set: {exc}", file=sys.stderr, flush=True)
            return 2
    if args.shard is not None and not args.answer_set:
        print("--shard needs --answer-set", file=sys.stderr, flush=True)
        return 2
    if args.out_dir:
        if args.mode != "diagnose":
            print("--out-dir is only valid with --mode diagnose", file=sys.stderr, flush=True)
            return 2
        if _inside_repo(args.out_dir):
            print("--out-dir must be outside the repository; nothing was fetched", file=sys.stderr, flush=True)
            return 2
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
        # stdout may be redirected by Actions to an artifact path; the script itself writes only --out-dir and
        # --verbose-report, and only when they are given.
        if args.verbose_report:
            with open(args.verbose_report, "w", encoding="utf-8") as file:
                json.dump({key: value for key, value in report.items() if key != "fixtures"}, file,
                          ensure_ascii=False, indent=2)
                file.write("\n")
        if args.out_dir:
            write_diagnose_outputs(report, args.out_dir)
            printed = {key: value for key, value in report.items() if key not in ("rows", "fixtures")}
        else:
            printed = report
        print(json.dumps(printed, ensure_ascii=False, indent=2), flush=True)
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
