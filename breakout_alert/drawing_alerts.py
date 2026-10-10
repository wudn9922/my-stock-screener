"""
Atlas 畫線警示。

讀取使用者在 Atlas 圖表上的畫線（chart_drawings，timeframe='atlas'），
以最新價判斷是否向上突破或向下跌破，並發送 Discord 提醒。

本模組只用標準函式庫，不 import price_provider、market_clock 或 yfinance；
報價與市場時段由 runner 傳入。任何錯誤都由 runner 隔離，不影響均線提醒。
"""

import copy
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote as url_quote


# 總開關：改成 False 即停用畫線警示（均線提醒不受影響）
DRAWING_ALERTS_ENABLED = True

# 費波那契回撤預設不提醒；開啟時只取 0.382 / 0.5 / 0.618 中可見的價位
FIB_ALERTS = False
FIB_ALERT_LEVELS = (0.382, 0.5, 0.618)
DEFAULT_FIB_LEVELS = (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1)

# 突破確認緩衝：高於畫線 0.5% 為 above，低於 0.5% 為 below，
# 介於中間延續前次位置。
LINE_UP_RATIO = 0.005
LINE_DOWN_RATIO = 0.005

# 連續幾次執行都在另一側才發提醒
CONFIRM_RUNS = 2

# 矩形右邊界早於這個天數就不提醒（舊的箱型）
RECTANGLE_MAX_AGE_DAYS = 60

# 價位變動超過此值視為移動畫線，重新建立基準
LEVEL_EPSILON = 1e-6

# 每次執行最多發送的提醒數；超過 3 則合併成一則訊息
MAX_ALERTS_PER_RUN = 10
COMBINE_THRESHOLD = 3
MESSAGE_MAX_LENGTH = 1950

# 畫線股票數上限，避免報價時間過長
MAX_DRAWING_TICKERS = 200

# 狀態列（breakout_alert_state，每檔一列）
STATE_GROUP_ID = "atlas_lines"
STATE_MA_PERIOD = 1
STATE_KIND = "atlas_lines"
STATE_VERSION = 1
FALLBACK_KIND = "atlas_lines_state"

# 後備狀態超過此天數未更新就忽略（重新建立基準），
# 避免畫線全部隱藏後又顯示時沿用很久以前的狀態。
FALLBACK_MAX_AGE_DAYS = 3
UPSERT_CHUNK_SIZE = 100

CHART_LIFF_URL = (
    "https://liff.line.me/"
    "2010330411-6JhrotT9"
)

VALID_TIMEFRAMES = (
    "5m", "15m", "30m", "1H", "4H", "1D", "1W", "1M"
)

TW_SYMBOL_PATTERN = re.compile(
    r"[0-9]{4,6}[A-Z]?\.(?:TW|TWO)"
)
US_SYMBOL_PATTERN = re.compile(
    r"[A-Z][A-Z0-9.\-]{0,14}"
)

DIRECTION_UP = "breakout_up"
DIRECTION_DOWN = "breakout_down"


@dataclass(frozen=True)
class AlertLine:
    key: str
    drawing_id: str
    level: float
    label: str
    description: str
    drawing_type: str
    timeframe: str


@dataclass
class WatchEntry:
    ticker: str
    symbol: str
    market: str
    lines: list = field(default_factory=list)


@dataclass
class DrawingWatchlist:
    entries: list
    row_count: int
    valid_tickers: set
    line_count: int = 0


@dataclass
class DrawingAlert:
    plan: object
    line: AlertLine
    direction: str
    previous_state: dict
    nearest_above: float | None
    nearest_below: float | None


@dataclass
class TickerPlan:
    entry: WatchEntry
    quote: object
    price: float
    decision: object
    previous_lines: dict
    lines: dict
    changed: bool
    alerts: list = field(default_factory=list)


def utc_iso(now=None):
    # 固定格式（含微秒），字串比較即可判斷新舊
    now = now or datetime.now(timezone.utc)

    return (
        now.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )


