import os
import re

import requests

from breakout_alert.account_permissions import storage_mode
from breakout_alert.supabase_store import FIXED_STOCK_GROUP_ID, fixed_parameter_map, parameter_ticker_key
from screener_common import register_tw_stock_name, safe_int


# =========================================================================
# 動態自訂群組工具
# =========================================================================
def normalize_group_name(value):
    name = str(
        value or ""
    ).strip()

    # 合併連續空白
    name = re.sub(
        r"\s+",
        " ",
        name
    )

    # 同時接受有空格與無空格的國旗市場前綴
    name = name.replace(
        "🇹🇼 台股",
        "🇹🇼台股"
    )

    name = name.replace(
        "🇺🇸 美股",
        "🇺🇸美股"
    )

    return name


def detect_group_market(group_name):
    normalized_name = normalize_group_name(
        group_name
    )

    if normalized_name.startswith(
        "🇹🇼台股"
    ):
        return "tw"

    if normalized_name.startswith(
        "🇺🇸美股"
    ):
        return "us"

    # 固定群組沒有國旗時仍可辨識
    if normalized_name.startswith("台股"):
        return "tw"

    if normalized_name.startswith("美股"):
        return "us"

    return None


def get_custom_group_number(group_key):
    group_key = str(
        group_key or ""
    ).strip()

    if not group_key.startswith("custom_"):
        return 999_999

    return safe_int(
        group_key.replace(
            "custom_",
            "",
            1
        ),
        999_999
    )


def normalize_custom_ticker(ticker):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if not ticker:
        return ""

    if (
        ticker.endswith(".TW")
        or ticker.endswith(".TWO")
    ):
        return ticker

    if ticker.isdigit():
        return ticker

    # Yahoo Finance 使用 BRK-B，不是 BRK.B
    return ticker.replace(".", "-")


def get_group_ma_list(group_item):
    ma_list = []

    for ma_key in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_value = safe_int(
            group_item.get(ma_key)
        )

        if (
            ma_value is not None
            and ma_value > 0
        ):
            ma_list.append(ma_value)

    return (
        sorted(set(ma_list))
        or [20]
    )


def get_stock_ma_list(stock_item):
    ma_list = []

    for ma_key in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_value = safe_int(
            stock_item.get(ma_key)
        )

        if (
            ma_value is not None
            and ma_value > 0
        ):
            ma_list.append(ma_value)

    return sorted(set(ma_list))


# =========================================================================
# Supabase 設定
# =========================================================================
def get_supabase_settings():
    base_url = os.environ.get(
        "SUPABASE_URL",
        ""
    ).strip().rstrip("/")

    service_key = os.environ.get(
        "SUPABASE_SERVICE_ROLE_KEY",
        ""
    ).strip()

    api_key = (
        service_key
        or os.environ.get(
            "SUPABASE_ANON_KEY"
        )
        or ""
    ).strip()

    if api_key and not service_key:
        print(
            "⚠️ 未設定 SUPABASE_SERVICE_ROLE_KEY，"
            "改用 anon key 讀取；若資料表已鎖定匿名讀取，"
            "將讀不到自選股與大盤設定"
        )

    if not base_url:
        print("❌ 未設定 SUPABASE_URL")
        return None, None

    if not api_key:
        print("❌ 未設定 Supabase API Key")
        return None, None

    if base_url.endswith("/rest/v1"):
        rest_url = base_url
    else:
        rest_url = f"{base_url}/rest/v1"

    return rest_url, api_key


def get_default_index_configs():
    return [
        {
            "ticker": "^TWII",
            "name": "台灣加權指數",
            "ma1": 23,
            "ma2": 29,
            "ma3": 61,
            "ma4": None,
            "enabled": True,
            "sort_order": 1
        },
        {
            "ticker": "^TWOII",
            "name": "台灣櫃買指數(OTC)",
            "ma1": 20,
            "ma2": 60,
            "ma3": 120,
            "ma4": None,
            "enabled": True,
            "sort_order": 2
        },
        {
            "ticker": "^GSPC",
            "name": "美國標普500",
            "ma1": 23,
            "ma2": 60,
            "ma3": None,
            "ma4": None,
            "enabled": True,
            "sort_order": 3
        },
        {
            "ticker": "^DJI",
            "name": "美國道瓊工業",
            "ma1": 20,
            "ma2": 23,
            "ma3": 55,
            "ma4": None,
            "enabled": True,
            "sort_order": 4
        },
        {
            "ticker": "^IXIC",
            "name": "美國那斯達克",
            "ma1": 29,
            "ma2": None,
            "ma3": None,
            "ma4": None,
            "enabled": True,
            "sort_order": 5
        },
        {
            "ticker": "^RUT",
            "name": "美國羅素2000",
            "ma1": 21,
            "ma2": 56,
            "ma3": None,
            "ma4": None,
            "enabled": True,
            "sort_order": 6
        },
        {
            "ticker": "^SOX",
            "name": "美國費城半導體",
            "ma1": 20,
            "ma2": 58,
            "ma3": 108,
            "ma4": None,
            "enabled": True,
            "sort_order": 7
        }
    ]


