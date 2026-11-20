"""Per-list notification settings (Atlas 我的 › 清單 › 通知).

Stored as one JSON row per LINE account in `atlas_alert_settings`:

    {"version": 1, "mode": "all" | "custom",
     "lists": {"group:tw_g1": {"ma": true, "drawing": false},
               "watchlist:<uuid>": {"ma": false, "drawing": true}}}

`mode: "all"` (also the default when no row exists, the table is not deployed
or the row is malformed) keeps today's behaviour: MA alerts for every grouped
stock and every monitored watchlist, drawing alerts for every drawing.
In `custom` mode a symbol notifies for an alert type when ANY list containing
it enables that type. Index (大盤) alerts are not list-based and always run.
Settings filter what is alerted only; alert baselines are never pruned because
of them, so re-enabling a list does not lose state.
"""

import re
from dataclasses import dataclass, field

from breakout_alert.supabase_store import parameter_ticker_key

ALL = "all"
CUSTOM = "custom"
KINDS = ("ma", "drawing")
MAX_LISTS = 200
LIST_KEY = re.compile(
    r"^(group:(tw_g[12]|us_g[1-4]|custom_\d{1,18})"
    r"|watchlist:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
NON_MEMBER_GROUPS = {"", "ungrouped", "admin_index"}


@dataclass(frozen=True)
class ListNotificationSettings:
    mode: str = ALL
    lists: dict = field(default_factory=dict)

    @property
    def all_lists(self):
        return self.mode != CUSTOM

    def enabled(self, list_key, kind):
        if self.all_lists:
            return True
        return bool(self.lists.get(list_key, {}).get(kind))

    def watchlists_with(self, kind):
        """Watchlist ids that custom mode enables for `kind`."""
        if self.all_lists:
            return set()
        return {
            key.split(":", 1)[1].lower()
            for key, flags in self.lists.items()
            if key.startswith("watchlist:") and flags.get(kind)
        }


DEFAULT_SETTINGS = ListNotificationSettings()


def parse_settings(raw):
    """Validate a stored settings document; anything unusable means "all lists"."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return DEFAULT_SETTINGS
    if raw.get("mode") != CUSTOM:
        return DEFAULT_SETTINGS
    lists = raw.get("lists")
    if not isinstance(lists, dict) or len(lists) > MAX_LISTS:
        return DEFAULT_SETTINGS
    parsed = {}
    for key, flags in lists.items():
        if not isinstance(key, str) or not LIST_KEY.match(key) or not isinstance(flags, dict):
            return DEFAULT_SETTINGS
        if any(type(flags.get(kind, False)) is not bool for kind in KINDS):
            return DEFAULT_SETTINGS
        parsed[key.lower() if key.startswith("watchlist:") else key] = {
            kind: flags.get(kind, False) for kind in KINDS
        }
    return ListNotificationSettings(mode=CUSTOM, lists=parsed)


def config_list_key(config):
    """The list a monitor config came from; None for index configs (not list-based)."""
    group_id = str(config.get("raw_group_id") or config.get("group_id") or "").strip()
    if group_id == "admin_index":
        return None
    if group_id.startswith("watchlist_"):
        return f"watchlist:{group_id[len('watchlist_'):].lower()}"
    return f"group:{group_id}"


def filter_ma_configs(configs, settings):
    """Configs that may alert: enabled lists only, one config per ticker (no duplicate alerts).

    Group configs precede watchlist configs, so a stock in a group and a monitored
    watchlist keeps alerting under its group exactly as before.
    """
    result = []
    seen = set()
    for config in configs:
        key = config_list_key(config)
        if key is None:
            result.append(config)
            continue
        if not settings.enabled(key, "ma"):
            continue
        ticker = parameter_ticker_key(config.get("ticker"))
        if ticker in seen:
            continue
        seen.add(ticker)
        result.append(config)
    return result


def watchlist_symbols(definition):
    """Members of a synced watchlist: manual symbols, or a rule list's latest result."""
    if not isinstance(definition, dict):
        return []
    if definition.get("kind") == "manual":
        symbols = definition.get("symbols")
        return [s for s in symbols if isinstance(s, str)] if isinstance(symbols, list) else []
    history = definition.get("history")
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return []
    members = history[-1].get("members")
    if not isinstance(members, list):
        return []
    return [m.get("symbol") for m in members if isinstance(m, dict) and isinstance(m.get("symbol"), str)]


def drawing_allowed_tickers(settings, stocks, watchlist_rows):
    """Normalized tickers whose drawing alerts may notify; None means every drawing."""
    if settings.all_lists:
        return None
    allowed = set()
    for stock in stocks or []:
        if not isinstance(stock, dict):
            continue
        group_id = str(stock.get("group_id") or "").strip()
        if group_id in NON_MEMBER_GROUPS:
            continue
        if settings.enabled(f"group:{group_id}", "drawing"):
            allowed.add(parameter_ticker_key(stock.get("ticker")))
    for row in watchlist_rows or []:
        if not isinstance(row, dict):
            continue
        list_id = str(row.get("id") or "").lower()
        if settings.enabled(f"watchlist:{list_id}", "drawing"):
            allowed.update(parameter_ticker_key(symbol) for symbol in watchlist_symbols(row.get("definition")))
    return allowed


def filter_drawing_watchlist(watchlist, allowed):
    """Drop drawing entries of symbols no enabled list contains (state pruning keeps the full list)."""
    if watchlist is None or allowed is None:
        return watchlist
    from breakout_alert.drawing_alerts import DrawingWatchlist

    entries = [entry for entry in watchlist.entries if parameter_ticker_key(entry.symbol) in allowed]
    return DrawingWatchlist(
        entries=entries,
        row_count=watchlist.row_count,
        valid_tickers=watchlist.valid_tickers,
        line_count=sum(len(entry.lines) for entry in entries),
    )