def finite_positive(value):
    if isinstance(value, bool):
        return None

    if not isinstance(value, (int, float)):
        return None

    value = float(value)

    if not math.isfinite(value) or value <= 0:
        return None

    return value


def safe_price(value):
    try:
        if value is None or isinstance(value, bool):
            return None

        return finite_positive(float(value))

    except (TypeError, ValueError):
        return None


def map_atlas_symbol(symbol):
    """
    Atlas 代號 → (報價代號, 市場)。

    ^ 開頭的指數與上證／深證代號不提醒；
    .TW / .TWO 為台股，其餘為美股（'.' 換成 '-'，與均線監控一致）。
    """
    if not isinstance(symbol, str):
        return None

    normalized = symbol.strip().upper()

    if not normalized or "^" in normalized:
        return None

    if normalized.endswith((".SS", ".SZ")):
        return None

    if normalized.endswith((".TW", ".TWO")):
        if TW_SYMBOL_PATTERN.fullmatch(normalized):
            return normalized, "TW"

        return None

    if not US_SYMBOL_PATTERN.fullmatch(normalized):
        return None

    return normalized.replace(".", "-"), "US"


def point_time(point):
    value = finite_positive(
        point.get("time")
    )

    if value is None:
        return None

    # Atlas 存 Unix 秒；容忍毫秒
    if value > 1e11:
        value = value / 1000

    return value


def resolve_timeframe(drawing, points):
    timeframe = points[0].get("timeframe")

    if timeframe in VALID_TIMEFRAMES:
        return timeframe

    scope = drawing.get("scope")

    if isinstance(scope, dict):
        timeframes = scope.get("timeframes")

        if isinstance(timeframes, list):
            for item in timeframes:
                if item in VALID_TIMEFRAMES:
                    return item

    return "1D"


def level_in(levels, target):
    return any(
        finite_number(level) is not None
        and abs(float(level) - target) < 1e-9
        for level in levels
    )


def finite_number(value):
    if isinstance(value, bool):
        return None

    if not isinstance(value, (int, float)):
        return None

    value = float(value)

    return value if math.isfinite(value) else None


