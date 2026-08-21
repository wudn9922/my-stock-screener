from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

TW_CALENDAR_NAME = "XTAI"
US_CALENDAR_NAME = "XNYS"

TW_REGULAR_OPEN = time(9, 0)
TW_REGULAR_CLOSE = time(13, 30)

US_PRE_MARKET_OPEN = time(4, 0)
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)
US_AFTER_HOURS_CLOSE = time(20, 0)

# 美股提早收盤日通常盤後交易至 17:00
US_EARLY_AFTER_HOURS_CLOSE = time(17, 0)


@dataclass(frozen=True)
class MarketDecision:
    market: str
    session_type: str
    should_run: bool
    interval_minutes: int | None
    local_time: str
    session_date: str
    slot_key: str | None
    reason: str

    def to_dict(self):
        return asdict(self)


@lru_cache(maxsize=2)
def get_exchange_calendar(calendar_name):
    try:
        return xcals.get_calendar(
            calendar_name
        )

    except Exception as exc:
        raise RuntimeError(
            f"無法載入交易所日曆："
            f"{calendar_name}"
        ) from exc


def normalize_utc_datetime(now_utc=None):
    if now_utc is None:
        return datetime.now(timezone.utc)

    if not isinstance(now_utc, datetime):
        raise TypeError(
            "now_utc 必須是 datetime"
        )

    if now_utc.tzinfo is None:
        return now_utc.replace(
            tzinfo=timezone.utc
        )

    return now_utc.astimezone(
        timezone.utc
    )


def combine_local_datetime(
    local_date,
    local_time,
    timezone_value
):
    return datetime.combine(
        local_date,
        local_time,
        tzinfo=timezone_value
    )


def is_exchange_session(
    calendar_name,
    local_date
):
    calendar = get_exchange_calendar(
        calendar_name
    )

    session_label = pd.Timestamp(
        local_date
    )

    try:
        return bool(
            calendar.is_session(
                session_label
            )
        )

    except Exception as exc:
        raise RuntimeError(
            f"交易日判斷失敗："
            f"{calendar_name} / "
            f"{local_date}"
        ) from exc


def get_exchange_session_times(
    calendar_name,
    local_date,
    local_timezone
):
    calendar = get_exchange_calendar(
        calendar_name
    )

    session_label = pd.Timestamp(
        local_date
    )

    try:
        open_timestamp = (
            calendar.session_open(
                session_label
            )
        )

        close_timestamp = (
            calendar.session_close(
                session_label
            )
        )

    except Exception as exc:
        raise RuntimeError(
            f"取得交易時間失敗："
            f"{calendar_name} / "
            f"{local_date}"
        ) from exc

    open_datetime = (
        open_timestamp
        .to_pydatetime()
        .astimezone(local_timezone)
    )

    close_datetime = (
        close_timestamp
        .to_pydatetime()
        .astimezone(local_timezone)
    )

    return (
        open_datetime,
        close_datetime
    )


def is_interval_due(
    current_datetime,
    period_start,
    interval_minutes
):
    if current_datetime < period_start:
        return False

    elapsed_seconds = (
        current_datetime
        - period_start
    ).total_seconds()

    elapsed_minutes = int(
        elapsed_seconds // 60
    )

    return (
        elapsed_minutes
        % int(interval_minutes)
        == 0
    )


def build_slot_key(
    market,
    session_type,
    local_datetime,
    interval_minutes
):
    if interval_minutes is None:
        return None

    minute_value = (
        local_datetime.minute
        // interval_minutes
        * interval_minutes
    )

    slot_datetime = (
        local_datetime
        .replace(
            minute=minute_value,
            second=0,
            microsecond=0
        )
    )

    return (
        f"{market}:"
        f"{session_type}:"
        f"{slot_datetime.isoformat()}"
    )


def build_closed_decision(
    market,
    local_datetime,
    reason
):
    return MarketDecision(
        market=market,
        session_type="closed",
        should_run=False,
        interval_minutes=None,
        local_time=(
            local_datetime.isoformat()
        ),
        session_date=(
            local_datetime.date()
            .isoformat()
        ),
        slot_key=None,
        reason=reason
    )


