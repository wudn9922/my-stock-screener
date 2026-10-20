# ADR-014 — Unified console and saved screening lists

Status: user-authorized extension, 2026-10-20.

Atlas owns the console query route. Parameter management calls the existing LINE-verified user-settings API; no external control iframe or browser service credentials. The original chart engine, drawings, IndexedDB models, financial computations and breakout thresholds remain in use.

Saved lists have a separate validated v1 local document containing source/market, the existing canonical filter encoding, optional fixed symbols, monitoring intent, bounded daily observations and a separate last-opening snapshot. Browser and daily collector use the same filter implementation. Collection never moves the opening baseline. Failed prices/required sources and backward dates cannot become false exits.

Optional Supabase records are keyed by verified LINE user/list UUID. RLS denies direct browser access; writes/deletes require an expected server version. Deployment remains an explicit operator checklist in UNIFIED_CONSOLE.md. No live migration is part of this source change. The existing Python breakout service can opt into enabled lists using its existing engine/notifier; unavailable watchlist settings cannot authorize deletion of alert baselines.

US statements expose actual quarterly history with a default 20-quarter view and explicit p=a annual links. Annual EPS quality gates and US river annual valuation basis remain. Peers reuse existing financial functions and disclose period/currency/nulls. Rotation uses aligned relative-return windows; health distinguishes readable data from complete data.

Validation includes unit/lint/build checks, storage/daily observation tests, offline breakout tests and desktop/mobile interactions. CI WebKit and physical-device UAT are separate evidence. No independent reviewer sign-off is implied.
