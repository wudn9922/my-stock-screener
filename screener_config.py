import os


# =========================================================================
# 全域核心參數
# =========================================================================
# 僅供每日 LINE 報告 tw_all 清單；行情下載與 CSV 更新不設成交股數門檻。
# tw_all 另需 MA20 上彎，並依 RS 百分位排序（見 _market_scan_report 的 latent_rules）。
TW_MIN_VOLUME = 1_000_000

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

DATA_DIR = "data"
REPORT_NON_TRADING_DATES = {"TW": frozenset(), "US": frozenset()}
DOCS_DIR = "docs"
# 252 根可算 52 週高點；新 CSV 初始化下載 250d，既有 CSV 每天累積一根
MAX_DAYS = 260

# 少於此根數的 CSV 由 backfill_short_csvs 下載 1 年日 K 補齊（預設不啟用）
BACKFILL_ENABLED = False
BACKFILL_MIN_ROWS = 240
BACKFILL_MAX_PER_RUN = 200

# data/*.csv 是永久 K 線紀錄，不再依日期或掃描名單刪除。


# =========================================================================
# Atlas 報告 JSON（docs/report/，網址 /my-stock-screener/report/）
# =========================================================================
REPORT_DIR = os.path.join(DOCS_DIR, "report")
REPORT_SERIES_DIR = os.path.join(REPORT_DIR, "series")
REPORT_JSON_VERSION = 1

# =========================================================================
# LIFF、報告與畫線同步
# =========================================================================

# 圖表報告專用 LIFF
REPORT_LIFF_ID = "2010330411-6JhrotT9"
REPORT_LIFF_URL = (
    "https://liff.line.me/"
    f"{REPORT_LIFF_ID}"
)

# 舊 LIFF ID 保留供既有捷徑辨識；新報告入口直接開啟 Atlas 內的控制台。
SETTING_LIFF_ID = "2010330411-SbwvRXRN"
SETTING_LIFF_URL = f"{REPORT_LIFF_URL}?page=console"

# 原本另一套控制台，保持不變
BITGET_SETTING_URL = (
    "https://liff.line.me/"
    "2010535859-y3CoAWaC"
)


# =========================================================================
# 自訂群組篩選設定
# =========================================================================

# True：Supabase 自訂群組只要可下載就顯示
# False：套用均線距離條件
CUSTOM_GROUP_TEST_MODE = (
    os.environ.get(
        "CUSTOM_GROUP_TEST_MODE",
        "false"
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on"
    }
)

# 股價位於均線下方 3% 至上方 1%
CUSTOM_MA_MIN_RATIO = 0.97
CUSTOM_MA_MAX_RATIO = 1.01
