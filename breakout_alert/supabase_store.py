from datetime import datetime, timezone

import requests

from breakout_alert.config import (
    get_supabase_rest_url,
    get_supabase_service_key,
    get_supabase_user_id,
)


GROUP_NAME_TO_KEY = {
    "台股-權值精選": "tw_g1",
    "台股－權值精選": "tw_g1",
    "台股-熱門": "tw_g2",
    "台股－熱門": "tw_g2",
    "美股-權值精選": "us_g1",
    "美股－權值精選": "us_g1",
    "美股-低本益比": "us_g2",
    "美股－低本益比": "us_g2",
    "美股-超級績效": "us_g3",
    "美股－超級績效": "us_g3",
    "美股-熱門": "us_g4",
    "美股－熱門": "us_g4"
}

GROUP_DISPLAY_NAMES = {
    "tw_g1": "台股權值精選",
    "tw_g2": "台股熱門",
    "us_g1": "美股權值精選",
    "us_g2": "美股低本益比",
    "us_g3": "美股超級績效",
    "us_g4": "美股熱門"
}

VALID_GROUP_KEYS = set(
    GROUP_DISPLAY_NAMES
)


def utc_now_string():
    return (
        datetime.now(timezone.utc)
        .isoformat()
    )


def safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default

        return int(value)

    except (TypeError, ValueError):
        return default


