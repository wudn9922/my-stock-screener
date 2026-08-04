import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class BreakoutResult:
    ticker: str
    group_id: str
    ma_period: int
    previous_side: str
    current_side: str
    action: str
    direction: str | None = None
    message: str = ""

    def to_dict(self):
        return asdict(self)


def utc_now_string():
    return (
        datetime.now(timezone.utc)
        .isoformat()
    )


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


def determine_price_side(
    price,
    ma_value,
    tolerance_ratio=0.000001
):
    price = safe_float(price)
    ma_value = safe_float(ma_value)

    if price is None or ma_value is None:
        return "unknown"

    tolerance = max(
        abs(ma_value) * tolerance_ratio,
        0.000001
    )

    difference = price - ma_value

    if difference > tolerance:
        return "above"

    if difference < -tolerance:
        return "below"

    return "equal"


def determine_breakout_direction(
    previous_side,
    current_side
):
    if (
        previous_side == "below"
        and current_side == "above"
    ):
        return "breakout_up"

    if (
        previous_side == "above"
        and current_side == "below"
    ):
        return "breakout_down"

    return None


def get_persisted_side(
    previous_side,
    current_side
):
    # 價格剛好等於均線時，保留原本方向，
    # 避免 below → equal → above 漏掉突破。
    if (
        current_side == "equal"
        and previous_side in {
            "above",
            "below"
        }
    ):
        return previous_side

    return current_side


def build_state_metadata(
    *,
    config,
    quote,
    analysis,
    decision,
    current_side
):
    metadata = {
        "stock_id": config.get(
            "stock_id"
        ),
        "raw_group_id": config.get(
            "raw_group_id"
        ),
        "group_name": config.get(
            "group_name"
        ),
        "display_name": (
            quote.display_name
            or ""
        ),
        "quoted_at": quote.quoted_at,
        "price_source": quote.source,
        "current_side": current_side,
        "session_date": (
            decision.session_date
        ),
        "slot_key": decision.slot_key,
        "interval_minutes": (
            decision.interval_minutes
        )
    }

    if analysis is not None:
        metadata.update(
            {
                "volume_source": (
                    analysis.volume_source
                ),
                "current_session_date": (
                    analysis.current_session_date
                ),
                "previous_session_date": (
                    analysis.previous_session_date
                ),
                "volume_cutoff_minute": (
                    analysis.volume_cutoff_minute
                )
            }
        )

    return metadata


