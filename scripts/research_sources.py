"""Verified official TWSE/TPEx formats. Unknown shapes fail closed; no missing→zero."""

import csv
import io
import json
import re
from build_tw_chips import decode_payload, parse_date, to_number
from research_io import SourceError


def clean(value):
    return re.sub(r"\s+", "", str(value)).replace("外陸資", "外資及陸資")


def tables(body):
    if isinstance(body, (dict, list)):
        data = body
    else:
        text = decode_payload(body)
        if text.lstrip().startswith(("{", "[")):
            data = json.loads(text)
        else:
            rows = list(csv.reader(io.StringIO(text)))
            for i, row in enumerate(rows):
                if any(clean(c) in ("證券代號", "代號") for c in row):
                    title = " ".join(cell for prefix in rows[:i] for cell in prefix)
                    return [(row, rows[i + 1 :], parse_date(title))]
            raise SourceError("CSV missing securities header")
    if isinstance(data, list):
        if not data:
            return []
        if not all(isinstance(r, dict) for r in data):
            raise SourceError("expected record array")
        headers = list(data[0])
        return [(headers, [[r.get(k) for k in headers] for r in data], None)]
    if not isinstance(data, dict):
        raise SourceError("not a JSON object")
    if re.search("沒有符合|無資料|查無", str(data.get("stat", ""))):
        return []
    blocks = data.get("tables", [data])
    out = [
        (b.get("fields"), b.get("data"), parse_date(b.get("date") or data.get("date")))
        for b in blocks
        if b.get("fields") is not None and isinstance(b.get("data"), list)
    ]
    if not out:
        raise SourceError("unknown JSON table schema")
    return out


def parse_flows(body, market, expected_date):
    out = {}
    for headers, rows, actual in tables(body):
        if actual and actual != expected_date:
            raise SourceError(f"wrong report date {actual}, expected {expected_date}")
        names = [clean(c) for c in headers]
        repeated = market == "TWO" and names == ["代號", "名稱"] + [
            "買進股數",
            "賣出股數",
            "買賣超股數",
        ] * 7 + ["三大法人買賣超股數合計"]

        def locate(pattern):
            hits = [i for i, h in enumerate(names) if re.fullmatch(pattern, h)]
            if len(hits) != 1:
                raise SourceError(f"field missing/ambiguous: {pattern}; {headers}")
            return hits[0]

        code_i = locate("證券代號|代號|SecuritiesCompanyCode")
        for row in rows:
            if len(row) <= code_i:
                continue
            code = str(row[code_i]).strip().strip('="')
            if not re.fullmatch("[1-9][0-9]{3}", code):
                continue
            if actual is None:
                dated = next(
                    (
                        parse_date(row[i])
                        for i, name in enumerate(names)
                        if name in ("Date", "日期", "資料日期")
                    ),
                    None,
                )
                if dated != expected_date:
                    raise SourceError(
                        "Missing or mismatched official report date; refusing URL-only date inference"
                    )
            if len(row) != len(names):
                raise SourceError("row width changed")

            # TPEx duplicated JSON headings use its verified seven-group contract;
            # CSV's expanded headings are preferred for all online historical requests.
            def triple(pattern, offset=None):
                indices = (
                    list(range(offset, offset + 3))
                    if repeated and offset is not None
                    else [
                        locate(pattern + suffix)
                        for suffix in ("買進股數", "賣出股數", "買賣超股數")
                    ]
                )
                values = [to_number(row[i]) for i in indices]
                if any(v is None or int(v) != v for v in values):
                    raise SourceError(f"invalid shares: {code}")
                b, s, n = map(int, values)
                if b < 0 or s < 0 or b - s != n:
                    raise SourceError(f"buy-sell != net: {code}")
                return dict(buy=b, sell=s, net=n)

            if market == "TW":
                # TWSE embeds category qualifiers after the measurement rather than before it.
                def twgroup(prefix, qualifier=""):
                    indices = [
                        locate("(?:" + prefix + ")" + word + re.escape(qualifier))
                        for word in ("買進股數", "賣出股數", "買賣超股數")
                    ]
                    vals = [to_number(row[i]) for i in indices]
                    if (
                        any(v is None or int(v) != v for v in vals)
                        or vals[0] < 0
                        or vals[1] < 0
                        or vals[0] - vals[1] != vals[2]
                    ):
                        raise SourceError(f"invalid shares: {code}")
                    return dict(zip(("buy", "sell", "net"), map(int, vals)))

                if any("不含外資自營商" in h for h in names):
                    foreign = twgroup("外資及陸資", "(不含外資自營商)")
                    fd = twgroup("外資自營商")
                else:
                    raise SourceError(
                        "Historical T86 predates foreign-dealer separation; research-v1 does not infer missing subcategories"
                    )
                trust = twgroup("投信")
                own = (
                    twgroup("自營商", "(自行買賣)")
                    if any("自行買賣" in h for h in names)
                    else twgroup("自營商")
                )
                hedge = (
                    twgroup("自營商", "(避險)")
                    if any("避險" in h for h in names)
                    else dict(buy=0, sell=0, net=0)
                )
                dealer = {k: own[k] + hedge[k] for k in own}
                total_i = locate("三大法人買賣超股數")
            else:
                foreign = triple(r"外資及陸資\(不含外資自營商\)-", 2)
                fd = triple(r"外資自營商-", 5)
                combined = triple("外資及陸資-", 8)
                trust = triple("投信-", 11)
                own, hedge = triple(r"自營商\(自行買賣\)-", 14), triple(
                    r"自營商\(避險\)-", 17
                )
                dealer = triple("自營商-", 20)
                if any(
                    combined[k] != foreign[k] + fd[k] or dealer[k] != own[k] + hedge[k]
                    for k in dealer
                ):
                    raise SourceError(f"TPEx group totals mismatch: {code}")
                total_i = locate("三大法人買賣超股數合計")
            combined = {k: foreign[k] + fd[k] for k in foreign}
            if (
                to_number(row[total_i])
                != combined["net"] + trust["net"] + dealer["net"]
            ):
                raise SourceError(f"official total mismatch: {code}")
            out[code + "." + market] = dict(
                date=expected_date,
                foreign=combined,
                foreignExDealer=foreign,
                foreignDealer=fd,
                trust=trust,
                dealer=dealer,
                dealerOwn=own,
                dealerHedge=hedge,
            )
    return out


