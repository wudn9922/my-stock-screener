# ADR-017 — Five-destination navigation, phase 1

Status: user-authorized navigation-only extension, 2026-10-27.

Primary navigation has watchlist, markets, screener, chart and console. World/themes keep their existing
routes and page components under a shared market-section navigator; session refs remember each section's
existing selectors. DEFAULT_PAGE, brand links, scroll ownership, RouteBoundary and chartVisited remain.

ConsolePage uses a section prop to expose its existing list content at watchlist and its parameters/admin
content at console. DisplaySettings extracts the original gear contents and serves both the retained dialog
and the new display/App tab. Storage notices, account gates, legacy import, list models, cloud APIs,
private-data namespaces and the chart engine are unchanged.

The query router retains all prior non-console routes and LIFF unwrapping. Old console+list and watchlists
aliases canonicalize to watchlist; console/settings expose 我的. Internal list callbacks use the canonical
watchlist route. Browser history applies the same canonical rewrite when traversing old URLs.

There is no storage migration, data rewrite, default-home change, backend change or deployment. Validation
uses a URL/LIFF matrix, a feature reachability inventory and the complete existing site regressions. See
[NAVIGATION_PHASE_ONE.md](../NAVIGATION_PHASE_ONE.md) for the exhaustive feature/URL mapping and evidence.
