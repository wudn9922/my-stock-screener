#!/usr/bin/env python3
"""
Q4 EPS from the 8-K earnings release (EX-99.1) of US issuers, for the Q4 periods that have none.

A table is a candidate only when expanded HTML row/column geometry identifies one current Q4
column group and one GAAP total diluted EPS row in that group. Stored net income, sign, units
and share-formula checks are diagnostics and review flags; they do not reject a reported EPS.
Tables from one filing must agree on diluted EPS.

Modes:
  backtest  run the same extraction on Q4 periods that already hold a diluted EPS and report the
            errors; writes nothing.
  fill      write uniquely validated original-exhibit values into null fields only (never overwrites).
            Sign/magnitude mismatches stay as review candidates and remain null until audit approval.

SEC access: User-Agent from SEC_USER_AGENT (or the repo default), at most about 5 requests per second,
stop at the first HTTP 403, and --budget requests per run. docs/report/fundamentals/q4-8k-state.json
records finished tickers so the next run continues where this one stopped.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib import error, request

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
import repair_q4_eps as repair  # noqa: E402
import seed_financials_sec as seed  # noqa: E402

REPO = os.path.dirname(os.path.dirname(SCRIPTS))
FIN_DIR = os.path.join(REPO, "docs", "report", "financials")
STATE_PATH = os.path.join(REPO, "docs", "report", "fundamentals", "q4-8k-state.json")
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
PERIOD_END_TOLERANCE_DAYS = 7  # SEC reportDate is authoritative for 52/53-week quarters.

NI_RE = re.compile(r"net\s*(\(loss\)\s*|loss\s*)?(income|earnings)|net\s+loss")
# non-GAAP and adjusted rows sit next to the GAAP rows in most releases and must never be picked
NON_GAAP = ("non-gaap", "non gaap", "nongaap", "adjusted", "excluding", "pro forma")
NI_AVOID = ("per share", "noncontrolling", "non-controlling", "minority", "continuing", "discontinued",
            "before", "comprehensive") + NON_GAAP
EPS_AVOID = ("weighted", "shares", "number of", "average", "continuing", "discontinued") + NON_GAAP


class Forbidden(RuntimeError):
    """SEC answered HTTP 403: the run stops at once."""


class OutOfBudget(RuntimeError):
    """The --budget of SEC requests is used up: the run pauses and the next run continues."""


class NetworkError(RuntimeError):
    """SEC could not be reached after the retries."""


@dataclass
class HtmlCell:
    text: str
    tag: str = "td"
    attrs: dict = field(default_factory=dict)
    colspan: int = 1
    rowspan: int = 1
    footnote_markers: list = field(default_factory=list)


@dataclass
class ParsedTable:
    rows: list
    caption: str = ""
    attrs: dict = field(default_factory=dict)


class _TableParser(HTMLParser):
    """Extract table cells, including spans and superscript markers, from SEC HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []

    @staticmethod
    def _attrs(attrs):
        return {str(key).lower(): value for key, value in attrs}

    @staticmethod
    def _positive_span(attrs, name):
        try:
            return max(1, min(100, int(attrs.get(name, 1))))
        except (TypeError, ValueError):
            return 1

    def _finish_cell(self, state):
        cell = state.get("cell")
        if cell is None or state.get("row") is None:
            return
        text = " ".join("".join(cell["text"]).split())
        markers = [" ".join("".join(parts).split()) for parts in cell["sup"]]
        markers = [marker for marker in markers if marker]
        state["row"].append(HtmlCell(text=text, tag=cell["tag"], attrs=cell["attrs"],
                                     colspan=self._positive_span(cell["attrs"], "colspan"),
                                     rowspan=self._positive_span(cell["attrs"], "rowspan"),
                                     footnote_markers=markers))
        state["cell"] = None

    def _finish_row(self, state):
        self._finish_cell(state)
        if state.get("row") is not None:
            state["rows"].append(state["row"])
            state["row"] = None

    def handle_starttag(self, tag, attrs):
        attrs = self._attrs(attrs)
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None, "caption": [], "in_caption": False,
                                "attrs": attrs})
            return
        if not self._stack:
            return
        state = self._stack[-1]
        if tag == "caption":
            state["in_caption"] = True
        elif tag == "tr":
            self._finish_row(state)
            state["row"] = []
        elif tag in ("td", "th"):
            if state["row"] is None:
                state["row"] = []
            self._finish_cell(state)
            state["cell"] = {"tag": tag, "attrs": attrs, "text": [], "sup": [], "sup_depth": 0}
        elif tag == "sup" and state.get("cell") is not None:
            state["cell"]["sup_depth"] += 1

    def handle_endtag(self, tag):
        if not self._stack:
            return
        state = self._stack[-1]
        if tag == "sup" and state.get("cell") is not None:
            state["cell"]["sup_depth"] = max(0, state["cell"]["sup_depth"] - 1)
        elif tag in ("td", "th"):
            self._finish_cell(state)
        elif tag == "tr":
            self._finish_row(state)
        elif tag == "caption":
            state["in_caption"] = False
        elif tag == "table":
            self._finish_row(state)
            self._stack.pop()
            self.tables.append(ParsedTable(rows=state["rows"],
                                           caption=" ".join("".join(state["caption"]).split()),
                                           attrs=state["attrs"]))

    def handle_data(self, data):
        if not self._stack:
            return
        state = self._stack[-1]
        cell = state.get("cell")
        if cell is not None:
            (cell["sup"] if cell["sup_depth"] else cell["text"]).append(data)
        elif state.get("in_caption"):
            state["caption"].append(data)


def structured_tables_of(text):
    """Return parsed tables while retaining the original row/column geometry."""
    parser = _TableParser()
    parser.feed(text or "")
    parser.close()
    return parser.tables


def tables_of(text):
    """Compatibility view: rows of display text for filing-index scans and small callers."""
    return [[[cell.text for cell in row] for row in table.rows] for table in structured_tables_of(text)]


def number(text):
    """Parse a financial number without losing negative parentheses or zero values."""
    compact = re.sub(r"[\s,$\u00a0]", "", str(text or "")).replace("−", "-")
    if compact in DASHES:
        return None
    compact = re.sub(r"[*†‡]+$", "", compact)
    # SEC HTML often puts a footnote number in a sup element. The parser stores that
    # marker separately; tolerate a plain-text parenthesized suffix as well.
    if re.fullmatch(r"\(?\d+(?:\.\d+)?\)?\(\d+\)", compact):
        compact = re.sub(r"\(\d+\)$", "", compact)
    negative = compact[:1] in ("(", "-")
    digits = compact.strip("()-")
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
    low = (label or "").lower()
    return bool(NI_RE.search(low)) and not any(word in low for word in NI_AVOID)


def is_eps(label, kind):
    low = (label or "").lower()
    return (re.search(rf"\b{re.escape(kind)}\b", low) is not None
            and re.search(r"\b(?:per\s+share|eps|earnings\s+per)\b", low) is not None
            and not re.search(r"\b(?:ads|adr|depositary|depository)\b", low)
            and not any(word in low for word in EPS_AVOID))


def is_diluted_shares(label):
    low = (label or "").lower()
    return ("diluted" in low and ("weighted" in low or "shares" in low or "share count" in low)
            and "per share" not in low and not any(word in low for word in NON_GAAP))


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]