def normalize_group_name(value):
    return (
        str(value or "")
        .strip()
        .replace("－", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", "")
    )


def normalize_ticker(ticker, market):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if market == "US":
        return ticker.replace(".", "-")

    return ticker


def detect_market(
    group_id,
    group_name="",
    ticker=""
):
    group_id = str(
        group_id or ""
    ).strip().lower()

    group_name = (
        str(group_name or "")
        .strip()
        .replace("－", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", "")
        .lower()
    )

    ticker = str(
        ticker or ""
    ).strip().upper()

    # 1. 先判斷固定群組 ID
    if group_id.startswith("tw_"):
        return "TW"

    if group_id.startswith("us_"):
        return "US"

    # 2. 再依群組名稱判斷
    tw_keywords = [
        "台股",
        "台灣",
        "taiwan",
        "twstock",
        "tw-stock"
    ]

    us_keywords = [
        "美股",
        "美國",
        "usa",
        "usstock",
        "us-stock"
    ]

    if any(
        keyword in group_name
        for keyword in tw_keywords
    ):
        return "TW"

    if any(
        keyword in group_name
        for keyword in us_keywords
    ):
        return "US"

    # 3. 依股票代號後綴判斷
    if (
        ticker.endswith(".TW")
        or ticker.endswith(".TWO")
    ):
        return "TW"

    # 4. 台股可能只儲存純數字代號
    if ticker.isdigit():
        return "TW"

    # 5. 一般英文字母或美股格式視為美股
    if ticker:
        return "US"

    return None


def get_market_from_group_key(group_key):
    # 保留舊函式，避免其他程式引用時失效
    return detect_market(group_key)


def get_ma_list(stock):
    ma_list = []

    for column in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_period = safe_int(
            stock.get(column)
        )

        if (
            ma_period is not None
            and ma_period > 0
        ):
            ma_list.append(ma_period)

    return sorted(set(ma_list))


class SupabaseStore:
    def __init__(self):
        self.rest_url = (
            get_supabase_rest_url()
        )

        self.service_key = (
            get_supabase_service_key()
        )

        self.user_id = (
            get_supabase_user_id()
        )

        self.session = requests.Session()

        headers = {
            "apikey": self.service_key,
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        # Legacy service_role 是 JWT，可以放入 Bearer。
        # 新版 sb_secret_ 金鑰只放 apikey，不可當成 JWT。
        if self.service_key.startswith("eyJ"):
            headers["Authorization"] = (
                f"Bearer {self.service_key}"
            )

        self.session.headers.update(headers)



    def _request(
        self,
        method,
        table_name,
        *,
        params=None,
        json_body=None,
        extra_headers=None
    ):
        headers = {}

        if extra_headers:
            headers.update(extra_headers)

        response = self.session.request(
            method=method,
            url=(
                f"{self.rest_url}/"
                f"{table_name}"
            ),
            params=params,
            json=json_body,
            headers=headers,
            timeout=25
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase {table_name} "
                f"{method.upper()} 失敗，"
                f"HTTP {response.status_code}："
                f"{response.text[:500]}"
            )

        if response.status_code == 204:
            return None

        if not response.text.strip():
            return None

        try:
            return response.json()

        except ValueError as exc:
            raise RuntimeError(
                f"Supabase {table_name} "
                "回傳非 JSON 格式"
            ) from exc

    def get_groups(self):
        rows = self._request(
            "GET",
            "groups",
            params={
                "select": "*",
                "order": "id.asc"
            }
        )

        return rows if isinstance(
            rows,
            list
        ) else []

    def get_user_stocks(self):
        rows = self._request(
            "GET",
            "stocks",
            params={
                "select": (
                    "id,group_id,ticker,"
                    "ma1,ma2,ma3,ma4,"
                    "line_user_id"
                ),
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "order": "id.asc"
            }
        )

        return rows if isinstance(
            rows,
            list
        ) else []


    def build_group_mapping(self, groups):
        group_mapping = {}

        for group in groups:
            raw_group_id = str(
                group.get("id") or ""
            ).strip()

            raw_group_name = str(
                group.get("name") or ""
            ).strip()

            if not raw_group_id:
                continue

            normalized_name = (
                normalize_group_name(
                    raw_group_name
                )
            )

            mapped_fixed_key = None

            for known_name, group_key in (
                GROUP_NAME_TO_KEY.items()
            ):
                if (
                    normalize_group_name(
                        known_name
                    )
                    == normalized_name
                ):
                    mapped_fixed_key = group_key
                    break

            # groups.id 本身可能就是 tw_g1、us_g1
            if (
                not mapped_fixed_key
                and raw_group_id
                in VALID_GROUP_KEYS
            ):
                mapped_fixed_key = raw_group_id

            # 固定群組使用標準代號；
            # 動態群組保留 custom_22 等原始 ID。
            effective_group_id = (
                mapped_fixed_key
                or raw_group_id
            )

            market = detect_market(
                effective_group_id,
                raw_group_name
            )

            default_ma_list = get_ma_list(
                group
            )

            group_mapping[raw_group_id] = {
                "group_id": effective_group_id,
                "raw_group_id": raw_group_id,
                "group_name": (
                    raw_group_name
                    or GROUP_DISPLAY_NAMES.get(
                        effective_group_id
                    )
                    or raw_group_id
                ),
                "market": market,
                "default_ma_list": (
                    default_ma_list
                ),
                "is_dynamic": (
                    effective_group_id
                    not in VALID_GROUP_KEYS
                )
            }

            # 支援 stocks.group_id 直接存標準群組代號，
            # 但 groups.id 是數字的情況。
            if mapped_fixed_key:
                group_mapping.setdefault(
                    mapped_fixed_key,
                    {
                        "group_id": (
                            mapped_fixed_key
                        ),
                        "raw_group_id": (
                            raw_group_id
                        ),
                        "group_name": (
                            raw_group_name
                            or GROUP_DISPLAY_NAMES.get(
                                mapped_fixed_key
                            )
                            or mapped_fixed_key
                        ),
                        "market": (
                            detect_market(
                                mapped_fixed_key,
                                raw_group_name
                            )
                        ),
                        "default_ma_list": (
                            default_ma_list
                        ),
                        "is_dynamic": False
                    }
                )

        return group_mapping

    def get_monitor_configs(self):
        groups = self.get_groups()
        stocks = self.get_user_stocks()

        group_mapping = (
            self.build_group_mapping(
                groups
            )
        )

        monitor_configs = []
        deduplicate_keys = set()

        for stock in stocks:
            raw_group_id = str(
                stock.get("group_id") or ""
            ).strip()

            raw_ticker = str(
                stock.get("ticker") or ""
            ).strip().upper()

            if not raw_group_id:
                print(
                    "⚠️ 略過 group_id 為空的股票："
                    f"{raw_ticker or '未知代號'}"
                )
                continue

            if raw_group_id == "admin_index":
                continue

            group_info = group_mapping.get(
                raw_group_id
            )

            # 固定群組即使 groups 表沒有對應資料，
            # 仍可直接辨識。
            if group_info is None and (
                raw_group_id in VALID_GROUP_KEYS
            ):
                group_info = {
                    "group_id": raw_group_id,
                    "raw_group_id": raw_group_id,
                    "group_name": (
                        GROUP_DISPLAY_NAMES.get(
                            raw_group_id,
                            raw_group_id
                        )
                    ),
                    "market": detect_market(
                        raw_group_id,
                        "",
                        raw_ticker
                    ),
                    "default_ma_list": [],
                    "is_dynamic": False
                }

            # 動態群組若 groups 資料暫時缺少，
            # 仍依 ticker 判斷市場並保留原群組 ID。
            if group_info is None:
                inferred_market = detect_market(
                    raw_group_id,
                    "",
                    raw_ticker
                )

                if inferred_market:
                    group_info = {
                        "group_id": raw_group_id,
                        "raw_group_id": raw_group_id,
                        "group_name": raw_group_id,
                        "market": inferred_market,
                        "default_ma_list": [],
                        "is_dynamic": True
                    }

            if group_info is None:
                print(
                    "⚠️ 略過無法辨識的群組："
                    f"group_id={raw_group_id}"
                )
                continue

            effective_group_id = str(
                group_info.get("group_id")
                or raw_group_id
            ).strip()

            group_name = str(
                group_info.get("group_name")
                or effective_group_id
            ).strip()

            market = (
                group_info.get("market")
                or detect_market(
                    effective_group_id,
                    group_name,
                    raw_ticker
                )
            )

            if market not in {"TW", "US"}:
                print(
                    "⚠️ 略過無法辨識市場："
                    f"group_id={raw_group_id}, "
                    f"ticker={raw_ticker}"
                )
                continue

            ticker = normalize_ticker(
                raw_ticker,
                market
            )

            if not ticker:
                print(
                    "⚠️ 略過 ticker 為空的股票"
                )
                continue

            # 優先使用 stocks 個別均線；
            # 若沒有有效均線，才使用 groups 預設均線。
            ma_list = get_ma_list(
                stock
            )

            if not ma_list:
                ma_list = sorted(
                    set(
                        group_info.get(
                            "default_ma_list"
                        )
                        or []
                    )
                )

            if not ma_list:
                print(
                    f"⚠️ 略過無有效均線："
                    f"{ticker} / "
                    f"{effective_group_id}"
                )
                continue

            unique_key = (
                self.user_id,
                effective_group_id,
                ticker
            )

            if unique_key in deduplicate_keys:
                print(
                    f"⚠️ 略過重複監控設定："
                    f"{effective_group_id}/"
                    f"{ticker}"
                )
                continue

            deduplicate_keys.add(
                unique_key
            )

            monitor_configs.append(
                {
                    "stock_id": stock.get("id"),
                    "line_user_id": self.user_id,
                    "group_id": (
                        effective_group_id
                    ),
                    "raw_group_id": (
                        raw_group_id
                    ),
                    "group_name": group_name,
                    "ticker": ticker,
                    "market": market,
                    "ma_list": ma_list,
                    "is_dynamic_group": bool(
                        group_info.get(
                            "is_dynamic"
                        )
                    )
                }
            )

            if group_info.get("is_dynamic"):
                print(
                    f"✅ 載入動態群組："
                    f"{effective_group_id}"
                    f"｜{group_name}"
                    f"｜{ticker}"
                    f"｜{market}"
                    f"｜MA={ma_list}"
                )

        return monitor_configs

    def get_alert_state(
        self,
        group_id,
        ticker,
        ma_period
    ):
        rows = self._request(
            "GET",
            "breakout_alert_state",
            params={
                "select": "*",
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "group_id": (
                    f"eq.{group_id}"
                ),
                "ticker": (
                    f"eq.{ticker}"
                ),
                "ma_period": (
                    f"eq.{int(ma_period)}"
                ),
                "limit": "1"
            }
        )

        if (
            isinstance(rows, list)
            and rows
        ):
            return rows[0]

        return None

    def get_states_for_stock(
        self,
        group_id,
        ticker
    ):
        rows = self._request(
            "GET",
            "breakout_alert_state",
            params={
                "select": "*",
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "group_id": (
                    f"eq.{group_id}"
                ),
                "ticker": (
                    f"eq.{ticker}"
                ),
                "order": "ma_period.asc"
            }
        )

        return rows if isinstance(
            rows,
            list
        ) else []

    def upsert_alert_state(
        self,
        *,
        group_id,
        ticker,
        ma_period,
        market,
        session_type,
        previous_side,
        last_price=None,
        last_ma_value=None,
        current_cumulative_volume=None,
        previous_cumulative_volume=None,
        volume_ratio=None,
        price_source=None,
        last_checked_at=None,
        last_alert_at=None,
        last_alert_direction=None,
        last_alert_price=None,
        last_alert_ma_value=None,
        last_alert_volume_ratio=None,
        metadata=None
    ):
        payload = {
            "line_user_id": self.user_id,
            "group_id": str(group_id),
            "ticker": str(ticker),
            "ma_period": int(ma_period),
            "market": str(market),
            "session_type": str(session_type),
            "previous_side": str(previous_side),
            "last_price": last_price,
            "last_ma_value": last_ma_value,
            "current_cumulative_volume": (
                current_cumulative_volume
            ),
            "previous_cumulative_volume": (
                previous_cumulative_volume
            ),
            "volume_ratio": volume_ratio,
            "price_source": price_source,
            "last_checked_at": (
                last_checked_at
                or utc_now_string()
            ),
            "metadata": metadata or {}
        }

        optional_fields = {
            "last_alert_at": last_alert_at,
            "last_alert_direction": (
                last_alert_direction
            ),
            "last_alert_price": (
                last_alert_price
            ),
            "last_alert_ma_value": (
                last_alert_ma_value
            ),
            "last_alert_volume_ratio": (
                last_alert_volume_ratio
            )
        }

        for key, value in optional_fields.items():
            if value is not None:
                payload[key] = value

        rows = self._request(
            "POST",
            "breakout_alert_state",
            params={
                "on_conflict": (
                    "line_user_id,"
                    "group_id,"
                    "ticker,"
                    "ma_period"
                )
            },
            json_body=payload,
            extra_headers={
                "Prefer": (
                    "resolution=merge-duplicates,"
                    "return=representation"
                )
            }
        )

        if (
            isinstance(rows, list)
            and rows
        ):
            return rows[0]

        return None

    def close(self):
        self.session.close()
