"""Small HTMLParser for source-visible Inline XBRL contexts, units, dimensions and facts."""
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re


def _local(tag):
    return tag.lower().split(":")[-1]


class InlineXbrlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts = {}
        self.units = {}
        self.facts = []
        self.namespaces = {}
        self.context = None
        self.unit = None
        self.unit_part = None
        self.capture = None
        self.capture_text = []
        self.fact = None
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs}
        for key, value in attrs.items():
            if key == "xmlns":
                self.namespaces[""] = value
            elif key.startswith("xmlns:"):
                self.namespaces[key.split(":", 1)[1]] = value
        self.stack.append(tag)
        local = _local(tag)
        if local == "context":
            self.context = {"id": attrs.get("id"), "start": None, "end": None,
                            "entityIdentifiers": [], "dimensions": [],
                            "typedDimensions": [], "hasSegment": False, "hasScenario": False}
        elif local == "unit":
            self.unit = {"id": attrs.get("id"), "numerator": [], "denominator": []}
        elif self.unit is not None and local in {"unitnumerator", "unitdenominator"}:
            self.unit_part = "numerator" if local == "unitnumerator" else "denominator"
        elif self.context is not None and local == "identifier":
            self.capture = ("entity-identifier", attrs.get("scheme"))
            self.capture_text = []
        elif self.context is not None and local in {"startdate", "enddate", "instant"}:
            self.capture = ("context-date", local)
            self.capture_text = []
        elif self.context is not None and local == "explicitmember":
            self.capture = ("dimension", attrs.get("dimension"))
            self.capture_text = []
        elif self.context is not None and local == "typedmember":
            self.capture = ("typed-dimension", attrs.get("dimension"))
            self.capture_text = []
        elif self.context is not None and local == "segment":
            self.context["hasSegment"] = True
        elif self.context is not None and local == "scenario":
            self.context["hasScenario"] = True
        elif self.unit is not None and local == "measure":
            self.capture = ("unit-measure", self.unit_part or "numerator")
            self.capture_text = []
        elif local == "nonfraction" or tag.lower() == "nonfraction":
            self.fact = {
                "name": (attrs.get("name") or "").split(":")[-1],
                "namespace": (attrs.get("name") or "").split(":")[0] if ":" in (attrs.get("name") or "") else None,
                "namespaceUri": self.namespaces.get((attrs.get("name") or "").split(":")[0])
                if ":" in (attrs.get("name") or "") else self.namespaces.get(""),
                "id": attrs.get("id"),
                "contextRef": attrs.get("contextref"),
                "unitRef": attrs.get("unitref"),
                "format": attrs.get("format"),
                "decimals": attrs.get("decimals"),
                "scale": attrs.get("scale", "0"),
                "sign": attrs.get("sign"),
                "text": [],
            }

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.capture is not None:
            self.capture_text.append(data)
        if self.fact is not None:
            self.fact["text"].append(data)

    def handle_endtag(self, tag):
        local = _local(tag)
        if self.capture is not None and local in {"identifier", "startdate", "enddate", "instant", "explicitmember",
                                                  "typedmember", "measure"}:
            kind, key = self.capture
            text = "".join(self.capture_text).strip()
            if kind == "entity-identifier" and self.context is not None:
                self.context["entityIdentifiers"].append({"scheme": key, "identifier": text})
            elif kind == "context-date" and self.context is not None:
                self.context["start" if key == "startdate" else "end"] = text
                if key == "instant":
                    self.context["end"] = text
            elif kind == "dimension" and self.context is not None:
                self.context["dimensions"].append({"dimension": key, "member": text})
            elif kind == "typed-dimension" and self.context is not None:
                self.context["typedDimensions"].append({"dimension": key, "value": text})
            elif kind == "unit-measure" and self.unit is not None:
                prefix, separator, local = text.partition(":")
                namespace = self.namespaces.get(prefix) if separator else self.namespaces.get("")
                self.unit[key].append({"prefix": prefix if separator else "", "local": local if separator else text,
                                       "namespaceUri": namespace})
            self.capture = None
            self.capture_text = []
        if self.fact is not None and local == "nonfraction":
            raw = "".join(self.fact.pop("text")).strip()
            self.fact["rawText"] = raw
            try:
                parenthetical_negative = raw.startswith("(") and raw.endswith(")")
                number_text = raw.strip("()$ \u00a0").replace("−", "-").replace("–", "-")
                transform = str(self.fact.get("format") or "").split(":")[-1].lower()
                if transform in ("", "numdotdecimal"):
                    # Permit only conventional comma grouping with a dot decimal. A comma
                    # decimal without an explicit transform is ambiguous and must stay pending.
                    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?", number_text):
                        raise ValueError("unsupported unformatted numeric separators")
                    number_text = number_text.replace(",", "")
                elif transform == "numcommadecimal":
                    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)?(?:,\d+)?", number_text):
                        raise ValueError("invalid numcommadecimal value")
                    number_text = number_text.replace(".", "").replace(",", ".")
                else:
                    raise ValueError(f"unsupported Inline XBRL numeric transform: {transform}")
                value = Decimal(number_text)
                scale = int(self.fact.pop("scale", "0") or 0)
                value *= Decimal(10) ** scale
                sign = self.fact.pop("sign", None)
                if (sign == "-" or parenthetical_negative) and value > 0:
                    value = -value
                self.fact["value"] = value
            except (InvalidOperation, TypeError, ValueError, OverflowError):
                self.fact["value"] = None
                self.fact["valueError"] = "numeric transform or lexical form is unsupported"
            self.facts.append(self.fact)
            self.fact = None
        if self.context is not None and local == "context":
            self.contexts[self.context.get("id")] = self.context
            self.context = None
        if self.unit is not None and local == "unit":
            self.units[self.unit.get("id")] = self.unit
            self.unit = None
            self.unit_part = None
        if self.stack:
            self.stack.pop()

    def result(self):
        for fact in self.facts:
            unit = self.units.get(fact.get("unitRef")) or {}
            numerator = unit.get("numerator") or []
            denominator = unit.get("denominator") or []
            if (len(numerator) == 1 and len(denominator) == 1
                    and numerator[0].get("namespaceUri") in {
                        "http://www.xbrl.org/2003/iso4217", "https://www.xbrl.org/2003/iso4217"}
                    and numerator[0].get("local") == "USD"
                    and denominator[0].get("namespaceUri") in {
                        "http://www.xbrl.org/2003/instance", "https://www.xbrl.org/2003/instance"}
                    and denominator[0].get("local") == "shares"):
                fact["unit"] = "USD/shares"
            elif (len(numerator) == 1 and not denominator
                  and numerator[0].get("namespaceUri") in {
                      "http://www.xbrl.org/2003/iso4217", "https://www.xbrl.org/2003/iso4217"}
                  and numerator[0].get("local") == "USD"):
                fact["unit"] = "USD"
            elif (len(numerator) == 1 and not denominator
                  and numerator[0].get("namespaceUri") in {
                      "http://www.xbrl.org/2003/instance", "https://www.xbrl.org/2003/instance"}
                  and numerator[0].get("local") == "shares"):
                fact["unit"] = "shares"
            elif numerator and denominator:
                def expanded(measure):
                    uri = measure.get("namespaceUri")
                    return f"{{{uri}}}{measure.get('local')}" if uri else f"{measure.get('prefix')}:{measure.get('local')}"
                fact["unit"] = "/".join(["/".join(expanded(item) for item in numerator),
                                          "/".join(expanded(item) for item in denominator)])
            elif numerator:
                fact["unit"] = "/".join(
                    f"{{{item.get('namespaceUri')}}}{item.get('local')}" if item.get("namespaceUri")
                    else f"{item.get('prefix')}:{item.get('local')}" for item in numerator)
            else:
                fact["unit"] = None
            fact["context"] = self.contexts.get(fact.get("contextRef"))
        return {"contexts": self.contexts, "units": self.units, "facts": self.facts}