class BreakoutEngine:
    def __init__(
        self,
        store,
        notifier
    ):
        self.store = store
        self.notifier = notifier

    def process_config(
        self,
        *,
        config,
        quote,
        analysis,
        decision
    ):
        results = []

        ticker = config["ticker"]
        group_id = config["group_id"]
        group_name = config["group_name"]
        market = config["market"]

        existing_states = (
            self.store.get_states_for_stock(
                group_id,
                ticker
            )
        )

        state_by_ma = {
            int(state["ma_period"]): state
            for state in existing_states
            if state.get("ma_period") is not None
        }

        for ma_period in config["ma_list"]:
            ma_period = int(ma_period)

            ma_value = safe_float(
                analysis.ma_values.get(
                    str(ma_period)
                )
            )

            if ma_value is None:
                results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=group_id,
                        ma_period=ma_period,
                        previous_side="unknown",
                        current_side="unknown",
                        action="skipped",
                        message="均線資料不足"
                    )
                )

                print(
                    f"⚠️ {ticker} MA{ma_period}"
                    "｜均線資料不足"
                )

                continue

            current_side = (
                determine_price_side(
                    quote.price,
                    ma_value
                )
            )

            if current_side == "unknown":
                results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=group_id,
                        ma_period=ma_period,
                        previous_side="unknown",
                        current_side="unknown",
                        action="skipped",
                        message="價格或均線無效"
                    )
                )

                continue

            state = state_by_ma.get(
                ma_period
            )

            previous_side = (
                str(
                    state.get(
                        "previous_side"
                    )
                    or "unknown"
                )
                if state
                else "unknown"
            )

            metadata = build_state_metadata(
                config=config,
                quote=quote,
                analysis=analysis,
                decision=decision,
                current_side=current_side
            )

            common_state_values = {
                "group_id": group_id,
                "ticker": ticker,
                "ma_period": ma_period,
                "market": market,
                "session_type": (
                    decision.session_type
                ),
                "last_price": quote.price,
                "last_ma_value": ma_value,
                "current_cumulative_volume": (
                    analysis
                    .current_cumulative_volume
                ),
                "previous_cumulative_volume": (
                    analysis
                    .previous_cumulative_volume
                ),
                "volume_ratio": (
                    analysis.volume_ratio
                ),
                "price_source": quote.source,
                "last_checked_at": (
                    utc_now_string()
                ),
                "metadata": metadata
            }

            # 第一次遇到此股票與均線：
            # 只建立基準，不發送提醒。
            if (
                state is None
                or previous_side == "unknown"
            ):
                baseline_side = (
                    get_persisted_side(
                        previous_side,
                        current_side
                    )
                )

                self.store.upsert_alert_state(
                    **common_state_values,
                    previous_side=baseline_side
                )

                results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=group_id,
                        ma_period=ma_period,
                        previous_side=previous_side,
                        current_side=current_side,
                        action="initialized",
                        message="已建立基準狀態"
                    )
                )

                print(
                    f"🆕 {ticker} MA{ma_period}"
                    f"｜基準={baseline_side}"
                    f"｜現價={quote.price:,.2f}"
                    f"｜均線={ma_value:,.2f}"
                )

                continue

            direction = (
                determine_breakout_direction(
                    previous_side,
                    current_side
                )
            )

            # 沒有發生跨越，只更新檢查資料。
            if direction is None:
                persisted_side = (
                    get_persisted_side(
                        previous_side,
                        current_side
                    )
                )

                self.store.upsert_alert_state(
                    **common_state_values,
                    previous_side=persisted_side
                )

                results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=group_id,
                        ma_period=ma_period,
                        previous_side=previous_side,
                        current_side=current_side,
                        action="unchanged",
                        message="未發生突破"
                    )
                )

                print(
                    f"ℹ️ {ticker} MA{ma_period}"
                    f"｜{previous_side}"
                    f"→{current_side}"
                    "｜無提醒"
                )

                continue

            display_name = (
                quote.display_name
                or ""
            )

            try:
                sent = self.notifier.send_breakout(
                    ticker=ticker,
                    display_name=display_name,
                    group_name=group_name,
                    ma_period=ma_period,
                    ma_value=ma_value,
                    price=quote.price,
                    volume_ratio=(
                        analysis.volume_ratio
                    ),
                    direction=direction
                )

            except Exception as exc:
                # Discord 失敗時不更新 previous_side，
                # 下次執行仍會重新嘗試提醒。
                results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=group_id,
                        ma_period=ma_period,
                        previous_side=previous_side,
                        current_side=current_side,
                        action="alert_failed",
                        direction=direction,
                        message=(
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    )
                )

                print(
                    f"❌ {ticker} MA{ma_period}"
                    "｜Discord 提醒失敗"
                    f"｜{type(exc).__name__}: {exc}"
                )

                continue

            if not sent:
                results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=group_id,
                        ma_period=ma_period,
                        previous_side=previous_side,
                        current_side=current_side,
                        action="alert_failed",
                        direction=direction,
                        message="Discord 回傳失敗"
                    )
                )

                continue

            alert_time = utc_now_string()

            self.store.upsert_alert_state(
                **common_state_values,
                previous_side=current_side,
                last_alert_at=alert_time,
                last_alert_direction=direction,
                last_alert_price=quote.price,
                last_alert_ma_value=ma_value,
                last_alert_volume_ratio=(
                    analysis.volume_ratio
                )
            )

            results.append(
                BreakoutResult(
                    ticker=ticker,
                    group_id=group_id,
                    ma_period=ma_period,
                    previous_side=previous_side,
                    current_side=current_side,
                    action="alert_sent",
                    direction=direction,
                    message="Discord 提醒成功"
                )
            )

            print(
                f"🚨 {ticker} MA{ma_period}"
                f"｜{previous_side}"
                f"→{current_side}"
                f"｜{direction}"
            )

        # 正常盤且量比有效時，另外保存最近一次
        # 正常盤快取。盤前及盤後不會執行此區塊，
        # 因此不會把正常盤量比覆蓋成空值。
        if (
            decision.session_type == "regular"
            and analysis.volume_ratio is not None
        ):
            try:
                self.store.update_regular_volume_cache(
                    group_id=group_id,
                    ticker=ticker,
                    current_cumulative_volume=(
                        analysis
                        .current_cumulative_volume
                    ),
                    previous_cumulative_volume=(
                        analysis
                        .previous_cumulative_volume
                    ),
                    volume_ratio=(
                        analysis.volume_ratio
                    ),
                    checked_at=utc_now_string()
                )

                print(
                    f"📊 {ticker}"
                    f"｜正常盤量比快取 "
                    f"{analysis.volume_ratio:.2f}"
                )

            except Exception as exc:
                # 量比快取失敗不影響突破狀態及其他股票。
                print(
                    f"⚠️ {ticker}"
                    "｜正常盤量比快取失敗："
                    f"{type(exc).__name__}: {exc}"
                )

        return results

    def process_all(
        self,
        *,
        monitor_configs,
        quotes,
        analytics,
        due_decisions
    ):
        all_results = []

        for config in monitor_configs:
            ticker = config["ticker"]
            market = config["market"]

            decision = due_decisions.get(
                market
            )

            if decision is None:
                continue

            quote = quotes.get(ticker)
            analysis = analytics.get(ticker)

            if quote is None:
                print(
                    f"⚠️ {ticker}"
                    "｜無最新價，略過突破判斷"
                )

                continue

            if analysis is None:
                print(
                    f"⚠️ {ticker}"
                    "｜無均線分析，略過突破判斷"
                )

                continue

            try:
                config_results = (
                    self.process_config(
                        config=config,
                        quote=quote,
                        analysis=analysis,
                        decision=decision
                    )
                )

                all_results.extend(
                    config_results
                )

            except Exception as exc:
                print(
                    f"❌ {ticker}"
                    f"｜{config['group_id']}"
                    "｜突破處理失敗："
                    f"{type(exc).__name__}: {exc}"
                )

                all_results.append(
                    BreakoutResult(
                        ticker=ticker,
                        group_id=(
                            config["group_id"]
                        ),
                        ma_period=0,
                        previous_side="unknown",
                        current_side="unknown",
                        action="processing_failed",
                        message=(
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    )
                )

        return all_results
