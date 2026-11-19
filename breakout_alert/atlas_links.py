"""Atlas 圖表深連結：讓提醒訊息一點就開到該股票的 Atlas 圖表。"""
from urllib.parse import quote

ATLAS_BASE_URL = "https://wudn9022.github.io/my-stock-screener/atlas/"
ATLAS_LINK_LABEL = "開啟 Atlas 圖表"


def atlas_chart_url(symbol, timeframe="1D"):
    """回傳 Atlas 圖表網址；symbol 會 URL 編碼（^TWII → %5ETWII）。"""
    symbol = str(symbol or "").strip()
    if not symbol:
        return ""
    return (
        f"{ATLAS_BASE_URL}?page=chart&tf={quote(str(timeframe), safe='')}"
        f"&symbol={quote(symbol, safe='')}"
    )


def atlas_markdown_link(symbol, label=ATLAS_LINK_LABEL, timeframe="1D"):
    """Discord markdown 連結；沒有代號時回傳空字串。"""
    url = atlas_chart_url(symbol, timeframe)
    return f"[{label}]({url})" if url else ""


def append_atlas_link(text, symbol, limit):
    """文字超過 limit 時只截斷本文，連結一定保留在最後一行。"""
    link = atlas_markdown_link(symbol)
    if not link:
        return str(text)[:limit]
    link = f"📈 {link}"
    room = max(limit - len(link) - 1, 0)
    return f"{str(text)[:room]}\n{link}"


def ticker_links(symbols, limit=1900):
    """多檔股票：Discord markdown「[2330](url) [AAPL](url)」，超過 limit 時省略其餘。"""
    symbols = [s for s in (str(s).strip() for s in symbols) if s]
    parts = []
    used = 0
    for index, symbol in enumerate(symbols):
        part = f"[{symbol}]({atlas_chart_url(symbol)})"
        remaining = len(symbols) - index - 1
        tail = len(f" …等另 {remaining} 檔") if remaining else 0
        if used + len(part) + 1 + tail > limit:
            parts.append(f"…等另 {len(symbols) - index} 檔")
            break
        parts.append(part)
        used += len(part) + 1
    return " ".join(parts)


def plain_ticker_links(symbols):
    """LINE 純文字：每檔一行「SYMBOL URL」（LINE 會自動轉為可點連結）。"""
    return "\n".join(
        f"{s} {atlas_chart_url(s)}"
        for s in (str(s).strip() for s in symbols)
        if s
    )