def shows_period(text, end):
    """The table names the quarter's month and year, e.g. 'Three Months Ended January 31, 2026'."""
    when = date.fromisoformat(end)
    name = MONTHS[when.month - 1]
    pattern = rf"\b(?:{name}|{name[:3]})\.?\s+\d{{1,2}},?\s+{when.year}\b"
    return re.search(pattern, text, re.I) is not None


def split_factor(doc, filing_date, period_end):
    """Return a post-filing factor only when full, symbol-bound split history is verified."""
    factor, failure = repair._verified_split_factor(
        doc, period_end, {"filed": filing_date})
    return None if failure else factor


@dataclass
class GridEntry:
    cell: HtmlCell
    row: int
    col: int


def _parsed_table(table):
    if isinstance(table, ParsedTable):
        return table
    rows = [[cell if isinstance(cell, HtmlCell) else HtmlCell(str(cell or "")) for cell in row]
            for row in (table or [])]
    return ParsedTable(rows=rows)


def _table_grid(table):
    """Expand HTML rowspan/colspan into a sparse rectangular grid with origin evidence."""
    parsed = _parsed_table(table)
    grid = [[] for _ in parsed.rows]

    def ensure(row, col):
        while len(grid[row]) <= col:
            grid[row].append(None)

    for row_index, cells in enumerate(parsed.rows):
        col = 0
        for cell in cells:
            while col < len(grid[row_index]) and grid[row_index][col] is not None:
                col += 1
            entry = GridEntry(cell=cell, row=row_index, col=col)
            for row in range(row_index, min(len(grid), row_index + cell.rowspan)):
                for column in range(col, col + cell.colspan):
                    ensure(row, column)
                    if grid[row][column] is None:
                        grid[row][column] = entry
            col += cell.colspan
    width = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([None] * (width - len(row)))
    return parsed, grid


def _own_entries(grid, row_index):
    seen = set()
    entries = []
    for entry in grid[row_index]:
        if entry is None or entry.row != row_index or (entry.row, entry.col) in seen:
            continue
        seen.add((entry.row, entry.col))
        entries.append(entry)
    return sorted(entries, key=lambda entry: entry.col)


def _row_info(grid, row_index):
    entries = _own_entries(grid, row_index)
    label = None
    values = []
    for index, entry in enumerate(entries):
        text = entry.cell.text.strip()
        value = number(text)
        if value is not None:
            previous = entries[index - 1].cell.text.strip() if index > 0 else ""
            following = entries[index + 1].cell.text.strip() if index + 1 < len(entries) else ""
            # Some SEC exhibits split accounting parentheses into their own cells.
            # Preserve that sign when the numeric token itself is unsigned.
            if not text.startswith(("(", "-", "−")) and \
                    (previous.endswith("(") or previous in ("(", "($") or following.startswith(")")):
                value = -abs(value)
            values.append((value, entry))
        elif label is None and text not in ("", "$", "(", ")", "%") and re.search(r"[A-Za-z]", text):
            label = text
    return {"label": label, "values": values, "entries": entries}


def _number_in_columns(info, columns):
    out = []
    for value, entry in info["values"]:
        covered = range(entry.col, entry.col + entry.cell.colspan)
        if any(column in covered for column in columns):
            out.append((value, entry))
    return out


