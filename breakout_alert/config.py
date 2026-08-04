
import os
from urllib.parse import urlparse


def require_env(name):
    value = os.environ.get(
        name,
        ""
    ).strip()

    if not value:
        raise RuntimeError(
            f"未設定必要環境變數：{name}"
        )

    return value


def get_supabase_rest_url():
    base_url = require_env(
        "SUPABASE_URL"
    ).rstrip("/")

    if base_url.endswith("/rest/v1"):
        rest_url = base_url
        project_url = base_url[:-8].rstrip("/")
    else:
        project_url = base_url
        rest_url = f"{base_url}/rest/v1"

    parsed = urlparse(project_url)

    if parsed.scheme != "https":
        raise RuntimeError(
            "SUPABASE_URL 必須使用 HTTPS"
        )

    if not parsed.hostname:
        raise RuntimeError(
            "SUPABASE_URL 格式不正確"
        )

    return rest_url


def get_supabase_service_key():
    # 突破提醒狀態表已啟用 RLS，
    # 正式執行時只允許 service_role／Secret key。
    return require_env(
        "SUPABASE_SERVICE_ROLE_KEY"
    )


def get_supabase_user_id():
    return require_env(
        "SUPABASE_USER_ID"
    )


def get_discord_webhook_url():
    webhook_url = require_env(
        "DISCORD_WEBHOOK_URL"
    )

    parsed = urlparse(webhook_url)

    allowed_hosts = {
        "discord.com",
        "www.discord.com",
        "discordapp.com",
        "www.discordapp.com"
    }

    if parsed.scheme != "https":
        raise RuntimeError(
            "Discord Webhook 必須使用 HTTPS"
        )

    if parsed.hostname not in allowed_hosts:
        raise RuntimeError(
            "Discord Webhook 網域格式不正確"
        )

    if not parsed.path.startswith(
        "/api/webhooks/"
    ):
        raise RuntimeError(
            "Discord Webhook 路徑格式不正確"
        )

    return webhook_url



def require_env(name):
    value = os.environ.get(
        name,
        ""
    ).strip()

    if not value:
        raise RuntimeError(
            f"未設定必要環境變數：{name}"
        )

    return value


def get_supabase_url():
    base_url = require_env(
        "SUPABASE_URL"
    ).rstrip("/")

    if base_url.endswith("/rest/v1"):
        return base_url

    return f"{base_url}/rest/v1"


def get_supabase_key():
    return (
        os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY",
            ""
        ).strip()
        or os.environ.get(
            "SUPABASE_ANON_KEY",
            ""
        ).strip()
    )


def get_supabase_user_id():
    return require_env(
        "SUPABASE_USER_ID"
    )


def get_discord_webhook_url():
    return require_env(
        "DISCORD_WEBHOOK_URL"
    )

def get_discord_public_key():
    public_key = require_env(
        "DISCORD_PUBLIC_KEY"
    ).lower()

    if len(public_key) != 64:
        raise RuntimeError(
            "DISCORD_PUBLIC_KEY 長度不正確"
        )

    try:
        bytes.fromhex(public_key)

    except ValueError as exc:
        raise RuntimeError(
            "DISCORD_PUBLIC_KEY 必須是十六進位格式"
        ) from exc

    return public_key