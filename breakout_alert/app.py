import hmac
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from breakout_alert.runner import main as run_breakout_monitor


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
    "breakout_alert"
)

# 防止同一個 Cloud Run 容器同時執行兩次監控。
# 正式部署時也會設定：
# max-instances=1、concurrency=1。
execution_lock = threading.Lock()


def utc_now_string():
    return (
        datetime.now(timezone.utc)
        .isoformat()
    )


@app.get("/")
def root():
    return jsonify(
        {
            "service": (
                "stock-breakout-alert"
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


def is_run_request_authorized():
    """
    選用的簡易驗證。

    未設定環境變數 RUN_TOKEN 時維持原行為（不驗證）。
    設定後，呼叫端必須帶上標頭：X-Run-Token: <RUN_TOKEN>
    （Cloud Scheduler 可在 HTTP headers 欄位加入）。
    """
    expected_token = os.environ.get(
        "RUN_TOKEN",
        ""
    ).strip()

    if not expected_token:
        return True

    provided_token = request.headers.get(
        "X-Run-Token",
        ""
    ).strip()

    return hmac.compare_digest(
        provided_token,
        expected_token
    )


@app.post("/run")
def run_monitor():
    if not is_run_request_authorized():
        return jsonify(
            {
                "status": "unauthorized",
                "message": "缺少或錯誤的 X-Run-Token"
            }
        ), 401

    request_id = (
        request.headers.get(
            "X-Cloud-Trace-Context",
            ""
        ).split("/")[0].strip()
        or str(uuid.uuid4())
    )

    acquired = execution_lock.acquire(
        blocking=False
    )

    if not acquired:
        logger.warning(
            "監控已在執行，略過重複請求：%s",
            request_id
        )

        return jsonify(
            {
                "status": "busy",
                "request_id": request_id,
                "message": (
                    "另一個監控程序正在執行"
                ),
                "time": utc_now_string()
            }
        ), 409

    started_at = time.monotonic()

    logger.info(
        "開始執行突破監控：%s",
        request_id
    )

    try:
        run_breakout_monitor()

        elapsed_seconds = (
            time.monotonic()
            - started_at
        )

        logger.info(
            "突破監控完成：%s，%.2f 秒",
            request_id,
            elapsed_seconds
        )

        return jsonify(
            {
                "status": "success",
                "request_id": request_id,
                "elapsed_seconds": round(
                    elapsed_seconds,
                    2
                ),
                "time": utc_now_string()
            }
        ), 200

    except Exception as exc:
        elapsed_seconds = (
            time.monotonic()
            - started_at
        )

        logger.exception(
            "突破監控失敗：%s",
            request_id
        )

        return jsonify(
            {
                "status": "error",
                "request_id": request_id,
                "error_type": (
                    type(exc).__name__
                ),
                "message": str(exc)[:300],
                "elapsed_seconds": round(
                    elapsed_seconds,
                    2
                ),
                "time": utc_now_string()
            }
        ), 500

    finally:
        execution_lock.release()


if __name__ == "__main__":
    # 只供本機開發使用。
    # Cloud Run 正式環境使用 Gunicorn。
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False
    )
