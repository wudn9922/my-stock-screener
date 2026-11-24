"""Supabase REST access with the service-role key. Errors never carry response bodies."""

from datetime import datetime, timedelta, timezone

import requests


class RepositoryError(Exception):
    pass


class Repository:
    def __init__(self, url, key, session=None):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.headers = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
        self.session = session or requests.Session()

    def _post(self, path, body, prefer=None):
        return self._request("post", path, body, prefer)

    def _request(self, method, path, body=None, prefer=None):
        headers = dict(self.headers, **({"Prefer": prefer} if prefer else {}))
        try:
            response = self.session.request(method, self.base + path, headers=headers, json=body, timeout=20, allow_redirects=False)
        except Exception:  # noqa: BLE001
            raise RepositoryError() from None
        if not response.ok:
            raise RepositoryError()
        return response.json() if response.content else None

    def save_trades(self, owner, broker, message_id, trades):
        if not trades:
            return
        rows = [{
            "line_user_id": owner, "broker": broker, "market": t.market, "trade_key": t.key, "trade_date": t.trade_date,
            "symbol": t.symbol, "side": t.side, "quantity": str(t.quantity), "price": str(t.price),
            "amount": str(t.amount), "currency": t.currency, "message_id": message_id,
        } for t in trades]
        # A re-sent e-mail carries the same keys: duplicates are ignored, never applied twice.
        self._post("atlas_statement_trades?on_conflict=line_user_id,broker,market,trade_key", rows,
                   "resolution=ignore-duplicates,return=minimal")

    def save_snapshot(self, owner, broker, message_id, report_date, snapshot):
        positions = {symbol: str(quantity) for symbol, quantity in snapshot["positions"].items()}
        self._post("atlas_statement_snapshots?on_conflict=line_user_id,broker,market,report_date", [{
            "line_user_id": owner, "broker": broker, "market": snapshot["market"], "report_date": report_date,
            "positions": positions, "message_id": message_id,
        }], "resolution=ignore-duplicates,return=minimal")

    def save_layout(self, owner, message_id, *, code, detail, pages):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        self._request("delete", f"atlas_statement_layouts?line_user_id=eq.{owner}&created_at=lt.{cutoff}")
        self._post("atlas_statement_layouts?on_conflict=line_user_id,message_id", [{
            "line_user_id": owner, "message_id": message_id, "code": code, "detail": detail or None, "pages": pages,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }], "resolution=merge-duplicates,return=minimal")

    def save_message(self, owner, message_id, *, broker, received_at, report_date, status, detail, trades):
        self._post("atlas_statement_messages?on_conflict=line_user_id,message_id", [{
            "line_user_id": owner, "message_id": message_id, "broker": broker, "received_at": received_at,
            "report_date": report_date, "status": status, "detail": detail or None, "trades": trades,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }], "resolution=merge-duplicates,return=minimal")

    def apply(self, owner, broker, market):
        result = self._post("rpc/atlas_statement_apply", {"owner_id": owner, "broker_code": broker, "market_code": market})
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise RepositoryError()
        return result
