from datetime import datetime, timezone

import requests
import os
import re

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
FIXED_STOCK_GROUP_ID = "ungrouped"

# 大盤指數（index_configs 表）的均線提醒使用固定群組。
# 目前監控時段只有台股與美股，其他市場的指數（歐洲、日韓）略過。
ADMIN_INDEX_GROUP_ID = "admin_index"
ADMIN_INDEX_GROUP_NAME = "大盤指數"
# 大盤指數的確認門檻：突破 0.3%、跌破 0.5%（股票為 1%／1.5%）。
# 放在這裡而不是 breakout_engine：Discord 查詢服務的映像
# 只複製 supabase_store 等少數檔案，不含 breakout_engine。
INDEX_BREAKOUT_UP_RATIO = 0.003
INDEX_BREAKOUT_DOWN_RATIO = 0.005
INDEX_MARKETS = {
    "^TWII": "TW",
    "^TWOII": "TW",
    "^GSPC": "US",
    "^DJI": "US",
    "^IXIC": "US",
    "^RUT": "US",
    "^SOX": "US"
}

# Atlas 畫線警示的狀態列使用 atlas_ 開頭的 group_id，
# 均線孤兒清理與 Discord /量比 都必須排除這些列。
ATLAS_STATE_GROUP_PREFIX = "atlas_"
ATLAS_LINES_GROUP_ID = "atlas_lines"

# 狀態表寫入遭拒（4xx）時的後備位置：
# chart_drawings 的一列；Atlas 的 atlas.list 只讀 timeframe='atlas'。
DRAWING_STATE_FALLBACK_TICKER = "_ALERTSTATE"
DRAWING_STATE_FALLBACK_TIMEFRAME = "alert-state"


class SupabaseRequestError(RuntimeError):
    """
    Supabase 回傳 HTTP 錯誤。

    繼承 RuntimeError，既有的錯誤處理與訊息不變；
    另外保留 status_code 供呼叫端區分 4xx 與 5xx。
    """

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def is_atlas_state_row(row):
    return str(
        (row or {}).get("group_id") or ""
    ).startswith(ATLAS_STATE_GROUP_PREFIX)


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


def parameter_ticker_key(ticker):
    ticker = str(ticker or "").strip().upper()
    if ticker.isdigit():
        return f"{ticker}.TW"
    return ticker if ticker.endswith((".TW", ".TWO")) else ticker.replace(".", "-")


def fixed_parameter_map(stocks, user_id):
    """Dormant preferences do not confer membership or notification consent."""
    result = {}
    for stock in stocks:
        if stock.get("line_user_id") != user_id or stock.get("group_id") != FIXED_STOCK_GROUP_ID:
            continue
        ticker = parameter_ticker_key(stock.get("ticker"))
        periods = get_ma_list(stock)
        if ticker and periods and len(periods) <= 4 and all(1 <= period <= 1000 for period in periods):
            result[ticker] = periods
    return result


