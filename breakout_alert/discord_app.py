import logging
import re
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from breakout_alert.cache_query import (
    CacheQueryService,
)
from breakout_alert.config import (
    get_discord_public_key,
)


app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    )
)

logger = logging.getLogger(
    "discord_volume_query"
)

DISCORD_PING_TYPE = 1
DISCORD_APPLICATION_COMMAND_TYPE = 2

DISCORD_PONG_RESPONSE_TYPE = 1
DISCORD_CHANNEL_MESSAGE_RESPONSE_TYPE = 4

DISCORD_EPHEMERAL_FLAG = 64

MAX_REQUEST_AGE_SECONDS = 300

TICKER_PATTERN = re.compile(
    r"^[A-Z0-9.^=-]{1,20}$"
)

verify_key = VerifyKey(
    bytes.fromhex(
        get_discord_public_key()
    )
)


def utc_now_string():
    return (
        datetime.now(timezone.utc)
        .isoformat()
    )


def build_ephemeral_response(content):
    return jsonify(
        {
            "type": (
                DISCORD_CHANNEL_MESSAGE_RESPONSE_TYPE
            ),
            "data": {
                "content": str(content)[:1950],
                "flags": DISCORD_EPHEMERAL_FLAG,
                "allowed_mentions": {
                    "parse": []
                }
            }
        }
    )


def verify_discord_request(
    signature,
    timestamp,
    raw_body
):
    if not signature or not timestamp:
        return False

    try:
        timestamp_value = int(timestamp)

    except (TypeError, ValueError):
        return False

    current_timestamp = int(time.time())

    if abs(
        current_timestamp - timestamp_value
    ) > MAX_REQUEST_AGE_SECONDS:
        logger.warning(
            "拒絕過期的 Discord 請求"
        )
        return False

    try:
        signed_message = (
            timestamp.encode("utf-8")
            + raw_body
        )

        verify_key.verify(
            signed_message,
            bytes.fromhex(signature)
        )

        return True

    except (
        BadSignatureError,
        ValueError,
        TypeError
    ):
        return False


def get_command_option(
    interaction_data,
    option_name
):
    options = interaction_data.get(
        "options",
        []
    )

    if not isinstance(options, list):
        return None

    for option in options:
        if not isinstance(option, dict):
            continue

        if option.get("name") == option_name:
            return option.get("value")

    return None


def normalize_input_ticker(value):
    ticker = str(
        value or ""
    ).strip().upper()

    ticker = ticker.replace(
        " ",
        ""
    )

    if not ticker:
        return ""

    if not TICKER_PATTERN.fullmatch(ticker):
        return ""

    return ticker


@app.get("/")
def root():
    return jsonify(
        {
            "service": (
                "stock-volume-query"
            ),
            "status": "ready",
            "time": utc_now_string()
        }
    ), 200


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "time": utc_now_string()
        }
    ), 200


@app.post("/discord/interactions")
def discord_interactions():
    raw_body = request.get_data(
        cache=True,
        as_text=False
    )

    signature = request.headers.get(
        "X-Signature-Ed25519",
        ""
    )

    timestamp = request.headers.get(
        "X-Signature-Timestamp",
        ""
    )

    if not verify_discord_request(
        signature,
        timestamp,
        raw_body
    ):
        logger.warning(
            "Discord Interaction 簽章驗證失敗"
        )

        return jsonify(
            {
                "error": "invalid request signature"
            }
        ), 401

    interaction = request.get_json(
        silent=True
    )

    if not isinstance(interaction, dict):
        return jsonify(
            {
                "error": "invalid json body"
            }
        ), 400

    interaction_type = interaction.get(
        "type"
    )

    # Discord 儲存 Interactions Endpoint URL
    # 時會傳送 Ping，必須回覆 Pong。
    if interaction_type == DISCORD_PING_TYPE:
        return jsonify(
            {
                "type": DISCORD_PONG_RESPONSE_TYPE
            }
        ), 200

    if (
        interaction_type
        != DISCORD_APPLICATION_COMMAND_TYPE
    ):
        return build_ephemeral_response(
            "不支援此互動類型。"
        ), 200

    interaction_data = interaction.get(
        "data",
        {}
    )

    command_name = str(
        interaction_data.get("name")
        or ""
    ).strip()

    if command_name != "量比":
        return build_ephemeral_response(
            "不支援此指令。"
        ), 200

    raw_ticker = get_command_option(
        interaction_data,
        "股票代號"
    )

    ticker = normalize_input_ticker(
        raw_ticker
    )

    if not ticker:
        return build_ephemeral_response(
            "股票代號格式不正確。"
        ), 200

    query_service = None

    try:
        query_service = (
            CacheQueryService()
        )

        message = (
            query_service
            .build_volume_ratio_message(
                ticker
            )
        )

        logger.info(
            "完成量比快取查詢：%s",
            ticker
        )

        return build_ephemeral_response(
            message
        ), 200

    except Exception as exc:
        logger.exception(
            "量比快取查詢失敗：%s",
            ticker
        )

        return build_ephemeral_response(
            "量比查詢暫時失敗，請稍後再試。"
        ), 200

    finally:
        if query_service is not None:
            query_service.close()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False
    )