def extract_alert_lines(
    drawing,
    symbol,
    now_ts,
    fib_alerts=None
):
    """
    從一筆畫線取出可提醒的價位。

    納入：可見的水平線、向右延伸的水平射線、
    近 60 天內的矩形上緣／下緣、（開啟時）費波那契價位。
    隱藏的畫線＝使用者關閉該提醒。
    """
    if fib_alerts is None:
        fib_alerts = FIB_ALERTS

    if not isinstance(drawing, dict):
        return []

    if drawing.get("visible") is not True:
        return []

    drawing_id = drawing.get("id")

    if (
        not isinstance(drawing_id, str)
        or not drawing_id.strip()
    ):
        return []

    drawing_id = drawing_id.strip()

    drawing_symbol = drawing.get("symbol")

    if (
        not isinstance(drawing_symbol, str)
        or drawing_symbol.strip().upper() != symbol
    ):
        return []

    points = drawing.get("points")

    if (
        not isinstance(points, list)
        or not points
        or not all(
            isinstance(point, dict)
            for point in points
        )
    ):
        return []

    drawing_type = drawing.get("type")
    timeframe = resolve_timeframe(
        drawing,
        points
    )

    def make_line(suffix, level, label, description):
        return AlertLine(
            key=f"{drawing_id}#{suffix}",
            drawing_id=drawing_id,
            level=level,
            label=label,
            description=description,
            drawing_type=str(drawing_type),
            timeframe=timeframe
        )

    if drawing_type == "horizontal":
        level = finite_positive(
            points[0].get("price")
        )

        if level is None:
            return []

        return [
            make_line("h", level, "水平線", "水平線")
        ]

    if drawing_type == "ray":
        if len(points) < 2:
            return []

        start_time = point_time(points[0])
        end_time = point_time(points[1])
        level = finite_positive(
            points[0].get("price")
        )

        # P2 時間在 P1 之前代表向左延伸，不提醒
        if (
            start_time is None
            or end_time is None
            or level is None
            or end_time < start_time
        ):
            return []

        return [
            make_line("h", level, "水平射線", "水平射線")
        ]

    if drawing_type == "rectangle":
        if len(points) < 2:
            return []

        first_time = point_time(points[0])
        second_time = point_time(points[1])
        first_price = finite_positive(
            points[0].get("price")
        )
        second_price = finite_positive(
            points[1].get("price")
        )

        if None in (
            first_time,
            second_time,
            first_price,
            second_price
        ):
            return []

        right_edge = max(first_time, second_time)

        if right_edge < (
            now_ts
            - RECTANGLE_MAX_AGE_DAYS * 86400
        ):
            return []

        top = max(first_price, second_price)
        bottom = min(first_price, second_price)

        lines = [
            make_line("top", top, "箱頂", "矩形上緣")
        ]

        if top - bottom > LEVEL_EPSILON:
            lines.append(
                make_line("bottom", bottom, "箱底", "矩形下緣")
            )

        return lines

    if drawing_type == "fibonacci" and fib_alerts:
        if len(points) < 2:
            return []

        origin_price = finite_positive(
            points[0].get("price")
        )
        end_price = finite_positive(
            points[1].get("price")
        )

        if origin_price is None or end_price is None:
            return []

        levels = drawing.get("levels")

        if not isinstance(levels, list):
            levels = list(DEFAULT_FIB_LEVELS)

        style = drawing.get("style")
        hidden = (
            style.get("hiddenLevels")
            if isinstance(style, dict)
            else None
        )

        if not isinstance(hidden, list):
            hidden = []

        lines = []

        for fib_level in FIB_ALERT_LEVELS:
            if not level_in(levels, fib_level):
                continue

            if level_in(hidden, fib_level):
                continue

            # 與 Atlas 相同：0 為 P2（波段終點），1 為 P1（起點）
            price = finite_positive(
                end_price
                + (origin_price - end_price) * fib_level
            )

            if price is None:
                continue

            lines.append(
                make_line(
                    f"fib:{fib_level:g}",
                    price,
                    f"Fib {fib_level:g}",
                    f"費波那契 {fib_level:g}"
                )
            )

        return lines

    # 趨勢線、通道、各種區間、垂直線不提醒
    return []


def build_watchlist(
    rows,
    now_ts=None,
    fib_alerts=None
):
    if now_ts is None:
        now_ts = time.time()

    if not isinstance(rows, list):
        rows = []

    entries = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        mapped = map_atlas_symbol(
            row.get("ticker")
        )

        if mapped is None:
            continue

        ticker, market = mapped
        symbol = str(row.get("ticker")).strip().upper()
        drawings = row.get("drawings")

        if not isinstance(drawings, list):
            continue

        lines = []

        for drawing in drawings:
            try:
                lines.extend(
                    extract_alert_lines(
                        drawing,
                        symbol,
                        now_ts,
                        fib_alerts=fib_alerts
                    )
                )

            except Exception as exc:
                # 單一畫線格式錯誤不影響其他畫線
                print(
                    f"⚠️ {symbol}｜略過格式錯誤的畫線："
                    f"{type(exc).__name__}: {exc}"
                )

        if not lines:
            continue

        entry = entries.get(ticker)

        if entry is None:
            entry = WatchEntry(
                ticker=ticker,
                symbol=symbol,
                market=market
            )
            entries[ticker] = entry

        known_keys = {
            line.key
            for line in entry.lines
        }

        for line in lines:
            if line.key not in known_keys:
                entry.lines.append(line)
                known_keys.add(line.key)

    ordered = sorted(
        entries.values(),
        key=lambda entry: (entry.market, entry.ticker)
    )

    if len(ordered) > MAX_DRAWING_TICKERS:
        print(
            "⚠️ Atlas 畫線股票過多"
            f"（{len(ordered)} 檔），"
            f"只檢查前 {MAX_DRAWING_TICKERS} 檔"
        )
        ordered = ordered[:MAX_DRAWING_TICKERS]

    return DrawingWatchlist(
        entries=ordered,
        row_count=len(rows),
        valid_tickers=set(entries),
        line_count=sum(
            len(entry.lines)
            for entry in ordered
        )
    )