class SupabaseStore:
    # index_configs 本次是否成功載入；
    # 失敗時不能把大盤指數的狀態列當成孤兒清掉。
    index_configs_loaded = False
    watchlists_loaded = False

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
            raise SupabaseRequestError(
                f"Supabase {table_name} "
                f"{method.upper()} 失敗，"
                f"HTTP {response.status_code}："
                f"{response.text[:500]}",
                status_code=response.status_code
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

                        # 動態群組的 stocks.group_id 可能儲存為 custom_22，
            # 而 groups.id 儲存為 22，因此額外建立別名。
            if raw_group_id.isdigit():
                dynamic_group_id = (
                    f"custom_{raw_group_id}"
                )

                group_mapping[
                    dynamic_group_id
                ] = {
                    "group_id": dynamic_group_id,
                    "raw_group_id": raw_group_id,
                    "group_name": (
                        raw_group_name
                        or dynamic_group_id
                    ),
                    "market": detect_market(
                        dynamic_group_id,
                        raw_group_name
                    ),
                    "default_ma_list": (
                        default_ma_list
                    ),
                    "is_dynamic": True
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

    def get_index_configs(self):
        rows = self._request(
            "GET",
            "index_configs",
            params={
                "select": (
                    "ticker,name,ma1,ma2,ma3,ma4"
                ),
                "order": "ticker.asc"
            }
        )

        # 空回應（None）不能當成「沒有指數」，
        # 否則會把既有指數狀態列全部當成孤兒。
        if not isinstance(rows, list):
            raise RuntimeError(
                "index_configs 回傳非清單格式"
            )

        return rows

    def get_index_monitor_configs(self):
        """
        把 index_configs 中有均線參數的大盤指數
        轉成監控設定。

        載入失敗時回傳空清單並保留既有狀態列
        （index_configs_loaded 維持 False）。
        """
        self.index_configs_loaded = False

        try:
            rows = self.get_index_configs()

        except Exception as exc:
            print(
                "⚠️ 大盤指數設定載入失敗，"
                "本次略過指數提醒："
                f"{type(exc).__name__}: {exc}"
            )

            return []

        self.index_configs_loaded = True

        configs = []
        skipped_markets = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            ticker = str(
                row.get("ticker") or ""
            ).strip().upper()

            if not ticker:
                continue

            ma_list = get_ma_list(row)

            if not ma_list:
                continue

            market = INDEX_MARKETS.get(ticker)

            if market is None:
                skipped_markets.append(ticker)
                continue

            configs.append(
                {
                    "stock_id": None,
                    "line_user_id": self.user_id,
                    "group_id": ADMIN_INDEX_GROUP_ID,
                    "raw_group_id": (
                        ADMIN_INDEX_GROUP_ID
                    ),
                    "group_name": (
                        ADMIN_INDEX_GROUP_NAME
                    ),
                    "ticker": ticker,
                    "market": market,
                    "ma_list": ma_list,
                    "is_dynamic_group": False,
                    "display_name": str(
                        row.get("name") or ""
                    ).strip(),
                    "breakout_up_ratio": (
                        INDEX_BREAKOUT_UP_RATIO
                    ),
                    "breakout_down_ratio": (
                        INDEX_BREAKOUT_DOWN_RATIO
                    )
                }
            )

        if skipped_markets:
            print(
                "ℹ️ 略過尚未支援交易時段的指數："
                + "、".join(skipped_markets)
            )

        print(
            f"📊 載入大盤指數監控設定：{len(configs)} 檔"
        )

        return configs

    def get_watchlist_monitor_configs(self, fixed_parameters=None):
        """Opt-in Atlas lists reuse the existing MA breakout engine/notifier.

        Only the configured user's synced lists are read. Malformed or stale
        snapshots never authorize deleting a previous alert baseline.
        """
        self.watchlists_loaded = False
        if os.environ.get("ATLAS_WATCHLIST_SYNC_ENABLED") != "1":
            return []
        try:
            rows = self._request("GET", "atlas_watchlists", params={
                "select": "id,line_user_id,definition,updated_at",
                "line_user_id": f"eq.{self.user_id}",
            })
            if not isinstance(rows, list):
                raise ValueError("watchlists response must be a list")
            configs = []
            complete = True
            for row in rows:
                if not isinstance(row, dict) or row.get("line_user_id") != self.user_id:
                    complete = False
                    continue
                definition = row.get("definition")
                if not isinstance(definition, dict):
                    complete = False
                    continue
                if definition.get("monitorBreakouts") is False:
                    continue
                if definition.get("monitorBreakouts") is not True:
                    complete = False
                    continue
                list_id = str(row.get("id") or "")
                market = definition.get("market")
                periods = definition.get("maList")
                if (not re.fullmatch(r"[0-9a-fA-F-]{36}", list_id)
                        or definition.get("id") != list_id
                        or market not in {"TW", "US"}
                        or not isinstance(periods, list)
                        or not 1 <= len(periods) <= 4
                        or any(type(period) is not int or not 1 <= period <= 1000 for period in periods)
                        or len(set(periods)) != len(periods)):
                    complete = False
                    continue
                if definition.get("kind") == "manual":
                    symbols = definition.get("symbols")
                    if not isinstance(symbols, list):
                        complete = False
                        continue
                    members = [{"symbol": ticker} for ticker in symbols]
                elif definition.get("kind") == "rules":
                    history = definition.get("history") or []
                    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
                        complete = False
                        continue
                    latest = history[-1]
                    try:
                        data_day = datetime.strptime(str(latest.get("date")), "%Y-%m-%d").date()
                        age = (datetime.now(timezone.utc).date() - data_day).days
                    except (TypeError, ValueError):
                        complete = False
                        continue
                    if age < -1 or age > 7:
                        complete = False
                        continue
                    members = latest.get("members")
                else:
                    complete = False
                    continue
                if not isinstance(members, list):
                    complete = False
                    continue
                seen = set()
                for member in members:
                    ticker = member.get("symbol") if isinstance(member, dict) else None
                    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=-]{0,31}", ticker):
                        complete = False
                        continue
                    if (market == "TW") != bool(re.search(r"\.(TW|TWO)$", ticker)):
                        complete = False
                        continue
                    if ticker in seen:
                        continue
                    seen.add(ticker)
                    configs.append({
                        "stock_id": None, "line_user_id": self.user_id,
                        "group_id": f"watchlist_{list_id}", "raw_group_id": f"watchlist_{list_id}",
                        "group_name": str(definition.get("name") or "Atlas 自選清單"),
                        "ticker": normalize_ticker(ticker, market), "market": market,
                        "ma_list": (fixed_parameters or {}).get(parameter_ticker_key(ticker), sorted(periods)), "is_dynamic_group": True,
                    })
            self.watchlists_loaded = complete
            return configs
        except Exception as exc:
            print(f"⚠️ Atlas 清單設定未就緒，本次保留狀態並略過清單提醒：{type(exc).__name__}")
            return []

    def get_monitor_configs(self):
        groups = self.get_groups()
        stocks = self.get_user_stocks()
        fixed_parameters = fixed_parameter_map(stocks, self.user_id)

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

            if raw_group_id in {"admin_index", FIXED_STOCK_GROUP_ID}:
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
            ma_list = fixed_parameters.get(parameter_ticker_key(ticker)) or get_ma_list(stock)

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

        monitor_configs.extend(self.get_watchlist_monitor_configs(fixed_parameters))
        monitor_configs.extend(
            self.get_index_monitor_configs()
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

    def update_regular_volume_cache(
        self,
        *,
        group_id,
        ticker,
        current_cumulative_volume,
        previous_cumulative_volume,
        volume_ratio,
        checked_at=None
    ):
        """
        更新最近一次正常盤量比。

        只在正常盤且量比有效時呼叫，
        因此盤前、盤後不會覆蓋既有快取。
        """
        try:
            valid_volume_ratio = float(
                volume_ratio
            )
        except (TypeError, ValueError):
            return None

        if (
            valid_volume_ratio <= 0
            or current_cumulative_volume is None
            or previous_cumulative_volume is None
            or int(current_cumulative_volume) <= 0
            or int(previous_cumulative_volume) <= 0
        ):
            return None

        checked_at = (
            checked_at
            or utc_now_string()
        )

        payload = {
            "last_regular_current_cumulative_volume": (
                current_cumulative_volume
            ),
            "last_regular_previous_cumulative_volume": (
                previous_cumulative_volume
            ),
            "last_regular_volume_ratio": (
                valid_volume_ratio
            ),
            "last_regular_checked_at": checked_at
        }

        return self._request(
            "PATCH",
            "breakout_alert_state",
            params={
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "group_id": (
                    f"eq.{group_id}"
                ),
                "ticker": (
                    f"eq.{ticker}"
                )
            },
            json_body=payload,
            extra_headers={
                "Prefer": "return=minimal"
            }
        )

    def get_cached_ticker_states(
        self,
        ticker
    ):
        """
        供 Discord /量比 查詢使用。

        此方法只讀取 Supabase 快取，
        不呼叫 Yahoo 或 TWSE MIS。
        """
        ticker = str(
            ticker or ""
        ).strip().upper()

        if not ticker:
            return []

        rows = self._request(
            "GET",
            "breakout_alert_state",
            params={
                "select": (
                    "group_id,ticker,ma_period,"
                    "market,session_type,"
                    "previous_side,last_price,"
                    "last_ma_value,price_source,"
                    "last_checked_at,"
                    "last_regular_current_cumulative_volume,"
                    "last_regular_previous_cumulative_volume,"
                    "last_regular_volume_ratio,"
                    "last_regular_checked_at,"
                    "metadata"
                ),
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "ticker": f"eq.{ticker}",
                # Atlas 畫線狀態列不是均線快取
                "group_id": (
                    f"not.like.{ATLAS_STATE_GROUP_PREFIX}*"
                ),
                "order": (
                    "group_id.asc,"
                    "ma_period.asc"
                )
            }
        )

        if not isinstance(rows, list):
            return []

        return [
            row
            for row in rows
            if isinstance(row, dict)
            and not is_atlas_state_row(row)
        ]


    def prune_orphan_states(
        self,
        monitor_configs,
        max_delete_ratio=0.5
    ):
        """
        刪除已不在監控設定內的狀態列。

        在設定頁刪除股票、移除群組，或修改均線天數後，
        舊的 (group_id, ticker, ma_period) 狀態列不會再被
        突破判斷使用，卻仍會被量比快取更新，也會出現在
        Discord /量比 查詢中，並讓資料表持續變大。

        monitor_configs 必須是「全部市場」的設定，
        不能只傳入本次到執行時間的市場。
        """
        def build_keys(configs):
            return {
                (
                    str(config["group_id"]),
                    str(config["ticker"]),
                    int(ma_period)
                )
                for config in configs
                for ma_period in config["ma_list"]
            }

        stock_configs = [
            config
            for config in monitor_configs
            if config["group_id"]
            != ADMIN_INDEX_GROUP_ID
        ]

        index_configs = [
            config
            for config in monitor_configs
            if config["group_id"]
            == ADMIN_INDEX_GROUP_ID
        ]

        stock_keys = build_keys(stock_configs)
        index_keys = build_keys(index_configs)

        # 股票設定載入異常（空）時不要把整張表清空。
        # 大盤指數的設定不能掩蓋這個保護。
        if not stock_keys:
            return 0

        rows = self._request(
            "GET",
            "breakout_alert_state",
            params={
                "select": (
                    "id,group_id,ticker,ma_period"
                ),
                "line_user_id": (
                    f"eq.{self.user_id}"
                )
            }
        )

        if not isinstance(rows, list):
            return 0

        # Atlas 畫線狀態列由 prune_drawing_states 管理，
        # 不列入均線孤兒判斷與刪除比例。
        rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and not is_atlas_state_row(row)
        ]

        index_rows = [
            row
            for row in rows
            if row.get("group_id")
            == ADMIN_INDEX_GROUP_ID
        ]

        stock_rows = [
            row
            for row in rows
            if row.get("group_id")
            != ADMIN_INDEX_GROUP_ID
            and not str(row.get("group_id") or "").startswith("watchlist_")
        ]

        deleted = self._delete_orphan_rows(
            stock_rows,
            stock_keys,
            max_delete_ratio,
            "股票"
        )

        if self.watchlists_loaded:
            watchlist_rows = [row for row in rows if str(row.get("group_id") or "").startswith("watchlist_")]
            deleted += self._delete_orphan_rows(watchlist_rows, stock_keys, max_delete_ratio, "Atlas 清單")

        # 大盤指數設定本次沒載入成功時，
        # 不能把它們的狀態列視為孤兒。
        # 股票與指數各自計算刪除比例，互不稀釋。
        if self.index_configs_loaded:
            deleted += self._delete_orphan_rows(
                index_rows,
                index_keys,
                max_delete_ratio,
                "大盤指數"
            )

        return deleted

    def _delete_orphan_rows(
        self,
        rows,
        valid_keys,
        max_delete_ratio,
        label
    ):
        if not rows:
            return 0

        orphan_ids = []

        for row in rows:
            try:
                row_key = (
                    str(row["group_id"]),
                    str(row["ticker"]),
                    int(row["ma_period"])
                )
            except (KeyError, TypeError, ValueError):
                continue

            if row_key not in valid_keys:
                orphan_ids.append(row["id"])

        if not orphan_ids:
            return 0

        if len(orphan_ids) > len(rows) * max_delete_ratio:
            print(
                f"⚠️ {label}孤兒狀態列數量異常"
                f"（{len(orphan_ids)}/{len(rows)}），"
                "略過清理"
            )
            return 0

        id_list = ",".join(
            str(row_id)
            for row_id in orphan_ids
        )

        self._request(
            "DELETE",
            "breakout_alert_state",
            params={
                "id": f"in.({id_list})",
                "line_user_id": (
                    f"eq.{self.user_id}"
                )
            },
            extra_headers={
                "Prefer": "return=minimal"
            }
        )

        print(
            f"🧹 已清除孤兒狀態列（{label}）："
            f"{len(orphan_ids)} 筆"
        )

        return len(orphan_ids)

    def get_atlas_drawings(self):
        """
        讀取 Atlas 的畫線（每檔股票一列，timeframe='atlas'）。
        """
        rows = self._request(
            "GET",
            "chart_drawings",
            params={
                "select": (
                    "ticker,drawings,updated_at"
                ),
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "timeframe": "eq.atlas",
                "limit": "2000"
            }
        )

        return rows if isinstance(
            rows,
            list
        ) else []

    def get_drawing_states(self):
        rows = self._request(
            "GET",
            "breakout_alert_state",
            params={
                "select": (
                    "id,ticker,market,metadata,"
                    "last_checked_at"
                ),
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "group_id": (
                    f"eq.{ATLAS_LINES_GROUP_ID}"
                )
            }
        )

        return rows if isinstance(
            rows,
            list
        ) else []

    def upsert_drawing_states(self, payloads):
        """
        一次寫入多檔的畫線狀態列。

        payload 由 drawing_alerts 建立；
        line_user_id 一律由這裡填入。
        """
        rows = []

        for payload in payloads:
            row = dict(payload)
            row["line_user_id"] = self.user_id
            rows.append(row)

        if not rows:
            return None

        return self._request(
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
            json_body=rows,
            extra_headers={
                "Prefer": (
                    "resolution=merge-duplicates,"
                    "return=minimal"
                )
            }
        )

    def get_drawing_state_fallback(self):
        rows = self._request(
            "GET",
            "chart_drawings",
            params={
                "select": "drawings,updated_at",
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "ticker": (
                    f"eq.{DRAWING_STATE_FALLBACK_TICKER}"
                ),
                "timeframe": (
                    f"eq.{DRAWING_STATE_FALLBACK_TIMEFRAME}"
                ),
                "limit": "1"
            }
        )

        if not isinstance(rows, list) or not rows:
            return None

        drawings = (rows[0] or {}).get(
            "drawings"
        )

        # 以單一元素陣列保存，與畫線欄位的陣列格式一致
        if (
            isinstance(drawings, list)
            and drawings
            and isinstance(drawings[0], dict)
        ):
            return drawings[0]

        if isinstance(drawings, dict):
            return drawings

        return None

    def save_drawing_state_fallback(self, state):
        return self._request(
            "POST",
            "chart_drawings",
            params={
                "on_conflict": (
                    "line_user_id,"
                    "ticker,"
                    "timeframe"
                )
            },
            json_body={
                "line_user_id": self.user_id,
                "ticker": (
                    DRAWING_STATE_FALLBACK_TICKER
                ),
                "timeframe": (
                    DRAWING_STATE_FALLBACK_TIMEFRAME
                ),
                "market_key": None,
                "drawings": [state],
                "updated_at": utc_now_string()
            },
            extra_headers={
                "Prefer": (
                    "resolution=merge-duplicates,"
                    "return=minimal"
                )
            }
        )

    def prune_drawing_states(self, valid_tickers):
        """
        刪除已沒有可提醒畫線的股票狀態列。

        只能在 chart_drawings 讀取成功且至少一列時呼叫，
        valid_tickers 必須包含「全部市場」的畫線股票。
        """
        valid_tickers = {
            str(ticker)
            for ticker in valid_tickers
        }

        rows = self._request(
            "GET",
            "breakout_alert_state",
            params={
                "select": "id,ticker",
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "group_id": (
                    f"eq.{ATLAS_LINES_GROUP_ID}"
                )
            }
        )

        if not isinstance(rows, list) or not rows:
            return 0

        orphan_ids = [
            row["id"]
            for row in rows
            if isinstance(row, dict)
            and row.get("id") is not None
            and str(row.get("ticker") or "")
            not in valid_tickers
        ]

        if not orphan_ids:
            return 0

        id_list = ",".join(
            str(row_id)
            for row_id in orphan_ids
        )

        self._request(
            "DELETE",
            "breakout_alert_state",
            params={
                "id": f"in.({id_list})",
                "line_user_id": (
                    f"eq.{self.user_id}"
                ),
                "group_id": (
                    f"eq.{ATLAS_LINES_GROUP_ID}"
                )
            },
            extra_headers={
                "Prefer": "return=minimal"
            }
        )

        print(
            f"🧹 已清除畫線狀態列：{len(orphan_ids)} 筆"
        )

        return len(orphan_ids)

    def close(self):
        self.session.close()
