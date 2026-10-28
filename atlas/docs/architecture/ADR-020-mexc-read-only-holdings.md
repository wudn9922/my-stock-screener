# ADR-020: MEXC read-only account synchronization

Status: proposed, PR B after #199 merged, 2026-10-28.

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