def load_drawing_watchlist(store, now_ts=None):
    rows = store.get_atlas_drawings()

    return build_watchlist(
        rows,
        now_ts=now_ts
    )


def classify_zone(price, level):
    price = safe_price(price)
    level = safe_price(level)

    if price is None or level is None:
        return None

    if price >= level * (1 + LINE_UP_RATIO):
        return "above"

    if price <= level * (1 - LINE_DOWN_RATIO):
        return "below"

    return "middle"


def safe_count(value):
    try:
        if isinstance(value, bool):
            return 0

        return max(int(value), 0)

    except (TypeError, ValueError):
        return 0


def advance_line(
    previous,
    line,
    price,
    *,
    now_iso,
    session_date,
    confirm_runs=None
):
    """
    單一畫線的狀態機，回傳 (新狀態, 事件)。

    事件：baseline（新線或移動過，只建立基準）、unchanged、
    pending（等待確認）、alert（確認突破，應發提醒）、
    suppressed（同方向本交易日已提醒過）、skipped（價格無效）。
    """
    if confirm_runs is None:
        confirm_runs = CONFIRM_RUNS

    previous = (
        previous
        if isinstance(previous, dict)
        else None
    )

    zone = classify_zone(price, line.level)

    if zone is None:
        return copy.deepcopy(previous), "skipped"

    alert_sessions = {}

    if previous and isinstance(
        previous.get("alertSessions"),
        dict
    ):
        alert_sessions = {
            str(key): value
            for key, value in previous["alertSessions"].items()
            if key in {DIRECTION_UP, DIRECTION_DOWN}
        }

    state = {
        "level": line.level,
        "label": line.label,
        "tf": line.timeframe,
        "type": line.drawing_type,
        "lastAlertAt": (
            previous.get("lastAlertAt")
            if previous else None
        ),
        "lastAlertDirection": (
            previous.get("lastAlertDirection")
            if previous else None
        ),
        "lastAlertSession": (
            previous.get("lastAlertSession")
            if previous else None
        ),
        "alertSessions": alert_sessions
    }

    previous_side = (
        previous.get("side")
        if previous else None
    )
    previous_level = (
        safe_price(previous.get("level"))
        if previous else None
    )

    # 新線、狀態損毀或畫線被移動：只建立基準，不發提醒
    if (
        previous is None
        or previous_side not in {"above", "below"}
        or previous_level is None
        or abs(previous_level - line.level) > LEVEL_EPSILON
    ):
        if zone == "middle":
            side = (
                "above"
                if float(price) >= line.level
                else "below"
            )
        else:
            side = zone

        state.update(side=side, pending=None)

        return state, "baseline"

    # 價位沒有實質變動時保留原值，避免無意義的寫入
    state["level"] = previous_level

    if zone == "middle" or zone == previous_side:
        state.update(side=previous_side, pending=None)

        return state, "unchanged"

    pending = previous.get("pending")

    if (
        isinstance(pending, dict)
        and pending.get("side") == zone
    ):
        count = safe_count(pending.get("count")) + 1
        since = pending.get("since") or now_iso
    else:
        count = 1
        since = now_iso

    if count < confirm_runs:
        state.update(
            side=previous_side,
            pending={
                "side": zone,
                "count": count,
                "since": since
            }
        )

        return state, "pending"

    direction = (
        DIRECTION_UP
        if zone == "above"
        else DIRECTION_DOWN
    )

    # 同線同方向每交易日最多提醒一次
    if alert_sessions.get(direction) == session_date or (
        state["lastAlertDirection"] == direction
        and state["lastAlertSession"] == session_date
    ):
        state.update(side=zone, pending=None)

        return state, "suppressed"

    alert_sessions = dict(alert_sessions)
    alert_sessions[direction] = session_date

    state.update(
        side=zone,
        pending=None,
        lastAlertAt=now_iso,
        lastAlertDirection=direction,
        lastAlertSession=session_date,
        alertSessions=alert_sessions
    )

    return state, "alert"