def parse_inline_xbrl(html):
    parser = InlineXbrlParser()
    parser.feed(html if isinstance(html, str) else html.decode("utf-8", errors="replace"))
    parser.close()
    return parser.result()


class FilingTextParser(HTMLParser):
    """Extract visible text and shallow table rows from non-iXBRL press releases."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.tables = []
        self.table_depth = 0
        self.current_table = None
        self.current_row = None
        self.current_cell = None

    def handle_starttag(self, tag, attrs):
        local = _local(tag)
        if local == "table":
            self.table_depth += 1
            if self.table_depth == 1:
                self.current_table = []
        elif self.table_depth == 1 and local == "tr":
            self.current_row = []
        elif self.table_depth == 1 and local in {"td", "th"}:
            self.current_cell = []

    def handle_data(self, data):
        data = " ".join(data.split())
        if not data:
            return
        self.text_parts.append(data)
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        local = _local(tag)
        if self.table_depth == 1 and local in {"td", "th"} and self.current_cell is not None:
            self.current_row.append(" ".join(self.current_cell).strip())
            self.current_cell = None
        elif self.table_depth == 1 and local == "tr" and self.current_row is not None:
            if any(self.current_row):
                self.current_table.append(self.current_row)
            self.current_row = None
        elif local == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0 and self.current_table is not None:
                self.tables.append(self.current_table)
                self.current_table = None

    def result(self):
        return {"text": " ".join(self.text_parts), "tables": self.tables}


def extract_filing_text(html):
    parser = FilingTextParser()
    parser.feed(html if isinstance(html, str) else html.decode("utf-8", errors="replace"))
    parser.close()
    return parser.result()