def parse_alerts(body, market, kind, url, fetched):
    out = []
    for headers, rows, _ in tables(body):
        for values in rows:
            row = dict(zip(headers, values))

            def get(*names):
                return next(
                    (str(row[n]).strip() for n in names if row.get(n) is not None), ""
                )

            code = get("Code", "SecuritiesCompanyCode", "證券代號")
            if not re.fullmatch("[1-9][0-9]{3}", code):
                continue
            day = parse_date(get("Date", "公告日期", "公布日期", "日期"))
            if not day:
                raise SourceError("announcement date missing")
            period = get("DispositionPeriod", "處置起訖時間")
            dates = re.findall(r"\d{3,4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{7,8}", period)
            start, end = (list(map(parse_date, dates)) + [None, None])[:2]
            reason = get(
                "TradingInfoForAttention",
                "TradingInformation",
                "注意交易資訊",
                "ReasonsOfDisposition",
                "DispositionReasons",
                "處置原因",
                "處置條件",
            )
            content = get("Detail", "DisposalCondition", "處置內容")
            out.append(
                dict(
                    id=f"{market}:{kind}:{code}:{day}",
                    symbol=code + "." + market,
                    market=market,
                    kind=kind,
                    date=day,
                    name=get("Name", "CompanyName", "證券名稱").split("(")[0],
                    reason=re.sub("<[^>]+>", " ", reason),
                    start=start,
                    end=end,
                    period=period or None,
                    measures=get("DispositionMeasures", "處置措施"),
                    content=re.sub("<[^>]+>", " ", content),
                    sourceUrl=url,
                    fetchedAt=fetched,
                    publishedAt=None,
                )
            )
    return out


def parse_market_turnover(body, market, expected_date):
    """Official shares / TWD amount; no inference from bid/ask lot columns."""
    out = {}
    for headers, rows, actual in tables(body):
        fields = [clean(h) for h in headers]
        code_name = "證券代號" if market == "TW" else "代號"
        amount_name = "成交金額" if market == "TW" else "成交金額(元)"
        close_name = "收盤價" if market == "TW" else "收盤"
        if not all(
            name in fields for name in (code_name, amount_name, close_name, "成交股數")
        ):
            continue
        if actual != expected_date:
            raise SourceError("Official turnover report date missing/mismatched")
        indices = [
            fields.index(k) for k in (code_name, close_name, "成交股數", amount_name)
        ]
        for row in rows:
            code = str(row[indices[0]]).strip()
            if not re.fullmatch("[1-9][0-9]{3}", code):
                continue
            close, volume, amount = [to_number(row[i]) for i in indices[1:]]
            if (
                close is None or volume == 0
            ):  # no closing trade, not an invented zero-price day
                continue
            if (
                any(v is None or v <= 0 for v in (close, volume, amount))
                or int(volume) != volume
            ):
                raise SourceError("Invalid official shares / TWD amount")
            average = amount / volume
            if "均價" in fields:
                published = to_number(row[fields.index("均價")])
                if published is not None and abs(average - published) > 0.03:
                    raise SourceError(
                        "TPEx published average disagrees with amount/shares"
                    )
            out[code + "." + market] = dict(
                close=close, volume=int(volume), amount=amount, average=average
            )
    if not out:
        raise SourceError("No dated official stock turnover table")
    return out