def format_price(value):
    return f"{value:,.2f}"


def format_signed_percent(value, digits):
    sign = "+" if value >= 0 else "−"

    return f"{sign}{abs(value):.{digits}f}%"


def format_neighbour(level, price):
    if level is None:
        return "無"

    change = (level / price - 1) * 100

    return (
        f"{format_price(level)}"
        f"（{format_signed_percent(change, 1)}）"
    )


def build_chart_url(symbol, timeframe):
    return (
        f"{CHART_LIFF_URL}?page=chart"
        f"&symbol={url_quote(symbol, safe='.-')}"
        f"&tf={url_quote(timeframe, safe='')}"
    )


def format_alert_block(
    *,
    ticker,
    symbol,
    display_name,
    line,
    price,
    direction,
    nearest_above,
    nearest_below
):
    title = ticker

    if display_name:
        title = f"{ticker} {display_name}"

    if direction == DIRECTION_UP:
        icon = "🚨"
        direction_text = "向上突破"
    else:
        icon = "⚠️"
        direction_text = "向下跌破"

    distance = (price / line.level - 1) * 100

    return (
        f"📐 {title}｜Atlas 畫線\n"
        f"{icon} {direction_text} {line.label} "
        f"{format_price(line.level)}｜"
        f"現價 {format_price(price)}"
        f"（{format_signed_percent(distance, 2)}）\n"
        f"週期 {line.timeframe}・{line.description}｜"
        f"確認：連續 {CONFIRM_RUNS} 次\n"
        f"上方最近畫線 {format_neighbour(nearest_above, price)}｜"
        f"下方 {format_neighbour(nearest_below, price)}\n"
        f"{build_chart_url(symbol, line.timeframe)}"
    )


def pack_alert_messages(blocks):
    """
    回傳 [(訊息內容, [區塊索引...])]。

    3 則以內各自發送；超過 3 則合併，
    每則訊息不超過 Discord 長度限制。
    """
    if len(blocks) <= COMBINE_THRESHOLD:
        return [
            (block[:MESSAGE_MAX_LENGTH], [index])
            for index, block in enumerate(blocks)
        ]

    header = f"📐 Atlas 畫線提醒｜共 {len(blocks)} 則"
    messages = []
    current_text = header
    current_indexes = []

    for index, block in enumerate(blocks):
        candidate = f"{current_text}\n\n{block}"

        if (
            current_indexes
            and len(candidate) > MESSAGE_MAX_LENGTH
        ):
            messages.append(
                (current_text, current_indexes)
            )
            current_text = f"{header}（續）"
            current_indexes = []
            candidate = f"{current_text}\n\n{block}"

        current_text = candidate[:MESSAGE_MAX_LENGTH]
        current_indexes.append(index)

    if current_indexes:
        messages.append(
            (current_text, current_indexes)
        )

    return messages


def nearest_levels(entry, alerted_line, price):
    above = None
    below = None

    for line in entry.lines:
        if line.key == alerted_line.key:
            continue

        if abs(line.level - alerted_line.level) <= LEVEL_EPSILON:
            continue

        if line.level > price and (
            above is None or line.level < above
        ):
            above = line.level

        if line.level < price and (
            below is None or line.level > below
        ):
            below = line.level

    return above, below


