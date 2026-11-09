"""Read-only portfolio conversion. All persisted numerics are Decimal strings."""

from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
import re
import uuid

MESSAGES = {
    "ok": "正常",
    "closed": "休市未同步",
    "login": "永豐登入失敗",
    "permission": "永豐帳務權限不足",
    "quota": "永豐流量超限，請稍後再試",
    "timeout": "永豐同步逾時，請稍後再試",
    "service": "永豐同步暫時無法使用",
    "conflict": "持股已更新，請重新同步",
    "rate": "60 秒內僅能同步一次",
    "calendar": "交易日曆尚未就緒",
    "invalid": "永豐資料無法完整辨識，未更新持股",
    "forbidden": "此帳號無永豐同步權限",
}


class SyncError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


def decimal(value, zero=False):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (not zero and n == 0) or n >= 10**15:
            raise ValueError()
        return n
    except (ValueError, InvalidOperation):
        raise SyncError("invalid") from None


def text(n):
    return (
        format(n, "f").rstrip("0").rstrip(".")
        if "." in format(n, "f")
        else format(n, "f")
    )


def field(row, key, default=None):
    return (
        row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)
    )


def enum(value):
    return str(getattr(value, "value", value)).split(".")[-1]


def convert(common, shares, catalog):
    merged = {}
    skipped = 0
    for rows, multiplier in [(common, 1000), (shares, 1)]:
        if not isinstance(rows, (list, tuple)):
            raise SyncError("invalid")
        for row in rows:
            direction = enum(field(row, "direction", ""))
            condition = enum(field(row, "cond", ""))
            if direction == "Sell" or condition in ("ShortSelling", "SBLShortSelling"):
                skipped += 1
                continue
            if direction != "Buy" or condition not in ("Cash", "MarginTrading"):
                raise SyncError("invalid")
            code = str(field(row, "code", ""))
            if not re.fullmatch(r"\d{4,6}[A-Z]?", code):
                raise SyncError("invalid")
            exchange = enum(field(row, "exchange", ""))
            symbol = (
                code + ".TW"
                if exchange == "TSE"
                else code + ".TWO" if exchange == "OTC" else catalog.get(code)
            )
            if not symbol or not re.fullmatch(r"\d{4,6}[A-Z]?\.(TW|TWO)", symbol):
                raise SyncError("invalid")
            quantity = decimal(field(row, "quantity"))
            if quantity != quantity.to_integral_value():
                raise SyncError("invalid")
            price = decimal(field(row, "price"), zero=True)
            with localcontext() as ctx:
                ctx.prec = 80
                q = quantity * multiplier
                oldq, cost = merged.get(symbol, (Decimal(0), Decimal(0)))
                merged[symbol] = (oldq + q, cost + q * price)
    result = []
    for symbol, (quantity, cost) in sorted(merged.items()):
        with localcontext() as ctx:
            ctx.prec = 80
            average = (cost / quantity).quantize(
                Decimal("0.000000000000000001"), rounding=ROUND_HALF_UP
            )
        result.append(
            {
                "symbol": symbol,
                "quantity": text(decimal(quantity)),
                "averageCost": text(decimal(average, zero=True)),
            }
        )
    return result, skipped


def read_positions(factory, units, key, secret):
    api = factory()
    stage = "login"
    completed = False
    try:
        api.login(
            api_key=key, secret_key=secret, subscribe_trade=False, force_refresh=True
        )
        stage = "permission"
        if not api.stock_account:
            raise SyncError("permission")
        common = api.list_positions(api.stock_account, unit=units.Common, timeout=10000)
        shares = api.list_positions(api.stock_account, unit=units.Share, timeout=10000)
        completed = True
        return common, shares
    except SyncError:
        raise
    except Exception as error:
        # Use type/code only, never the upstream exception's text.
        status = getattr(error, "status_code", getattr(error, "code", None))
        kind = type(error).__name__
        if isinstance(error, TimeoutError):
            code = "timeout"
        elif status in (429, "429") or kind == "MarketDataQueryUsageLimitError":
            code = "quota"
        elif isinstance(error, PermissionError) or status in (403, "403"):
            code = "permission"
        elif kind in ("AuthError", "TokenError") or status in (401, "401"):
            code = "login"
        else:
            code = "login" if stage == "login" else "service"
        raise SyncError(code) from None
    finally:
        try:
            if api.logout() is False and completed:
                raise SyncError("service")
        except Exception:
            # Reject uncertain success, but preserve a prior sanitized query/login failure.
            if completed:
                raise SyncError("service") from None


def signature(rows):
    return sorted(
        (
            h["symbol"],
            text(decimal(h["quantity"])),
            text(decimal(h["averageCost"], zero=True)),
            h.get("source", "api"),
        )
        for h in rows
    )


def synchronize(
    repo,
    owner,
    account_id,
    caller,
    today,
    calendar,
    fetch_positions,
    catalog,
    timestamp,
):
    if caller != owner:
        raise SyncError("forbidden")
    if not repo.claim(owner, account_id):
        raise SyncError("rate")
    skipped = 0
    try:
        if not calendar["validFrom"] <= today <= calendar["validTo"]:
            raise SyncError("calendar")
        if today not in calendar["markets"]["TW"]["sessions"]:
            repo.finish(owner, account_id, "closed", 0, False)
            return {"code": "closed", "message": MESSAGES["closed"]}
        # Account identity is validated before logging into the broker.
        document = repo.list(owner)
        old = next(
            (x for x in document["accounts"] if x["account"]["id"] == account_id), None
        )
        if old and any(
            old["account"].get(k) != v
            for k, v in {
                "broker": "sinopac",
                "kind": "tw-broker",
                "currency": "TWD",
            }.items()
        ):
            raise SyncError("forbidden")
        common, shares = fetch_positions()
        rows, skipped = convert(common, shares, catalog)
        account = (
            old["account"]
            if old
            else {
                "id": account_id,
                "name": "永豐證券",
                "broker": "sinopac",
                "kind": "tw-broker",
                "currency": "TWD",
            }
        )
        if not old or signature(rows) != signature(old["holdings"]):
            holdings = [
                dict(
                    h,
                    id=str(uuid.uuid5(uuid.UUID(account_id), h["symbol"])),
                    accountId=account_id,
                    market="TW",
                    costCurrency="TWD",
                    source="api",
                    updatedAt=timestamp,
                )
                for h in rows
            ]
            repo.replace(owner, account, holdings, old["revision"] if old else None)
        repo.finish(owner, account_id, "ok", skipped, True)
        return {"code": "ok", "message": MESSAGES["ok"]}
    except Exception as error:
        code = error.code if isinstance(error, SyncError) else "service"
        repo.finish(owner, account_id, code, skipped, False)
        raise SyncError(code) from None
