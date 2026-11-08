# ADR-020: MEXC read-only account synchronization

Status: proposed, PR B after #199 merged, 2026-10-28.

2026-11-06 follow-up keeps encryption, authorization and SQL unchanged. Upstream
failures log one metadata-only diagnostic with fixed reason codes; unknown field
names and non-allowlisted messages are redacted. Explicit numeric-zero success
envelopes are unwrapped, while all endpoint schemas and read-only permission
checks remain strict. Current official examples are bare objects, so the actual
production failure still needs the maintainer's post-deployment diagnostic.

Invalid-key errors belong to one account in the current RAM-only view and retain
the successful snapshot/time, including across status reload. Successful sync,
key replacement or disconnect clears that account's error. After 80 days since
connectedAt, the panel shows the unbound-key 90-day reminder; it is not an exact
exchange expiry date. No notification channel or polling interval is added.

2026-11-07 authorized extension adds a separate read-only Futures V1 response and
account-scoped RAM view. It uses the current official REST domain api.mexc.com,
header signing, detail/country and ticker fairPrice. Futures failures downgrade
only that view; all upstream I/O precedes the existing final authorization and
revision checks. Futures never enters SQL, the spot document, valuation or
allocation. See [the API mapping and isolation](../../../docs/ATLAS_MEXC_FUTURES.md).

Add website API credential entry for cloud-authorized MEXC accounts. The browser
sends the pair once on explicit submission, clears fields, never persists keys and
shows only the server-returned last four characters. Bind the originating storage
context before awaiting identity initialization, including existing holdings calls.

The existing user-settings function remains the only transport and checks cloud
authorization per request and again after upstream I/O. A fixed-origin GET-only
client verifies current key permissions, reads free plus locked spot balances, and
filters public USDT tickers. No trading, transfer, withdrawal or notification API.
AES-GCM encrypts both credentials using a server-only environment key and owner /
account-bound AAD. Private tables and the public RPC are service-role-only.

Source snapshots separate unknown cost from confirmed holdings. New/increased
balances require explicit cost review; equal/decreased balances retain averages.
Pending accounts are excluded from P&L, never treated as zero-cost positions. One-use
operation claims, shared owner locks and account/snapshot revisions prevent a late
sync from reviving disconnected keys or overwriting newer imports. Persistent rate
limits and visible-page five-minute polling bound upstream work.

MEXC ticker retrieval time is not an exchange timestamp; no previous daily close
is fabricated from rolling 24h statistics. Missing quotes / USDT FX remain unknown.
Manual / device / guest holdings and existing navigation keep their behavior.

Manual SQL and a server secret must precede function and frontend release. No SQL
execution, production deployment or real MEXC calls during this task. Static SQL
parsing and fake-backed handler/browser tests do not certify database RLS or native
iPhone behavior. See [the runbook](../../../docs/ATLAS_MEXC_SYNC.md).


2026-11-08 extension: `holdings.mexc.futures` reads encrypted credentials through
an independent service-role RPC and per-owner/account 30-second quota. A small
manual SQL addition stores only rate-limit/request metadata; financial futures
remain RAM-only. Reauthorization and a final token/ciphertext check reject reads
that raced a disconnect, key change or grant revocation. No spot document,
snapshot, timestamp, revision or spot reservation is changed. The visible active
holdings page loads once and refreshes each minute, while hidden pages stop new
reads. Failed reads retain the last successful values with their retrieval time;
manual sync results supersede older reads. Source decimals round with BigInt to
18 places (half away from zero), optional invalid PnL falls back to mark-price
calculation, and UI PnL displays at most eight places. Maintainer applies the new
manual SQL before redeploying user-settings, then publishes Atlas. No deployment
or SQL execution is part of this task.