def client_error_status(exc):
    status = getattr(exc, "status_code", None)

    return (
        isinstance(status, int)
        and 400 <= status < 500
    )


class DrawingAlertEngine:
    def __init__(
        self,
        store,
        notifier
    ):
        self.store = store
        self.notifier = notifier
        self.fallback_state = None

    def load_states(self, now=None):
        """
        讀取每檔的畫線狀態。

        主要來源是 breakout_alert_state 的 atlas_lines 列；
        後備來源是 chart_drawings 的 _ALERTSTATE 列。
        兩者都有時取較新者。任一讀取失敗就拋出，
        由 process 停止本次畫線提醒（寧可漏發也不重複發）。
        """
        rows = self.store.get_drawing_states()
        fallback = self.store.get_drawing_state_fallback()
        fallback_cutoff = utc_iso(
            (now or datetime.now(timezone.utc))
            - timedelta(days=FALLBACK_MAX_AGE_DAYS)
        )

        self.fallback_state = (
            fallback
            if isinstance(fallback, dict)
            and fallback.get("kind") == FALLBACK_KIND
            else None
        )

        states = {}

        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue

            metadata = row.get("metadata")

            if (
                not isinstance(metadata, dict)
                or metadata.get("kind") != STATE_KIND
                or not isinstance(metadata.get("lines"), dict)
            ):
                continue

            states[str(row.get("ticker") or "")] = {
                "lines": metadata["lines"],
                "updatedAt": str(
                    metadata.get("updatedAt") or ""
                )
            }

        fallback_tickers = (
            self.fallback_state.get("tickers")
            if self.fallback_state
            else None
        )

        if isinstance(fallback_tickers, dict):
            for ticker, record in fallback_tickers.items():
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("lines"), dict)
                ):
                    continue

                updated_at = str(
                    record.get("updatedAt") or ""
                )

                if updated_at < fallback_cutoff:
                    continue

                current = states.get(ticker)

                # 後備只在主要寫入失敗時才寫，同時間時以後備為準
                if (
                    current is None
                    or updated_at >= current["updatedAt"]
                ):
                    states[ticker] = {
                        "lines": record["lines"],
                        "updatedAt": updated_at
                    }

        return states

    def build_payload(self, plan, now_iso):
        return {
            "group_id": STATE_GROUP_ID,
            "ticker": plan.entry.ticker,
            "ma_period": STATE_MA_PERIOD,
            "market": plan.entry.market,
            "session_type": str(
                getattr(plan.decision, "session_type", "")
                or "regular"
            ),
            "previous_side": "unknown",
            "last_price": plan.price,
            "price_source": getattr(
                plan.quote,
                "source",
                None
            ),
            "last_checked_at": now_iso,
            "metadata": {
                "kind": STATE_KIND,
                "v": STATE_VERSION,
                "symbol": plan.entry.symbol,
                "updatedAt": now_iso,
                "lines": plan.lines
            }
        }

    def build_fallback_state(self, plans, now_iso, now=None):
        state = copy.deepcopy(self.fallback_state) or {}

        if not isinstance(state.get("tickers"), dict):
            state["tickers"] = {}

        # 順便移除過期的股票，後備列不會無限變大
        cutoff = utc_iso(
            (now or datetime.now(timezone.utc))
            - timedelta(days=FALLBACK_MAX_AGE_DAYS)
        )

        state["tickers"] = {
            ticker: record
            for ticker, record in state["tickers"].items()
            if isinstance(record, dict)
            and str(record.get("updatedAt") or "") >= cutoff
        }

        state["kind"] = FALLBACK_KIND
        state["v"] = STATE_VERSION
        state["updatedAt"] = now_iso

        for plan in plans:
            state["tickers"][plan.entry.ticker] = {
                "market": plan.entry.market,
                "symbol": plan.entry.symbol,
                "updatedAt": now_iso,
                "lines": plan.lines
            }

        return state

    def persist(self, plans, now_iso, now=None):
        """
        寫入狀態，回傳寫入成功的股票代號。

        主要寫入遇 4xx（例如資料表限制）改寫後備列；
        兩者都失敗的股票本次不發提醒。
        """
        saved = set()
        fallback_plans = []

        for start in range(0, len(plans), UPSERT_CHUNK_SIZE):
            chunk = plans[start:start + UPSERT_CHUNK_SIZE]

            try:
                self.store.upsert_drawing_states(
                    [
                        self.build_payload(plan, now_iso)
                        for plan in chunk
                    ]
                )

                saved.update(
                    plan.entry.ticker
                    for plan in chunk
                )

            except Exception as exc:
                if client_error_status(exc):
                    fallback_plans.extend(chunk)

                print(
                    "⚠️ 畫線狀態寫入失敗："
                    f"{type(exc).__name__}: {exc}"
                )

        if fallback_plans:
            try:
                state = self.build_fallback_state(
                    fallback_plans,
                    now_iso,
                    now
                )

                self.store.save_drawing_state_fallback(
                    state
                )

                self.fallback_state = state

                saved.update(
                    plan.entry.ticker
                    for plan in fallback_plans
                )

                print(
                    "ℹ️ 畫線狀態已改存後備位置："
                    f"{len(fallback_plans)} 檔"
                )

            except Exception as exc:
                print(
                    "❌ 畫線狀態後備寫入也失敗，"
                    "這些股票本次不發提醒："
                    f"{type(exc).__name__}: {exc}"
                )

        return saved

    def plan_entry(
        self,
        *,
        entry,
        quote,
        decision,
        record,
        now_iso,
        alert_budget,
        summary
    ):
        price = safe_price(
            getattr(quote, "price", None)
        )

        if price is None:
            return None, alert_budget

        previous_lines = (
            record["lines"]
            if record
            else {}
        )

        session_date = str(
            getattr(decision, "session_date", "")
            or ""
        )

        lines = {}
        plan = TickerPlan(
            entry=entry,
            quote=quote,
            price=price,
            decision=decision,
            previous_lines=previous_lines,
            lines=lines,
            changed=False
        )

        for line in entry.lines:
            previous = previous_lines.get(line.key)

            state, event = advance_line(
                previous,
                line,
                price,
                now_iso=now_iso,
                session_date=session_date
            )

            if event == "alert":
                if alert_budget > 0:
                    alert_budget -= 1

                    nearest_above, nearest_below = (
                        nearest_levels(entry, line, price)
                    )

                    plan.alerts.append(
                        DrawingAlert(
                            plan=plan,
                            line=line,
                            direction=state["lastAlertDirection"],
                            previous_state=copy.deepcopy(previous),
                            nearest_above=nearest_above,
                            nearest_below=nearest_below
                        )
                    )

                else:
                    # 超過每次上限：保留原狀態，下次執行再提醒
                    event = "deferred"
                    state = copy.deepcopy(previous)

            summary[event] = summary.get(event, 0) + 1

            if state is not None:
                lines[line.key] = state

        plan.changed = (
            record is None
            or previous_lines != lines
        )

        return plan, alert_budget

    def process(
        self,
        *,
        watchlist,
        quotes,
        due_decisions,
        now=None
    ):
        summary = {}
        now = now or datetime.now(timezone.utc)
        now_iso = utc_iso(now)

        entries = [
            entry
            for entry in (
                watchlist.entries
                if watchlist is not None
                else []
            )
            if entry.market in due_decisions
        ]

        if not entries:
            return summary

        try:
            states = self.load_states(now)

        except Exception as exc:
            print(
                "❌ 畫線狀態讀取失敗，本次不發畫線提醒："
                f"{type(exc).__name__}: {exc}"
            )
            summary["state_failed"] = 1

            return summary

        plans = []
        alert_budget = MAX_ALERTS_PER_RUN

        for entry in entries:
            quote = quotes.get(entry.ticker)

            if quote is None:
                summary["skipped"] = (
                    summary.get("skipped", 0) + 1
                )
                print(
                    f"⚠️ {entry.ticker}"
                    "｜無最新價，略過畫線判斷"
                )
                continue

            try:
                plan, alert_budget = self.plan_entry(
                    entry=entry,
                    quote=quote,
                    decision=due_decisions[entry.market],
                    record=states.get(entry.ticker),
                    now_iso=now_iso,
                    alert_budget=alert_budget,
                    summary=summary
                )

            except Exception as exc:
                summary["processing_failed"] = (
                    summary.get("processing_failed", 0) + 1
                )
                print(
                    f"❌ {entry.ticker}｜畫線判斷失敗："
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if plan is None:
                summary["skipped"] = (
                    summary.get("skipped", 0) + 1
                )
                continue

            plans.append(plan)

        # 先寫入「已提醒」狀態再發送：寫入失敗就不發，
        # 避免每次執行重複提醒；發送失敗再回復狀態。
        saved = self.persist(
            [plan for plan in plans if plan.changed],
            now_iso,
            now
        )

        saved.update(
            plan.entry.ticker
            for plan in plans
            if not plan.changed
        )

        alerts = []

        for plan in plans:
            if not plan.alerts:
                continue

            if plan.entry.ticker not in saved:
                summary["blocked"] = (
                    summary.get("blocked", 0)
                    + len(plan.alerts)
                )
                continue

            alerts.extend(plan.alerts)

        if not alerts:
            return summary

        blocks = [
            format_alert_block(
                ticker=alert.plan.entry.ticker,
                symbol=alert.plan.entry.symbol,
                display_name=str(
                    getattr(alert.plan.quote, "display_name", "")
                    or ""
                ).strip(),
                line=alert.line,
                price=alert.plan.price,
                direction=alert.direction,
                nearest_above=alert.nearest_above,
                nearest_below=alert.nearest_below
            )
            for alert in alerts
        ]

        failed_indexes = []

        for content, indexes in pack_alert_messages(blocks):
            try:
                sent = self.notifier.send_drawing_breakout(
                    content
                )

                if not sent:
                    raise RuntimeError("Discord 回傳失敗")

                summary["alert_sent"] = (
                    summary.get("alert_sent", 0)
                    + len(indexes)
                )

                for index in indexes:
                    alert = alerts[index]
                    print(
                        f"📐 {alert.plan.entry.ticker}"
                        f"｜{alert.line.label} "
                        f"{alert.line.level:,.2f}"
                        f"｜{alert.direction}"
                    )

            except Exception as exc:
                summary["alert_failed"] = (
                    summary.get("alert_failed", 0)
                    + len(indexes)
                )
                failed_indexes.extend(indexes)
                print(
                    "❌ Discord 畫線提醒失敗："
                    f"{type(exc).__name__}: {exc}"
                )

        if failed_indexes:
            self.revert_failed(
                [alerts[index] for index in failed_indexes],
                now_iso,
                now
            )

        return summary

    def revert_failed(self, failed_alerts, now_iso, now=None):
        """
        發送失敗的畫線回復成本次執行前的狀態，
        下次執行會重新確認並再嘗試提醒。
        """
        plans = {}

        for alert in failed_alerts:
            plan = alert.plan

            if alert.previous_state is None:
                plan.lines.pop(alert.line.key, None)
            else:
                plan.lines[alert.line.key] = copy.deepcopy(
                    alert.previous_state
                )

            plans[plan.entry.ticker] = plan

        saved = self.persist(
            list(plans.values()),
            now_iso,
            now
        )

        for ticker in plans:
            if ticker not in saved:
                print(
                    f"⚠️ {ticker}｜畫線狀態回復失敗，"
                    "此提醒將不再重送"
                )