def _has_target_date(text, end):
    when = date.fromisoformat(end)
    # SEC exhibits sometimes place the year immediately after the comma in adjacent column text.
    text = re.sub(r",(?=\d{4}\b)", ", ", str(text))
    abbreviations = {"september": "sep", "january": "jan", "february": "feb", "march": "mar",
                     "april": "apr", "june": "jun", "july": "jul", "august": "aug",
                     "october": "oct", "november": "nov", "december": "dec", "may": "may"}
    month_pattern = "|".join(rf"(?:{re.escape(month)}|{abbreviations[month]})\.?" for month in MONTHS)
    for match in re.finditer(rf"\b({month_pattern})\s+0?(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(\d{{4}})\b", text, re.I):
        month_token = match.group(1).rstrip(".").lower()
        month_number = next((index + 1 for index, month in enumerate(MONTHS)
                             if month == month_token or abbreviations[month] == month_token), None)
        try:
            candidate = date(int(match.group(3)), month_number, int(match.group(2)))
        except (TypeError, ValueError):
            continue
        if abs((candidate - when).days) <= PERIOD_END_TOLERANCE_DAYS:
            return True
    for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text):
        year = int(match.group(3))
        if year < 100:
            year += when.year // 100 * 100
            if abs(year - when.year) > 5:
                year += 100 if year < when.year else -100
        try:
            candidate = date(year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            continue
        if abs((candidate - when).days) <= PERIOD_END_TOLERANCE_DAYS:
            return True
    return False


QUARTER_HEADER_RE = re.compile(r"\b(?:three|3)\s*[- ]?months?\b|\bquarter(?:ly)?\b|\b3\s*[- ]?mos?\.?\b",
                                re.I)
ANNUAL_HEADER_RE = re.compile(r"\b(?:twelve|12)\s*[- ]?months?\b|\byears?\s+ended\b|\byear\s+ended\b|\bfiscal\s+year\b",
                              re.I)
FINANCIAL_LABEL_RE = re.compile(
    r"\b(?:revenue|sales|income|earnings|profit|loss|cash|expense|cost|shares?|assets?|liabilities|equity|eps)\b",
    re.I,
)


def _non_gaap(text):
    return any(term in (text or "").lower() for term in NON_GAAP)


def _explicit_gaap(text):
    without_non_gaap = re.sub(r"\bnon[- ]?gaap\b", " ", text or "", flags=re.I)
    return re.search(r"\bgaap\b", without_non_gaap, re.I) is not None


def _is_target_quarter_header(text, end):
    return (_has_target_date(text, end) and QUARTER_HEADER_RE.search(text) is not None
            and ANNUAL_HEADER_RE.search(text) is None and not _non_gaap(text))


def _unit_scale(text):
    low = (text or "").lower()
    if re.search(r"\bbillions?\b", low):
        return 1e9
    if re.search(r"\bmillions?\b", low):
        return 1e6
    if re.search(r"\bthousands?\b", low):
        return 1e3
    return None


def _table_units(parsed):
    texts = [parsed.caption]
    for row in parsed.rows:
        texts.extend(cell.text for cell in row)
    joined = " ".join(text for text in texts if text)
    unit_rows = [text for text in texts if re.search(r"\bin\s+(?:thousands?|millions?|billions?)\b|"
                                                    r"\b(?:thousands?|millions?|billions?)\s+of\s+"
                                                    r"(?:dollars?|shares?)\b|\bexcept\s+per\s+share\b", text, re.I)]
    share_match = re.search(r"\bshares?\s*(?:\(\s*)?in\s+(thousands?|millions?|billions?)\b|"
                            r"\b(thousands?|millions?|billions?)\s+of\s+shares?\b", joined, re.I)
    share_scale = _unit_scale(next(group for group in share_match.groups() if group)) if share_match else None
    # Do not let an explicit share unit become the dollar scale just because it
    # appears first in the caption. The table-wide convention remains the fallback.
    money_text = re.sub(r"\bshares?\s*(?:\(\s*)?in\s+(?:thousands?|millions?|billions?)\b|"
                        r"\b(?:thousands?|millions?|billions?)\s+of\s+shares?\b", " ", joined, flags=re.I)
    explicit_money = set()
    for match in re.finditer(r"\b(?:dollars?|amounts?)\s*(?:\(\s*)?in\s+(thousands?|millions?|billions?)\b|"
                             r"\b(thousands?|millions?|billions?)\s+of\s+(?:dollars?|amounts?)\b", joined, re.I):
        explicit_money.add(_unit_scale(next(group for group in match.groups() if group)))
    general_money = {_unit_scale(match.group(1)) for match in re.finditer(
        r"\bin\s+(thousands?|millions?|billions?)\b", money_text, re.I)}
    general_money.discard(None)
    money_scales = explicit_money | general_money
    unit_ambiguous = len(money_scales) > 1
    money_scale = next(iter(money_scales)) if len(money_scales) == 1 else None
    if money_scale is None and not unit_ambiguous:
        money_scale = _unit_scale(money_text)
    if share_scale is None:
        share_scale = money_scale or _unit_scale(joined)
    return {"text": joined, "unitRows": unit_rows[:8], "moneyScale": money_scale,
            "shareScale": share_scale, "unitAmbiguous": unit_ambiguous}


def _eps_currency_evidence(parsed, row_info, expected_currency=None, column_header="", selected_column=None):
    """Classify the currency from the EPS row's table context; never infer it from EPS math."""
    expected = str(expected_currency or "").upper().strip() or None
    header_text = str(column_header or "")
    caption = str(parsed.caption or "")
    table_units = _table_units(parsed)
    table_scope = " ".join([caption] + list(table_units.get("unitRows") or []))
    entries = row_info.get("entries", [])
    selected_entry = None
    if selected_column is not None:
        selected_entry = next((entry for entry in entries
                               if entry.col <= selected_column < entry.col + entry.cell.colspan), None)
    row_label = str(row_info.get("label") or "")
    selected_parts = [row_label, header_text]
    if selected_entry is not None:
        selected_parts.append(selected_entry.cell.text)
        selected_col_start = selected_entry.col
        selected_col_end = selected_entry.col + selected_entry.cell.colspan - 1
        currency_only = re.compile(
            r"^(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|RMB|INR|KRW|US\$|C\$|A\$|[$€£¥₹₩]|"
            r"U\.?S\.?\s+dollars?|Canadian\s+dollars?)$", re.I)
        for entry in entries:
            if entry is selected_entry:
                continue
            if entry.col == selected_col_start - 1 or entry.col == selected_col_end + 1:
                neighbor = entry.cell.text.strip()
                if currency_only.fullmatch(neighbor):
                    selected_parts.append(neighbor)
    selected_scope = " ".join(selected_parts)
    scope = " ".join((table_scope, selected_scope))
    foreign_codes = {
        "EUR": r"\bEUR\b|€", "GBP": r"\bGBP\b|£", "JPY": r"\bJPY\b|¥",
        "CAD": r"\bCAD\b|C\$|\bCanadian\s+dollars?\b",
        "AUD": r"\bAUD\b|A\$|\bAustralian\s+dollars?\b", "CHF": r"\bCHF\b|\bSwiss\s+francs?\b",
        "CNY": r"\bCNY\b|RMB|CN¥", "INR": r"\bINR\b|₹", "KRW": r"\bKRW\b|₩",
    }
    selected_foreign = [code for code, pattern in foreign_codes.items()
                        if re.search(pattern, selected_scope, re.I)]
    table_foreign = [code for code, pattern in foreign_codes.items()
                     if re.search(pattern, table_scope, re.I)]
    explicit_usd_selected = re.search(
        r"\bUSD\b|\bU\.?S\.?\s+dollars?\b|\bUnited States dollars?\b", selected_scope, re.I)
    explicit_usd_table = re.search(
        r"\bUSD\b|\bU\.?S\.?\s+dollars?\b|\bUnited States dollars?\b", table_scope, re.I)
    dollar_symbol = bool(re.search(r"[$€£¥₹₩]", selected_scope))
    if expected and expected != "USD":
        return {"currency": expected, "status": "currency-conflict"
                if explicit_usd_selected or explicit_usd_table or dollar_symbol else "non-usd",
                "evidence": scope[:500], "expectedCurrency": expected}
    if selected_foreign:
        conflict = bool(explicit_usd_selected or dollar_symbol or len(set(selected_foreign)) > 1)
        return {"currency": selected_foreign[0] if len(set(selected_foreign)) == 1 else None,
                "status": "ambiguous" if conflict else "non-usd",
                "evidence": scope[:500], "expectedCurrency": expected}
    # A selected column can disambiguate a multilingual/multi-currency table caption.
    if explicit_usd_selected:
        return {"currency": "USD", "status": "verified-usd", "evidence": scope[:500],
                "expectedCurrency": expected}
    if table_foreign:
        return {"currency": table_foreign[0] if len(set(table_foreign)) == 1 else None,
                "status": "ambiguous" if explicit_usd_table or len(set(table_foreign)) > 1 else "non-usd",
                "evidence": scope[:500], "expectedCurrency": expected}
    if explicit_usd_table:
        return {"currency": "USD", "status": "verified-usd", "evidence": scope[:500],
                "expectedCurrency": expected}
    if dollar_symbol and not table_foreign:
        return {"currency": None, "status": "dollar-symbol-currency-ambiguous",
                "evidence": scope[:500], "expectedCurrency": expected}
    return {"currency": None, "status": "unknown", "evidence": scope[:500],
            "expectedCurrency": expected}


def _is_financial_data_row(info):
    return bool(info["label"] and info["values"] and FINANCIAL_LABEL_RE.search(info["label"]))


def _header_contexts(parsed, grid):
    infos = [_row_info(grid, row) for row in range(len(grid))]
    data_start = next((index for index, info in enumerate(infos) if _is_financial_data_row(info)), len(grid))
    contexts = []
    for column in range(len(grid[0]) if grid else 0):
        pieces = []
        for row in range(data_start):
            entry = grid[row][column]
            if entry is not None and entry.cell.text and entry.cell.text not in pieces:
                pieces.append(entry.cell.text)
        contexts.append({"text": " ".join(pieces),
                         "key": tuple((grid[row][column].row, grid[row][column].col,
                                       grid[row][column].cell.text)
                                      for row in range(data_start)
                                      if grid[row][column] is not None and grid[row][column].cell.text)})
    return infos, data_start, contexts


def _eps_kind(label, infos, row_index):
    low = (label or "").lower()
    section = str((infos[row_index] if 0 <= row_index < len(infos) else {}).get("sectionContext") or "").lower()
    if _non_gaap(low) or any(word in low for word in ("continuing", "discontinued")):
        return None, False
    has_basic = re.search(r"\bbasic\b", low) is not None
    has_diluted = re.search(r"\bdiluted\b", low) is not None
    if has_basic == has_diluted:
        return None, False
    kind = "basic" if has_basic else "diluted"
    if is_eps(low, kind):
        return kind, True
    if re.fullmatch(r"(?:basic|diluted)[\s:]*", low):
        share_headers = list(re.finditer(
            r"\bweighted[-\s]?average shares?\b[^:;,.]{0,100}|"
            r"\bshares of common stock outstanding\b[^:;,.]{0,100}", section))
        eps_headers = [match for match in re.finditer(
            r"\b(?:earnings\s+per\s+share|per\s+share|eps)\b", section)
            if not any(header.start() <= match.start() < header.end() for header in share_headers)]
        if share_headers and (not eps_headers or share_headers[-1].start() > eps_headers[-1].start()):
            return None, False
        for prior in range(row_index - 1, -1, -1):
            info = infos[prior]
            previous = (info["label"] or "").lower()
            if not previous:
                continue
            if _non_gaap(previous) or any(word in previous for word in ("continuing", "discontinued")):
                return None, False
            if "per share" in previous or "eps" in previous:
                return kind, False
            if _is_financial_data_row(info) and not re.fullmatch(r"(?:basic|diluted)[\s:]*", previous):
                return None, False
    return None, False


def _share_class_or_ownership(label):
    return re.search(r"\b(?:class|series)\s+[a-z0-9]+\b|\bpreferred\b|\bparticipating\b|"
                     r"\bnoncontrolling\b|\bminority\b|\b(?:ads|adr|depositary|depository)\b",
                     (label or "").lower()) is not None


def _row_section_contexts(infos):
    """Carry explicit GAAP/adjusted/operations section headings into later data rows."""
    active = []
    for info in infos:
        label = (info.get("label") or "").strip()
        if label and not info.get("values"):
            if (_non_gaap(label) or _explicit_gaap(label)
                    or re.search(r"\b(?:continuing|discontinued)\s+operations\b", label, re.I)):
                active = [label]
            else:
                active.append(label)
        info["sectionContext"] = " ".join(active)
    return infos


def _footnote_evidence(parsed):
    markers = []
    notes = []
    for row_index, row in enumerate(parsed.rows):
        row_text = " ".join(cell.text for cell in row if cell.text).strip()
        for cell_index, cell in enumerate(row):
            for marker in cell.footnote_markers:
                markers.append({"row": row_index, "cell": cell_index, "marker": marker})
        if row_text and re.match(r"^(?:\(?\d+\)?[.)]?|[a-z][.)]|[*†‡])\s+", row_text, re.I):
            notes.append({"row": row_index, "text": row_text[:240]})
    return {"markers": markers[:20], "rows": notes[:12]}


def _matching_net_income(net_rows, target, declared_scale):
    matches = []
    if target is None:
        return matches
    for record in net_rows:
        value = record["values"][0][0]
        scales = (declared_scale,) if declared_scale else SCALES
        for scale in scales:
            if scale and abs(value * scale - target) <= max(NI_TOLERANCE * abs(target), 0.5 * scale):
                matches.append((record, scale))
    return matches


def table_eps(table, target, end, table_index=None, expected_currency=None):
    """Extract one reported diluted EPS using explicit row labels and period-column headers."""
    parsed, grid = _table_grid(table)
    if not grid or not any(grid):
        return None, None
    infos, data_start, contexts = _header_contexts(parsed, grid)
    infos = _row_section_contexts(infos)
    if not contexts:
        return None, None

    table_scope = " ".join([parsed.caption or ""] + [context["text"] for context in contexts])
    scope_has_non_gaap = _non_gaap(table_scope)
    scope_has_gaap = _explicit_gaap(table_scope)
    non_gaap_only_scope = scope_has_non_gaap and not scope_has_gaap
    target_columns = [column for column, context in enumerate(contexts)
                      if (_has_target_date(context["text"], end)
                          and QUARTER_HEADER_RE.search(context["text"]) is not None
                          and ANNUAL_HEADER_RE.search(context["text"]) is None)
                      and not (_non_gaap(context["text"]) and not _explicit_gaap(context["text"]))
                      and (not non_gaap_only_scope or _explicit_gaap(context["text"]))]
    if not target_columns:
        return None, None
    # A table may present GAAP and Adjusted results in separate columns. Once the
    # target column itself is explicitly GAAP, an otherwise unqualified EPS row is
    # safe to use. Reconciliation tables with a shared, unqualified period header
    # still require GAAP on the EPS row.
    target_column_is_gaap = any(_explicit_gaap(contexts[column]["text"]) for column in target_columns)
    mixed_gaap_scope = scope_has_non_gaap and scope_has_gaap and not target_column_is_gaap
    target_groups = {}
    for column in target_columns:
        target_groups.setdefault(contexts[column]["key"], []).append(column)
    if len(target_groups) != 1:
        location = f"table {table_index}" if table_index is not None else "table"
        groups = [f"columns {columns}: {contexts[columns[0]]['text']}"
                  for columns in target_groups.values()]
        return None, f"{location}: target quarter is shown in multiple distinct column groups ({'; '.join(groups)})"
    target_columns = next(iter(target_groups.values()))

    eps_rows, basic_rows, net_rows, share_rows = [], [], [], []
    for row_index, info in enumerate(infos):
        label = (info["label"] or "").strip()
        low = label.lower()
        values = _number_in_columns(info, target_columns)
        if not label:
            continue
        kind, explicit = _eps_kind(label, infos, row_index)
        if kind:
            classification_context = " ".join((label, info.get("sectionContext", "")))
            row_context = " ".join((classification_context,
                                     " ".join(contexts[column]["text"] for column in target_columns)))
            record = {"row": row_index, "label": label, "lowLabel": low, "explicit": explicit,
                      "values": values, "info": info, "rowContext": row_context,
                      "classificationContext": classification_context}
            (eps_rows if kind == "diluted" else basic_rows).append(record)
        if is_net_income(low) and values:
            net_rows.append({"row": row_index, "label": label, "lowLabel": low, "values": values})
        if is_diluted_shares(low) and values:
            share_rows.append({"row": row_index, "label": label, "lowLabel": low, "values": values})

    # An explicit basic/diluted line outranks a section-heading subrow. The latter is
    # only retained when the release has no explicit per-share row.
    explicit_eps = [record for record in eps_rows if record["explicit"]]
    if explicit_eps:
        eps_rows = explicit_eps
    # The selected row's section/header context is stronger than the table caption:
    # press releases often place separate GAAP and Adjusted sections in one table.
    eps_rows = [record for record in eps_rows
                if not (_non_gaap(record["classificationContext"])
                        and not _explicit_gaap(record["classificationContext"]))
                and (not (non_gaap_only_scope or mixed_gaap_scope)
                     or target_column_is_gaap or _explicit_gaap(record["classificationContext"]))]
    if not eps_rows:
        return None, None
    if len(eps_rows) > 1:
        location = f"table {table_index}" if table_index is not None else "table"
        rows = "; ".join(f"row {record['row']} {record['label']!r}" for record in eps_rows)
        return None, f"{location}: multiple GAAP diluted EPS rows in target quarter columns {target_columns}: {rows}"
    eps_record = eps_rows[0]
    if len(eps_record["values"]) != 1:
        location = f"table {table_index}, row {eps_record['row']}" if table_index is not None else \
                   f"row {eps_record['row']}"
        reason = f"{location}: diluted EPS is empty in target quarter columns {target_columns}" \
            if not eps_record["values"] else \
            f"{location}: diluted EPS has multiple values in target quarter columns " \
            f"{[entry.col for _, entry in eps_record['values']]}"
        return None, reason
    eps, eps_entry = eps_record["values"][0]

    units = _table_units(parsed)
    net_matches = _matching_net_income(net_rows, target, units["moneyScale"])
    net_record = net_matches[0][0] if len(net_matches) == 1 else None
    scale = net_matches[0][1] if len(net_matches) == 1 else units["moneyScale"]
    if not scale and len(net_rows) == 1 and target is not None:
        possible = _matching_net_income(net_rows, target, None)
        if len(possible) == 1:
            net_record, scale = possible[0]
    if net_record is None and len(net_rows) == 1:
        net_record = net_rows[0]
    net_value = net_record["values"][0][0] if net_record else None
    scaled_net = net_value * scale if net_value is not None and scale else net_value
    target_match = len(net_matches) == 1

    header = contexts[eps_entry.col]["text"]
    currency = _eps_currency_evidence(parsed, eps_record["info"], expected_currency, header, eps_entry.col)
    review_flags = []
    if units["unitAmbiguous"]:
        review_flags.append("table-money-unit-is-ambiguous")
    if currency["status"] != "verified-usd":
        review_flags.append("eps-currency-is-not-verified-usd")
    eps_sign_matches = None
    if scaled_net is not None:
        eps_sign_matches = (eps == 0 or scaled_net == 0 or (eps > 0) == (scaled_net > 0))
        if not eps_sign_matches:
            review_flags.append("diluted-eps-sign-differs-from-net-income")
    if target is not None and net_rows and not target_match:
        review_flags.append("net-income-does-not-match-stored-quarter-value")
    if target is not None and len(net_matches) > 1:
        review_flags.append("net-income-target-match-is-ambiguous")
    eps_context = " ".join((parsed.caption, eps_record["rowContext"], header))
    if _share_class_or_ownership(eps_context):
        review_flags.append("eps-share-class-or-ownership-needs-review")
    if re.search(r"\b(?:continuing|discontinued)\s+operations\b", eps_context, re.I):
        review_flags.append("eps-operations-scope-is-not-entity-total")
    if re.search(r"\b(?:ads|adr|depositary|depository)\b", eps_context, re.I):
        review_flags.append("eps-unit-is-depositary-security-not-common-share")

    basic = None
    basic_review_flags = []
    entity_basic_rows = [row for row in basic_rows
                         if not _share_class_or_ownership(row["lowLabel"])
                         and not (_non_gaap(row["classificationContext"])
                                  and not _explicit_gaap(row["classificationContext"]))
                         and (not (non_gaap_only_scope or mixed_gaap_scope)
                              or target_column_is_gaap or _explicit_gaap(row["classificationContext"]))]
    basic_currency = None
    basic_record = basic_entry = None
    if len(entity_basic_rows) == 1 and len(entity_basic_rows[0]["values"]) == 1:
        basic_record = entity_basic_rows[0]
        basic_value, basic_entry = basic_record["values"][0]
        basic_header = contexts[basic_entry.col]["text"]
        basic_context = " ".join((parsed.caption, basic_record["rowContext"], basic_header))
        if _share_class_or_ownership(basic_context):
            basic_review_flags.append("basic-eps-share-class-or-ownership-needs-review")
        elif re.search(r"\b(?:continuing|discontinued)\s+operations\b", basic_context, re.I):
            basic_review_flags.append("basic-eps-operations-scope-is-not-entity-total")
        else:
            basic_currency = _eps_currency_evidence(
                parsed, basic_record["info"], expected_currency, basic_header, basic_entry.col)
            if basic_currency["status"] == "verified-usd":
                basic = basic_value
            else:
                basic_review_flags.append("basic-eps-currency-is-not-verified-usd")
    elif len(entity_basic_rows) > 1:
        basic_review_flags.append("basic-eps-row-is-ambiguous")
    if basic_rows and not entity_basic_rows:
        basic_review_flags.append("basic-eps-share-class-or-ownership-needs-review")

    diluted_shares = None
    share_value = None
    share_entry = None
    if len(share_rows) == 1 and len(share_rows[0]["values"]) == 1:
        share_value, share_entry = share_rows[0]["values"][0]
        share_scale = units["shareScale"] or scale
        if share_value > 0 and share_scale:
            diluted_shares = share_value * share_scale
        elif share_value <= 0:
            review_flags.append("diluted-share-count-is-not-positive")
        else:
            review_flags.append("diluted-share-count-unit-is-unknown")
    elif len(share_rows) > 1:
        review_flags.append("diluted-share-count-row-is-ambiguous")

    formula = None
    if scaled_net is not None and diluted_shares is not None and diluted_shares > 0:
        formula = scaled_net / diluted_shares
    elif net_value is not None and share_value is not None and share_value > 0 and \
            units["moneyScale"] == units["shareScale"]:
        formula = net_value / share_value
    if formula is not None and abs(formula - eps) > max(SHARES_TOLERANCE * abs(eps), EPS_FLOOR):
        review_flags.append("net-income-divided-by-shares-differs-from-diluted-eps")

    evidence = {
        "tableIndex": table_index,
        "tableCaption": parsed.caption,
        "reportPeriod": {"end": end, "kind": "Q4"},
        "classification": {"gaap": True, "diluted": True, "operations": "total",
                           "period": "Q4", "explicitEpsLabel": eps_record["explicit"],
                           "dilutedEvidence": "row label" if eps_record["explicit"]
                           else "diluted subrow under a per-share heading"},
        "epsRow": {"index": eps_record["row"], "label": eps_record["label"]},
        "epsCell": {"row": eps_entry.row, "column": eps_entry.col, "text": eps_entry.cell.text,
                    "footnoteMarkers": eps_entry.cell.footnote_markers},
        "targetColumns": target_columns,
        "columnHeader": header,
        "tableUnits": units["unitRows"],
        "currency": currency,
        "basicCurrency": basic_currency,
        "basicReviewFlags": basic_review_flags,
        "footnotes": _footnote_evidence(parsed),
    }
    if basic_record is not None and basic_entry is not None and basic is not None:
        evidence["basicEpsRow"] = {"index": basic_record["row"], "label": basic_record["label"]}
        evidence["basicEpsCell"] = {"row": basic_entry.row, "column": basic_entry.col,
                                    "text": basic_entry.cell.text,
                                    "footnoteMarkers": basic_entry.cell.footnote_markers}
    if net_record:
        evidence["netIncomeRow"] = {"index": net_record["row"], "label": net_record["label"],
                                    "cell": net_record["values"][0][1].cell.text,
                                    "row": net_record["values"][0][1].row,
                                    "column": net_record["values"][0][1].col}
    if share_entry:
        evidence["dilutedSharesRow"] = {"index": share_rows[0]["row"], "label": share_rows[0]["label"],
                                        "cell": share_entry.cell.text, "row": share_entry.row,
                                        "column": share_entry.col}
    per_share_rows = [[record["lowLabel"], [value for value, _ in record["values"]]]
                      for record in eps_rows + basic_rows]
    diagnostics = {
        "storedNetIncome": target,
        "tableNetIncome": scaled_net,
        "netIncomeMatchesStored": target_match,
        "netIncomeScale": scale,
        "epsSignMatchesNetIncome": eps_sign_matches,
        "netIncomePerDilutedShares": formula,
        "shareFormulaWithinTolerance": None if formula is None else
            abs(formula - eps) <= max(SHARES_TOLERANCE * abs(eps), EPS_FLOOR),
    }
    # Arithmetic checks never erase the raw candidate. They route it for source/context
    # corroboration when they disagree with the stored numerator or share history.
    manual_review_flags = {"diluted-eps-sign-differs-from-net-income",
                           "net-income-does-not-match-stored-quarter-value",
                           "net-income-target-match-is-ambiguous",
                           "net-income-divided-by-shares-differs-from-diluted-eps",
                           "eps-operations-scope-is-not-entity-total",
                           "eps-unit-is-depositary-security-not-common-share",
                           "eps-share-class-or-ownership-needs-review",
                           "table-money-unit-is-ambiguous", "eps-currency-is-not-verified-usd"}
    manual_review = any(flag in manual_review_flags for flag in review_flags)
    row_cells = [{"column": entry.col, "text": entry.cell.text,
                  "footnoteMarkers": entry.cell.footnote_markers}
                 for entry in eps_record["info"]["entries"]
                 if any(column in range(entry.col, entry.col + entry.cell.colspan) for column in target_columns)]
    evidence["epsRowCells"] = row_cells
    evidence["reportedPerShareUnit"] = "per-share; EPS value kept as filed"
    evidence["tableUnitBasis"] = {"moneyScale": units["moneyScale"], "shareScale": units["shareScale"],
                                  "ambiguous": units["unitAmbiguous"]}
    quality_by_field = {
        "epsDiluted": {"candidateValidated": not manual_review, "manualReviewRequired": manual_review,
                        "reviewFlags": list(review_flags)},
        "epsBasic": {"candidateValidated": basic is not None and not basic_review_flags,
                     "manualReviewRequired": bool(basic_review_flags),
                     "reviewFlags": list(basic_review_flags)},
    }
    return {"epsDiluted": eps, "epsDilutedAsReported": eps, "epsBasic": basic,
            "epsBasicAsReported": basic, "dilutedShares": diluted_shares,
            "dilutedSharesAsReported": diluted_shares, "scale": scale, "net": scaled_net,
            "netLabel": net_record["lowLabel"] if net_record else None,
            "epsLabel": eps_record["lowLabel"], "perShareRows": per_share_rows[:8],
            "diagnostics": diagnostics, "reviewFlags": review_flags, "uniqueCandidate": True,
            "candidateValidated": not manual_review, "manualReviewRequired": manual_review,
            "qualityByField": quality_by_field, "evidence": evidence}, None


def table_basic_eps(table, end, table_index=None, expected_currency=None):
    """Extract a unique, entity-wide, GAAP Basic Q4 EPS when Diluted EPS is not shown."""
    parsed, grid = _table_grid(table)
    if not grid or not any(grid):
        return None, None
    infos, _data_start, contexts = _header_contexts(parsed, grid)
    infos = _row_section_contexts(infos)
    table_scope = " ".join([parsed.caption or ""] + [context["text"] for context in contexts])
    scope_has_non_gaap = _non_gaap(table_scope)
    scope_has_gaap = _explicit_gaap(table_scope)
    non_gaap_only_scope = scope_has_non_gaap and not scope_has_gaap
    target_columns = [column for column, context in enumerate(contexts)
                      if _is_target_quarter_header(context["text"], end)
                      and (not _non_gaap(parsed.caption) or _explicit_gaap(context["text"]))]
    if not target_columns:
        return None, None
    target_column_is_gaap = any(_explicit_gaap(contexts[column]["text"]) for column in target_columns)
    mixed_gaap_scope = scope_has_non_gaap and scope_has_gaap and not target_column_is_gaap
    target_groups = {}
    for column in target_columns:
        target_groups.setdefault(contexts[column]["key"], []).append(column)
    if len(target_groups) != 1:
        location = f"table {table_index}" if table_index is not None else "table"
        return None, f"{location}: Basic EPS quarter is shown in multiple distinct column groups"
    target_columns = next(iter(target_groups.values()))
    rows = []
    for row_index, info in enumerate(infos):
        kind, explicit = _eps_kind(info.get("label"), infos, row_index)
        if kind != "basic":
            continue
        values = _number_in_columns(info, target_columns)
        if values:
            label = info.get("label") or ""
            classification_context = " ".join((label, info.get("sectionContext", "")))
            row_context = " ".join((classification_context,
                                     " ".join(contexts[column]["text"] for column in target_columns)))
            if (_non_gaap(classification_context) and not _explicit_gaap(classification_context)) or (
                    (non_gaap_only_scope or mixed_gaap_scope) and not target_column_is_gaap
                    and not _explicit_gaap(classification_context)):
                continue
            rows.append({"row": row_index, "label": label, "explicit": explicit,
                         "values": values, "rowContext": row_context,
                         "classificationContext": classification_context})
    explicit_rows = [row for row in rows if row["explicit"]]
    if explicit_rows:
        rows = explicit_rows
    if not rows:
        return None, None
    if len(rows) != 1 or len(rows[0]["values"]) != 1:
        location = f"table {table_index}" if table_index is not None else "table"
        return None, f"{location}: Basic EPS row/cell is ambiguous in target quarter columns"
    row = rows[0]
    header = contexts[row["values"][0][1].col]["text"]
    class_context = " ".join((parsed.caption, row["rowContext"], header))
    if re.search(r"\b(?:continuing|discontinued)\s+operations\b", class_context, re.I):
        return None, f"table {table_index}: Basic EPS operations scope is not entity total"
    if _share_class_or_ownership(class_context):
        return None, f"table {table_index}: class-specific Basic EPS requires share-class review"
    value, entry = row["values"][0]
    header = contexts[entry.col]["text"]
    currency = _eps_currency_evidence(parsed, infos[row["row"]], expected_currency, header, entry.col)
    if currency["status"] != "verified-usd":
        return None, f"table {table_index}: Basic EPS currency is not verified as USD ({currency['status']})"
    evidence = {
        "tableIndex": table_index,
        "tableCaption": parsed.caption,
        "reportPeriod": {"end": end, "kind": "Q4"},
        "classification": {"gaap": True, "basic": True, "diluted": False,
                           "operations": "total", "period": "Q4", "explicitEpsLabel": row["explicit"]},
        "basicEpsRow": {"index": row["row"], "label": row["label"]},
        "basicEpsCell": {"row": entry.row, "column": entry.col, "text": entry.cell.text,
                         "footnoteMarkers": entry.cell.footnote_markers},
        "basicEpsRowContext": row.get("rowContext"),
        "targetColumns": target_columns,
        "columnHeader": header,
        "currency": currency,
        "tableUnits": _table_units(parsed)["unitRows"],
        "footnotes": _footnote_evidence(parsed),
        "reportedPerShareUnit": "per-share; EPS value kept as filed",
    }
    return {"epsBasic": value, "epsBasicAsReported": value, "epsDiluted": None,
            "uniqueCandidate": True, "candidateValidated": True, "manualReviewRequired": False,
            "reviewFlags": [], "diagnostics": {},
            "qualityByField": {"epsBasic": {"candidateValidated": True,
                                               "manualReviewRequired": False,
                                               "reviewFlags": []}},
            "evidence": evidence}, None


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
    found, shown, ambiguous = [], [], []
    orphans = {"net_tables_without_period": 0, "period_in_previous_table": 0, "sample": []}
    reason = "no table shows this quarter's net income"
    for position, name in enumerate(exhibits + extra):
        if position >= len(exhibits) and found:
            break
        url = base + name
        text = sec.get_text(url)
        if text is None:
            continue
        previous = ""
        for table_index, table in enumerate(structured_tables_of(text)):
            plain_rows = [[cell.text for cell in row] for row in table.rows]
            table_text = " ".join(" ".join(row) for row in plain_rows)
            result, why = table_eps(table, target, end, table_index=table_index)
            if result is not None:
                source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                result["evidence"]["sourceTextSha256"] = source_hash
                result["evidence"]["sourceDocument"] = name
                result["evidence"]["sourceUrl"] = url
                result["evidence"]["accession"] = accession
                result["evidence"]["filingDate"] = filing["filingDate"]
                found.append(dict(result, source="SEC EDGAR 8-K exhibit", accession=accession, url=url,
                                  filingDate=filing["filingDate"], sourceTextSha256=source_hash,
                                  candidateValidated=result["candidateValidated"]))
            elif why:
                reason = why
                ambiguous.append(why)
            elif debug is not None and shows_period(table_text, end):
                shown += [[label.lower(), values] for label, values in (row_of(cells) for cells in plain_rows)
                          if label and is_net_income(label.lower())]
            elif debug is not None and any(label and is_net_income(label.lower())
                                           for label in (row_of(cells)[0] for cells in plain_rows)):
                # a net income table without its period name: is the name in the table just before it?
                orphans["net_tables_without_period"] += 1
                if shows_period(previous, end):
                    orphans["period_in_previous_table"] += 1
                if len(orphans["sample"]) < 2:
                    orphans["sample"].append([" | ".join(cell for cell in row if cell)[:120]
                                               for row in plain_rows[:3]])
            previous = table_text
    if ambiguous:
        return None, ambiguous[0]
    if not found:
        if debug is not None:
            debug.append({"target": target, "period_net_rows": shown[:4], "orphans": orphans})
            text = sec.get_text(base + (exhibits + extra)[0]) or ""
            debug.append({"net_like_text": re.findall(r">\s*([^<>]{0,80}net[^<>]{0,60})<", text, re.I)[:6]})
        return None, reason
    if len({item["epsDiluted"] for item in found}) > 1:
        return None, "release tables disagree on diluted EPS"
    selected = dict(found[0])
    selected["corroboratingTables"] = [item.get("evidence") for item in found]
    selected["reviewFlags"] = sorted({flag for item in found for flag in item.get("reviewFlags", [])})
    selected["manualReviewRequired"] = any(item.get("manualReviewRequired") for item in found)
    selected["candidateValidated"] = not selected["manualReviewRequired"]
    return selected, None


def _needs_manual_review(reason):
    low = (reason or "").lower()
    return any(marker in low for marker in ("ambiguous", "multiple", "eps is empty", "eps has multiple values",
                                            "tables disagree on diluted eps"))


def find_quarter(sec, cik, filings, end, target, debug=None):
    """The first candidate filing that gives this quarter → (result, None), else (None, reason)."""
    reason = "no 8-K with Item 2.02 in the window"
    for filing in candidate_filings(filings, date.fromisoformat(end))[:MAX_FILINGS]:
        result, why = from_filing(sec, cik, filing, target, end, debug)
        if result is not None:
            return dict(result, end=end, filingDate=filing["filingDate"]), None
        reason = why or reason
    return None, reason


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
        factor = split_factor(doc, filing["filingDate"], end)
        if factor is None:
            return {"status": "pending-split-basis-review",
                    "reason": "split-event-history-unverified-or-incomplete", "url": result["url"]}
        checked = round(result["epsDiluted"] / factor, 6)
        status = "confirmed" if within_tolerance(checked, eps) else "conflict"
        return {"status": status, "epsDiluted": checked, "url": result["url"],
                "diagnostics": result.get("diagnostics"), "reviewFlags": result.get("reviewFlags", []),
                "evidence": result.get("evidence")}
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
            factor = split_factor(doc, result["filingDate"], p["end"])
            if factor is None:
                result = dict(result, splitFactor=None, splitNormalizationStatus="pending",
                              reviewFlags=sorted(set(result.get("reviewFlags", []))
                                                 | {"split-event-history-unverified-or-incomplete"}),
                              manualReviewRequired=True, candidateValidated=False)
            elif factor != 1:
                result = dict(result, splitFactor=factor,
                              epsDiluted=round(result["epsDiluted"] / factor, 6),
                              epsBasic=None if result["epsBasic"] is None else round(result["epsBasic"] / factor, 6),
                              dilutedShares=None if result["dilutedShares"] is None
                              else round(result["dilutedShares"] * factor, 1))
            else:
                result = dict(result, splitFactor=1.0)
        if mode in ("backtest", "fill") and result is not None:
            result = dict(result, verified=verify_quarter(sec, cik, filings, p["end"], result["net"],
                                                          result["epsDiluted"], doc))
        rows.append({"key": key, "end": p["end"], "stored": p["eps"], "target": p["target"], "derived": p["derived"],
                     "result": result, "reason": reason, "debug": debug[:2]})
    return True


def _source_audit_valid(result, end, field):
    audit = result.get("sourceAudit") if isinstance(result, dict) else None
    if not isinstance(audit, dict) or audit.get("coverageComplete") is not True \
            or audit.get("allVersionsAgree") is not True or audit.get("periodEnd") != end:
        return False
    evidence = (audit.get("fields") or {}).get(field) if isinstance(audit.get("fields"), dict) else None
    if not isinstance(evidence, dict):
        return False
    raw_path = evidence.get("rawBodyPath")
    raw_url = evidence.get("rawBodyUrl")
    if (not isinstance(raw_path, str) or not raw_path.strip()
            or raw_url != evidence.get("url") or evidence.get("locatorVerified") is not True):
        return False
    try:
        raw_bytes = Path(raw_path).read_bytes()
    except OSError:
        return False
    if hashlib.sha256(raw_bytes).hexdigest() != str(evidence.get("sha256") or "").lower():
        return False
    accession = str(evidence.get("accession") or "")
    cik = str(evidence.get("cik") or "")
    name = str(evidence.get("document") or "")
    parsed = urlparse(str(evidence.get("url") or ""))
    expected_path = f"/Archives/edgar/data/{int(cik)}/{re.sub(r'[^0-9]', '', accession)}/{name}" \
        if cik.isdigit() and accession and name and Path(name).name == name else None
    return bool(
        evidence.get("field") == field and evidence.get("periodEnd") == end
        and evidence.get("accession") == result.get("accession")
        and evidence.get("url") == result.get("url")
        and evidence.get("reportedValue") is not None
        and evidence.get("reportedValue") == result.get(field + "AsReported", result.get(field))
        and evidence.get("gaap") is True and evidence.get("periodKind") == "Q4"
        and evidence.get("shareClass") == "entity-total"
        and evidence.get("unit") == "USD/shares"
        and accession and evidence.get("form") and evidence.get("filingDate")
        and evidence.get("periodStart") and bool(evidence.get("excerpt"))
        and isinstance(evidence.get("locator"), dict)
        and re.fullmatch(r"[0-9a-fA-F]{64}", str(evidence.get("sha256") or ""))
        and expected_path and parsed.scheme == "https" and parsed.netloc.lower() == "www.sec.gov"
        and parsed.path == expected_path and not parsed.params and not parsed.query and not parsed.fragment
    )


def apply_fill(doc, found):
    """
    Writes uniquely validated or audit-approved values into null fields only. Grade A is based
    on the original exhibit's uniquely identified GAAP diluted Q4 row, not next-year verification.
    A candidate requiring manual review remains null until audit approval is recorded.
    """
    quarterly = doc["quarterly"]
    ends = quarterly["end"]
    values = quarterly.setdefault("values", {})
    written_count = 0
    for i, end in enumerate(ends):
        result = found.get(end)
        if not result or not result.get("uniqueCandidate") or \
                (not result.get("candidateValidated") and not result.get("auditApproved")):
            continue
        # Grade comes from the unique original exhibit row. A later comparative
        # column is diagnostic; it cannot promote or demote this source.
        grade = "A"
        written = []
        for field in ("epsDiluted", "epsBasic"):
            value = result.get(field)
            if value is None or not _source_audit_valid(result, end, field):
                continue
            column = values.setdefault(field, [None] * len(ends))
            if len(column) < len(ends):
                column.extend([None] * (len(ends) - len(column)))
            if column[i] is None:
                column[i] = value
                written.append(field)
                written_count += 1
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
        if not eps_fields:
            continue
        sources = doc.setdefault("q4EpsSources", {})
        period_sources = sources.get(end)
        if not isinstance(period_sources, dict):
            period_sources = {}
        elif period_sources.get("accession") or period_sources.get("url"):
            # Preserve the legacy shared record. New writes are field keyed so a later
            # Basic or Diluted fill cannot silently replace the other field's evidence.
            period_sources = {"legacy": period_sources}
        source_common = {"method": "audited SEC original filing", "accession": result["accession"],
                         "url": result["url"], "filingDate": result["filingDate"], "grade": grade,
                         "dilutedSharesAsReported": result.get("dilutedSharesAsReported",
                                                                result.get("dilutedShares")),
                         "splitFactor": result.get("splitFactor", 1.0),
                         "basis": "documented-split-adjusted-current" if result.get("splitFactor", 1.0) != 1
                         else "as-filed",
                         "source": result.get("source"), "sourceTextSha256": result.get("sourceTextSha256"),
                         "evidence": result.get("evidence"), "reviewFlags": result.get("reviewFlags", []),
                         "manualReviewRequired": result.get("manualReviewRequired", False),
                         "auditApproved": result.get("auditApproved", False),
                         "sourceAudit": result.get("sourceAudit")}
        for field in eps_fields:
            field_record = dict(source_common)
            field_record.update({"field": field,
                                 "reportedValue": result.get(field + "AsReported", result.get(field))})
            existing_record = period_sources.get(field)
            if existing_record is not None and existing_record != field_record:
                # A null EPS cell can still have an older or disputed source note. Keep
                # that record intact, retaining every source as an append-only history.
                previous_key = field + "PreviousSource"
                suffix = 2
                while previous_key in period_sources:
                    previous_key = f"{field}PreviousSource{suffix}"
                    suffix += 1
                period_sources[previous_key] = existing_record
            period_sources[field] = field_record
        sources[end] = period_sources
    return written_count


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
    out["candidateStatus"] = {
        "unique": sum(bool(r["result"].get("uniqueCandidate")) for r in accepted),
        "validated": sum(bool(r["result"].get("candidateValidated")) for r in accepted),
        "manualReviewRequired": sum(bool(r["result"].get("manualReviewRequired")) for r in accepted),
    }
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
            found = {r["end"]: r["result"] for r in ticker_rows if r["result"] and
                     r["result"].get("uniqueCandidate") and
                     (r["result"].get("candidateValidated") or r["result"].get("auditApproved"))}
            if found:
                ticker_filled = apply_fill(doc, found)
                if ticker_filled:
                    seed.write_atomic(path, doc)
                filled += ticker_filled
            pending = any((r["result"] and r["result"].get("manualReviewRequired") and
                           not r["result"].get("auditApproved")) or _needs_manual_review(r["reason"])
                          for r in ticker_rows)
            audited = all(r["result"] and r["result"].get("sourceAudit") and
                           all(r["result"].get(field) is None or _source_audit_valid(r["result"], r["end"], field)
                               for field in ("epsDiluted", "epsBasic"))
                           for r in ticker_rows)
            if complete and not pending and audited:
                state["done"][key] = {"checked": today, "filled": len(found)}
        if stop:
            break
    if args.mode == "fill" and state.get("done"):
        seed.write_atomic(STATE_PATH, state)
    report = summarize(rows, args.mode)
    mismatches = [{"key": r["key"], "end": r["end"], "stored": r["stored"], "estimate": r["result"]["epsDiluted"],
                   "basic": r["result"]["epsBasic"], "derived": r["derived"],
                   "verified": r["result"].get("verified"), "netLabel": r["result"].get("netLabel"),
                   "epsLabel": r["result"].get("epsLabel"), "perShareRows": r["result"].get("perShareRows"),
                   "reviewFlags": r["result"].get("reviewFlags"),
                   "manualReviewRequired": r["result"].get("manualReviewRequired"),
                   "candidateValidated": r["result"].get("candidateValidated"),
                   "evidence": r["result"].get("evidence"), "sourceTextSha256": r["result"].get("sourceTextSha256"),
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
    parser.add_argument("--mode", choices=("backtest", "fill"), default="backtest")
    parser.add_argument("--tickers", default="", help="comma separated; empty = every US file")
    parser.add_argument("--limit", type=int, default=0, help="only the first N files (0 = all)")
    parser.add_argument("--budget", type=int, default=6000, help="SEC requests this run")
    parser.add_argument("--report", default="", help="also write the report JSON to this path")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.mode == "fill":
        print("STOPPED: legacy --mode fill lacks a cache-bound raw-document audit and cannot promote EPS; "
              "use q4_eps_sec_scan.py and audit_q4_eps_sec_scan.py.", flush=True)
        return 2
    configured = (os.environ.get("SEC_USER_AGENT") or "").strip()
    user_agent = seed.resolve_user_agent({"SEC_USER_AGENT": configured} if configured else {})
    sec = Sec(user_agent, budget=args.budget)
    try:
        report = run(args, sec)
    except Forbidden as exc:
        print(f"STOPPED: SEC returned HTTP 403 for {exc}; files written so far are kept, re-run later.", flush=True)
        return 2
    except NetworkError as exc:
        print(f"STOPPED: SEC could not be reached ({exc}); re-run later.", flush=True)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as file:
            file.write(text)
    summary = {k: v for k, v in report.items() if k != "rows"}
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