def build_active_decision(
    *,
    market,
    session_type,
    local_datetime,
    period_start,
    interval_minutes,
    reason
):
    should_run = is_interval_due(
        local_datetime,
        period_start,
        interval_minutes
    )

    slot_key = None

    if should_run:
        slot_key = build_slot_key(
            market,
            session_type,
            local_datetime,
            interval_minutes
        )

    if should_run:
        final_reason = reason
    else:
        final_reason = (
            f"{reason}，"
            f"尚未到每 "
            f"{interval_minutes} 分鐘執行點"
        )

    return MarketDecision(
        market=market,
        session_type=session_type,
        should_run=should_run,
        interval_minutes=interval_minutes,
        local_time=(
            local_datetime.isoformat()
        ),
        session_date=(
            local_datetime.date()
            .isoformat()
        ),
        slot_key=slot_key,
        reason=final_reason
    )


def get_tw_market_decision(
    now_utc=None
):
    now_utc = normalize_utc_datetime(
        now_utc
    )

    local_datetime = now_utc.astimezone(
        TAIPEI_TIMEZONE
    )

    local_date = local_datetime.date()

    if not is_exchange_session(
        TW_CALENDAR_NAME,
        local_date
    ):
        return build_closed_decision(
            "TW",
            local_datetime,
            "台股休市日"
        )

    (
        calendar_open,
        calendar_close
    ) = get_exchange_session_times(
        TW_CALENDAR_NAME,
        local_date,
        TAIPEI_TIMEZONE
    )

    # 採用交易所日曆時間；
    # 若日曆回傳異常，才使用台股固定時間。
    regular_open = calendar_open
    regular_close = calendar_close

    if regular_open.date() != local_date:
        regular_open = (
            combine_local_datetime(
                local_date,
                TW_REGULAR_OPEN,
                TAIPEI_TIMEZONE
            )
        )

    if regular_close.date() != local_date:
        regular_close = (
            combine_local_datetime(
                local_date,
                TW_REGULAR_CLOSE,
                TAIPEI_TIMEZONE
            )
        )

    if local_datetime < regular_open:
        return build_closed_decision(
            "TW",
            local_datetime,
            "台股尚未開盤"
        )

    if local_datetime >= regular_close:
        return build_closed_decision(
            "TW",
            local_datetime,
            "台股已收盤"
        )

    first_ten_minutes_end = (
        regular_open
        + timedelta(minutes=10)
    )

    if local_datetime < first_ten_minutes_end:
        return build_active_decision(
            market="TW",
            session_type="regular",
            local_datetime=local_datetime,
            period_start=regular_open,
            interval_minutes=1,
            reason=(
                "台股正常盤開盤後前10分鐘"
            )
        )

    return build_active_decision(
        market="TW",
        session_type="regular",
        local_datetime=local_datetime,
        period_start=first_ten_minutes_end,
        interval_minutes=5,
        reason="台股正常盤"
    )


def get_us_market_decision(
    now_utc=None
):
    """
    美股只監控正常交易時段。

    盤前與盤後直接略過，不取得股價、
    不計算均線、不更新突破狀態。
    """
    now_utc = normalize_utc_datetime(
        now_utc
    )

    local_datetime = now_utc.astimezone(
        NEW_YORK_TIMEZONE
    )

    local_date = local_datetime.date()

    if not is_exchange_session(
        US_CALENDAR_NAME,
        local_date
    ):
        return build_closed_decision(
            "US",
            local_datetime,
            "美股休市日"
        )

    (
        regular_open,
        regular_close
    ) = get_exchange_session_times(
        US_CALENDAR_NAME,
        local_date,
        NEW_YORK_TIMEZONE
    )

    if local_datetime < regular_open:
        return build_closed_decision(
            "US",
            local_datetime,
            "美股盤前監控已關閉"
        )

    first_ten_minutes_end = (
        regular_open
        + timedelta(minutes=10)
    )

    if local_datetime < first_ten_minutes_end:
        return build_active_decision(
            market="US",
            session_type="regular",
            local_datetime=local_datetime,
            period_start=regular_open,
            interval_minutes=1,
            reason=(
                "美股正常盤開盤後前10分鐘"
            )
        )

    if local_datetime < regular_close:
        return build_active_decision(
            market="US",
            session_type="regular",
            local_datetime=local_datetime,
            period_start=first_ten_minutes_end,
            interval_minutes=5,
            reason="美股正常盤"
        )

    return build_closed_decision(
        "US",
        local_datetime,
        "美股盤後監控已關閉"
    )


def get_market_decisions(
    now_utc=None
):
    normalized_now = (
        normalize_utc_datetime(
            now_utc
        )
    )

    return {
        "TW": get_tw_market_decision(
            normalized_now
        ),
        "US": get_us_market_decision(
            normalized_now
        )
    }


def get_due_markets(
    now_utc=None
):
    decisions = get_market_decisions(
        now_utc
    )

    return {
        market: decision
        for market, decision
        in decisions.items()
        if decision.should_run
    }