def get_default_group_metadata():
    return {
        "tw_g1": {
            "key": "tw_g1",
            "name": "台股-權值精選",
            "market": "tw",
            "is_custom": False,
            "sort_order": 1,
            "ma_list": [20]
        },
        "tw_g2": {
            "key": "tw_g2",
            "name": "台股-熱門",
            "market": "tw",
            "is_custom": False,
            "sort_order": 2,
            "ma_list": [20]
        },
        "us_g1": {
            "key": "us_g1",
            "name": "美股-權值精選",
            "market": "us",
            "is_custom": False,
            "sort_order": 1,
            "ma_list": [20]
        },
        "us_g2": {
            "key": "us_g2",
            "name": "美股-低本益比",
            "market": "us",
            "is_custom": False,
            "sort_order": 2,
            "ma_list": [20]
        },
        "us_g3": {
            "key": "us_g3",
            "name": "美股-超級績效",
            "market": "us",
            "is_custom": False,
            "sort_order": 3,
            "ma_list": [20]
        },
        "us_g4": {
            "key": "us_g4",
            "name": "美股-熱門",
            "market": "us",
            "is_custom": False,
            "sort_order": 4,
            "ma_list": [20]
        }
    }


def load_configs_from_supabase(
    target_user_id
):
    configs = {
        "tw_g1": {},
        "tw_g2": {},
        "us_g1": {},
        "us_g2": {},
        "us_g3": {},
        "us_g4": {}
    }

    group_metadata = (
        get_default_group_metadata()
    )

    index_configs = []
    default_index_configs = (
        get_default_index_configs()
    )

    target_user_id = str(
        target_user_id or ""
    ).strip()

    supabase_url, supabase_key = (
        get_supabase_settings()
    )

    if not supabase_url or not supabase_key:
        return (
            configs,
            default_index_configs,
            group_metadata
        )

    headers = {
        "apikey": supabase_key,
        "Content-Type": "application/json"
    }
    if supabase_key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {supabase_key}"

    if storage_mode(target_user_id, supabase_url, headers=headers, request=requests.get) != "cloud":
        print("ℹ️ 此帳號未啟用雲端儲存，略過私人群組與均線設定；使用公開預設資料。")
        return configs, default_index_configs, group_metadata

    name_mapping = {
        "台股-權值精選": "tw_g1",
        "台股-熱門": "tw_g2",
        "美股-權值精選": "us_g1",
        "美股-低本益比": "us_g2",
        "美股-超級績效": "us_g3",
        "美股-熱門": "us_g4"
    }

    group_id_to_key = {}

    try:
        groups_response = requests.get(
            f"{supabase_url}/groups",
            headers=headers,
            params={
                "select": "*",
                "line_user_id": f"eq.{target_user_id}"
            },
            timeout=15
        )

        stocks_response = requests.get(
            f"{supabase_url}/stocks",
            headers=headers,
            params={
                "select": "*",
                "line_user_id": f"eq.{target_user_id}"
            },
            timeout=15
        )

        print(
            "📡 Supabase groups 狀態："
            f"{groups_response.status_code}"
        )

        print(
            "📡 Supabase stocks 狀態："
            f"{stocks_response.status_code}"
        )

        if groups_response.status_code == 200:
            groups_data = (
                groups_response.json()
            )
        else:
            groups_data = []

            print(
                "❌ groups 查詢失敗："
                f"{groups_response.text[:500]}"
            )

        if stocks_response.status_code == 200:
            stocks_data = (
                stocks_response.json()
            )
        else:
            stocks_data = []

            print(
                "❌ stocks 查詢失敗："
                f"{stocks_response.text[:500]}"
            )

        print(
            "📋 Supabase 群組總數："
            f"{len(groups_data)}"
        )

        print(
            "📋 Supabase 股票總數："
            f"{len(stocks_data)}"
        )

        # -----------------------------------------------------------------
        # 建立 groups.id 與 stocks.group_id 的對照
        #
        # 固定群組：
        # groups.id = 1
        # stocks.group_id 可能為 tw_g1 或 1
        #
        # 自訂群組：
        # groups.id = 22
        # stocks.group_id = custom_22
        # -----------------------------------------------------------------
        for group_item in groups_data:
            # Enforce identity again even if an unexpected backend ignores the filter.
            if group_item.get("line_user_id") != target_user_id:
                continue
            raw_group_id = str(
                group_item.get("id", "")
            ).strip()

            raw_group_name = str(
                group_item.get("name", "")
            ).strip()

            group_name = normalize_group_name(
                raw_group_name
            )

            if (
                not raw_group_id
                or not group_name
            ):
                continue

            group_ma_list = (
                get_group_ma_list(
                    group_item
                )
            )

            # 固定群組
            if group_name in name_mapping:
                group_key = (
                    name_mapping[group_name]
                )

                group_id_to_key[
                    raw_group_id
                ] = group_key

                group_id_to_key[
                    group_key
                ] = group_key

                group_metadata[
                    group_key
                ]["ma_list"] = group_ma_list

                continue

            # 動態群組只載入目前使用者
            group_user_id = str(
                group_item.get(
                    "line_user_id",
                    ""
                )
                or ""
            ).strip()

            if group_user_id != target_user_id:
                continue

            market = detect_group_market(
                group_name
            )

            if market not in {
                "tw",
                "us"
            }:
                print(
                    "⚠️ 無法判斷自訂群組市場："
                    f"id={raw_group_id}, "
                    f"name={group_name}"
                )
                continue

            group_key = (
                f"custom_{raw_group_id}"
            )

            configs.setdefault(
                group_key,
                {}
            )

            group_metadata[group_key] = {
                "key": group_key,
                "id": raw_group_id,
                "name": group_name,
                "market": market,
                "is_custom": True,
                "sort_order": safe_int(
                    raw_group_id,
                    999_999
                ),
                "ma_list": group_ma_list,
                "created_at": str(
                    group_item.get(
                        "created_at",
                        ""
                    )
                )
            }

            group_id_to_key[
                raw_group_id
            ] = group_key

            group_id_to_key[
                group_key
            ] = group_key

            print(
                "✅ 載入動態群組："
                f"{group_key} / "
                f"{group_name} / "
                f"market={market} / "
                f"MA={group_ma_list}"
            )

        matched_user_count = 0
        loaded_count = 0
        fixed_parameters = fixed_parameter_map(stocks_data, target_user_id)

        for stock_item in stocks_data:
            stock_user_id = str(
                stock_item.get(
                    "line_user_id",
                    ""
                )
            ).strip()

            if stock_user_id != target_user_id:
                continue

            matched_user_count += 1

            group_id = str(
                stock_item.get(
                    "group_id",
                    ""
                )
            ).strip()

            ticker = str(
                stock_item.get(
                    "ticker",
                    ""
                )
            ).strip().upper()

            stock_name = str(
                stock_item.get("name")
                or stock_item.get("stock_name")
                or stock_item.get("company_name")
                or stock_item.get("chinese_name")
                or stock_item.get("tw_name")
                or ""
            ).strip()

            if not ticker:
                print(
                    "⚠️ 發現 ticker 為空的資料"
                )
                continue

            if group_id in {"admin_index", FIXED_STOCK_GROUP_ID}:
                print(
                    f"ℹ️ 略過 {group_id}："
                    f"{ticker}"
                )
                continue

            mapped_key = (
                group_id_to_key.get(
                    group_id
                )
            )

            # 固定群組直接保存 tw_g1、us_g1 等代碼
            if (
                not mapped_key
                and group_id in configs
            ):
                mapped_key = group_id

            # 動態群組防呆
            if (
                not mapped_key
                and group_id.startswith(
                    "custom_"
                )
                and group_id in group_metadata
            ):
                mapped_key = group_id

            if not mapped_key:
                print(
                    "⚠️ 股票找不到對應群組："
                    f"ticker={ticker}, "
                    f"group_id={group_id}"
                )
                continue

            metadata = group_metadata.get(
                mapped_key,
                {}
            )

            market = metadata.get(
                "market"
            )

            ticker = normalize_custom_ticker(
                ticker
            )

            if market == "us":
                ticker = ticker.replace(
                    ".",
                    "-"
                )

            if (
                market == "tw"
                and stock_name
            ):
                register_tw_stock_name(
                    ticker,
                    stock_name
                )

            # 股票均線優先；全為 0 或空值時繼承群組均線
            stock_ma_list = fixed_parameters.get(parameter_ticker_key(ticker)) or get_stock_ma_list(stock_item)

            if stock_ma_list:
                final_ma_list = stock_ma_list
                ma_source = "stock"
            else:
                final_ma_list = (
                    metadata.get("ma_list")
                    or [20]
                )
                ma_source = "group"

            configs.setdefault(
                mapped_key,
                {}
            )

            configs[mapped_key][
                ticker
            ] = final_ma_list

            loaded_count += 1

            print(
                "✅ 載入 Supabase 股票："
                f"{mapped_key} / "
                f"{ticker} / "
                f"MA={final_ma_list} / "
                f"source={ma_source}"
            )

        print(
            "👤 符合 Supabase User ID "
            f"的資料數：{matched_user_count}"
        )

        print(
            "📊 成功載入群組股票數："
            f"{loaded_count}"
        )

        if matched_user_count == 0:
            print(
                "⚠️ Supabase 查詢不到目前使用者。"
                "請確認 SUPABASE_USER_ID 是否與 "
                "stocks.line_user_id 相同。"
            )

    except Exception as exc:
        print(
            "❌ 讀取雲端個股失敗："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        index_response = requests.get(
            f"{supabase_url}/index_configs",
            headers=headers,
            params={
                "select": "*"
            },
            timeout=15
        )

        if index_response.status_code == 200:
            index_configs = (
                index_response.json()
            )

            print(
                "📡 成功從雲端同步全球大盤"
                "自訂均線參數，共 "
                f"{len(index_configs)} 筆"
            )
        else:
            print(
                "⚠️ index_configs 查詢失敗："
                f"HTTP "
                f"{index_response.status_code} "
                f"{index_response.text[:500]}"
            )

    except Exception as exc:
        print(
            "⚠️ 讀取大盤設定失敗："
            f"{type(exc).__name__}: {exc}"
        )

    if not index_configs:
        index_configs = (
            default_index_configs
        )

    print(
        "\n===== Supabase 群組讀取結果 ====="
    )

    sorted_group_keys = sorted(
        configs.keys(),
        key=lambda key: (
            1 if key.startswith(
                "custom_"
            ) else 0,
            (
                get_custom_group_number(key)
                if key.startswith("custom_")
                else 0
            ),
            key
        )
    )

    for group_key in sorted_group_keys:
        stocks = configs[group_key]

        metadata = group_metadata.get(
            group_key,
            {}
        )

        print(
            f"{group_key}: "
            f"{len(stocks)} 檔 / "
            f"{metadata.get('name', group_key)}"
        )

        for ticker, ma_list in stocks.items():
            print(
                f"  └─ {ticker}: {ma_list}"
            )

    print(
        "================================\n"
    )

    return (
        configs,
        index_configs,
        group_metadata
    )


def get_ma_list_from_item(item):
    ma_list = []

    for ma_key in [
        "ma1",
        "ma2",
        "ma3",
        "ma4"
    ]:
        ma_value = safe_int(
            item.get(ma_key)
        )

        if (
            ma_value is not None
            and ma_value > 0
        ):
            ma_list.append(ma_value)

    return sorted(set(ma_list)) or [20]


def is_config_enabled(item):
    enabled_value = item.get(
        "enabled",
        True
    )

    if isinstance(enabled_value, str):
        return (
            enabled_value.strip().lower()
            not in {
                "0",
                "false",
                "no",
                "off"
            }
        )

    return bool(enabled_value)


# =========================================================================
# 第 4 部分：動態群組工具
# =========================================================================

def get_sorted_custom_group_keys(
    group_metadata,
    market=None
):
    keys = []

    for group_key, metadata in (
        group_metadata.items()
    ):
        if not metadata.get(
            "is_custom",
            False
        ):
            continue

        if (
            market is not None
            and metadata.get("market")
            != market
        ):
            continue

        keys.append(group_key)

    return sorted(
        keys,
        key=get_custom_group_number
    )
