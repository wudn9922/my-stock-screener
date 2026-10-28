# ADR-019: Private account holdings and browser-only import

Status: proposed in PR A, 2026-10-28.

Add a holdings view alongside the existing watchlist console, with the list view
remaining the default. No navigation-shell or authentication changes. Normalized
holdings are snapshots per account, replaced atomically after an editable preview.
CSV/XLSX and pasted raw content remain in the browser; saved mappings contain only
column indexes, a header fingerprint and quantity unit. Firstrade transaction files
use dated average-cost reconstruction and require review of skipped/non-trade rows.

The existing verified storage context owns every record. Guest/none uses userStorage
RAM, device uses its account-qualified local space, and cloud uses Supabase with no
local portfolio copy. A changed context invalidates old stores and pending work.
Existing settingsApi is the transport, while each server request still passes
requireCloudStorage. The service-role-only transactional RPC binds verified owner,
serializes writes for per-owner limits and uses account revisions for concurrency.
RPC failures are sanitized before the generic handler logger. Manual SQL is supplied
for maintainers; no schema or functions are deployed during development.

Decimal strings are canonical for quantities and inclusive average costs; Decimal.js
calculates values without floating-point accumulation. Public prices remain behind
the existing provider and research contracts. Missing prices, absent FX and unsupported
crypto never produce a guessed complete total. USD/TWD is not a USDT exchange rate.
See [the runbook](../../../docs/ATLAS_HOLDINGS.md) for formats, boundaries, limitations
and the required manual SQL → user-settings → frontend rollout order.

MEXC credential storage and automated synchronization are a separate PR B after A
merges. This decision does not authorize API keys, broker order placement or alerts.
