import requests
from .core import SyncError


class Repository:
    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/rest/v1/rpc/"
        self.headers = {
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        }

    def rpc(self, name, payload):
        try:
            r = requests.post(
                self.base + name,
                headers=self.headers,
                json=payload,
                timeout=15,
                allow_redirects=False,
            )
            if not r.ok:
                raise SyncError("service")
            # Void RPCs (atlas_sinopac_finish) answer 204 No Content.
            if r.status_code == 204 or not r.content:
                return None
            return r.json()
        except SyncError:
            raise
        except Exception:
            raise SyncError("service") from None

    def claim(self, owner, account):
        data = self.rpc(
            "atlas_sinopac_claim", {"owner_id": owner, "account_uuid": account}
        )
        if data.get("status") == "forbidden":
            raise SyncError("forbidden")
        if data.get("status") not in ("ok", "rate"):
            raise SyncError("service")
        return data["status"] == "ok"

    def finish(self, owner, account, code, skipped, success):
        self.rpc(
            "atlas_sinopac_finish",
            {
                "owner_id": owner,
                "account_uuid": account,
                "result_code": code,
                "short_count": skipped,
                "success": success,
            },
        )

    def action(self, owner, operation, payload=None, revision=None):
        data = self.rpc(
            "atlas_holdings_action",
            {
                "operation": operation,
                "owner_id": owner,
                "payload": payload or {},
                "expected_revision": revision,
            },
        )
        return self.document(data)

    @staticmethod
    def document(data):
        if data.get("status") != "ok":
            code = data.get("status")
            raise SyncError(code if code in ("conflict", "forbidden") else "service")
        return data["document"]

    def list(self, owner):
        return self.action(owner, "list")

    def replace(self, owner, account, holdings, revision):
        return self.document(
            self.rpc(
                "atlas_sinopac_replace",
                {
                    "owner_id": owner,
                    "payload": {"account": account, "holdings": holdings},
                    "expected_revision": revision,
                },
            )
        )
