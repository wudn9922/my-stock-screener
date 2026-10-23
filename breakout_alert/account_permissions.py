"""Storage consent for scheduled private-data consumers; a login never enrolls a user.

This is the same fail-closed policy as the Atlas Edge Functions. Public market/K-line
collectors do not need these permissions; only account settings and alert state do.
"""

import os
import re

import requests

DEFAULT_ADMIN_LINE_ID = "Ua0037dbd5037b64fc76804c60520ad76"
LINE_USER_ID = re.compile(r"U[0-9a-f]{32}\Z")
STORAGE_MODES = frozenset({"none", "device", "cloud"})
MISSING_TABLE_CODES = frozenset({"42P01", "PGRST205"})


class CloudStorageDenied(PermissionError):
    """The user has not authorized server storage, or policy cannot be verified."""


def admin_user_id():
    return os.environ.get("ADMIN_LINE_USER_ID", "").strip() or DEFAULT_ADMIN_LINE_ID


def is_public_report_owner(user_id):
    """Only the owner's chosen public configuration may become a GitHub artifact."""
    return (
        bool(LINE_USER_ID.fullmatch(str(user_id or ""))) and user_id == admin_user_id()
    )


def storage_mode(user_id, rest_url, *, headers=None, request=None):
    """Read one existing grant. Never POST/UPSERT a record or trust a client claim.

    The existing owner retains cloud access while bootstrapping the new table. A
    real owner's explicit `device`/`none` row overrides that default. Transport,
    malformed responses, and all other database errors deny access to everyone.
    """
    if not LINE_USER_ID.fullmatch(str(user_id or "")):
        return "none"
    if not str(rest_url).startswith("https://"):
        return "none"
    owner_default = "cloud" if is_public_report_owner(user_id) else "none"
    try:
        response = (request or requests.get)(
            f"{rest_url.rstrip('/')}/atlas_storage_access",
            headers=headers or {},
            params={
                "select": "line_user_id,storage_mode",
                "line_user_id": f"eq.{user_id}",
                "limit": "2",
            },
            timeout=15,
        )
        body = response.json()
        if response.status_code >= 400:
            if isinstance(body, dict) and body.get("code") in MISSING_TABLE_CODES:
                return owner_default
            return "none"
        if not isinstance(body, list) or len(body) > 1:
            return "none"
        if not body:
            return owner_default
        row = body[0]
        if not isinstance(row, dict) or row.get("line_user_id") != user_id:
            return "none"
        value = row.get("storage_mode")
        return value if value in STORAGE_MODES else "none"
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return "none"


def require_cloud_storage(user_id, rest_url, *, headers=None, request=None):
    if storage_mode(user_id, rest_url, headers=headers, request=request) != "cloud":
        raise CloudStorageDenied(
            "Account cloud storage is disabled or permission is unavailable"
        )
