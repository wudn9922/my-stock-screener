# ADR-016: Account isolation and owner-controlled persistence

Status: implemented in the PR; production SQL and both Edge Function deployments pending.

The screener deployment resolves a verified LINE account and its server storage policy before opening personal storage. Ordinary browser visitors do not automatically load LIFF. The self-use PWA extension allows an explicit login button, a recognized OAuth return, or a LIFF primary handoff to initialize LIFF outside LINE; the SDK's functional authentication storage is used only after that opt-in. It does not grant application storage: every recovered session still requires a fresh server policy check. Guests and ungranted accounts use RAM only; identity verification or policy errors also fail closed to RAM. The existing server-configured owner defaults to cloud, while everyone else defaults to none. No user or grant row is created on login. Only the verified owner can list or change grants, even when the owner's personal storage mode is device or none.

Modes are none, device and cloud. Device persists locally under the verified account; cloud additionally enables the existing authenticated Supabase endpoints. Groups, fixed stock moving averages, saved lists, display preferences, drawing workspaces and disposable financial/market caches follow the same policy. Personal exports are disabled in none mode. Account and mode namespace both localStorage and IndexedDB. Old shared records are retained without automatic adoption, transfer, upload or deletion. Mode transitions do not automatically transfer records.

An explicit legacy import is available only after the server verifies a LINE account with device or cloud permission. The console and chart page offer source counts and Import / Not now controls; guests, ungranted users and none mode do not inspect the legacy stores. The source is the unscoped `atlas-terminal-screener` database and known legacy localStorage settings keys. Reading never upgrades, changes, renames or deletes the old database. Validation and version migrations run in memory using the existing settings schemas. Caches, authentication material, grants and previous cloud sync bindings are excluded.

Legacy import merges into the current account/mode namespace, adding missing items while preserving the account version on a conflict and reporting the count. Ordinary settings-file import retains its validated atomic replacement semantics. Cloud import uses the normal checked sync paths; copied state never carries another user's cloud binding. Import/dismissal status is recorded per verified account per browser, with a console control to show the card again. Repeated imports are idempotent, and a second authorized account can still choose to import because the legacy source is untouched. Upgrade does not depend on a manual export backup.

An account/policy epoch replaces the rendered workspace, chart store, list store and in-memory preference state. Cloud callbacks, queued operations and cache responses are bound to that epoch. The LINE token callback checks the current subject and expiry. Visibility, focus and periodic refresh recheck policy; no browser grant cache allows offline persistence. Screener builds stop installing their service worker and retire only the old screener-scoped shell cache. The separate drawing-lab build retains its existing offline behavior.

Both private-data Edge Functions verify LINE independently, derive ownership from its verified subject, and require a cloud grant before touching personal tables. SQL revokes direct anon/authenticated table privileges, enables RLS, and checks cloud permission again at mutation time. Grant changes and personal writes serialize on a per-account advisory lock. The policy table is bounded and changes only through explicit owner operations. Scheduled consumers also check policy and scope user queries; public GitHub reports accept only the configured owner.

This changes storage routing, not the drawing engine, canonical models, market feeds, authentication provider or trading strategies. Existing production rows remain intact. Rollout, account management, data recovery, verification and the limits of device revocation are documented in [ACCOUNT_STORAGE_PERMISSIONS.md](../../../docs/ACCOUNT_STORAGE_PERMISSIONS.md).

## Cross-browser LINE handoff (2026-10-26)

Mobile browsers / installed PWAs now keep the receiving page alive while a separate
LIFF window authenticates and explicitly approves a matching pairing code in a
top-level page. Merely visiting a handoff link never transfers an identity. A two-minute, proof-bound, encrypted server handoff
delivers a candidate identity once only after a server-generated reverse confirmation code is typed
in the original window and begin/complete match the trusted ingress network prefix.
The code is shown only in the LINE window, hashed on the private row, limited to five wrong guesses
under a row lock, and never returned by poll. CF-Connecting-IP is the sole source on the verified
hosted ingress; missing/ambiguous headers, shared Worker IPs and legacy unbound intents fail closed.
This is followed by explicit account confirmation and
the unchanged server storage-grant check. Receiving proofs and recovered identity
stay in RAM; functional authentication metadata is the sole addition to a private
server schema. No personal settings or grants are created by this transport.
The parent LIFF endpoint renders the same Atlas bootstrap before URL changes.
This supersedes the former same-browser SDK assumption for mobile login; the
desktop fallback remains. See [handoff design, SQL and rollout](../../../docs/LINE_LOGIN_HANDOFF.md).

## Mobile dispatch and sheet return (2026-10-27)

Mobile login first attempts the documented legacy line://app scheme from the active
gesture in a disposable child frame, preserving the receiver document and RAM proof.
LINE deprecates this scheme due to interception risks; it is a compatibility attempt,
not a trusted app-identity signal. Only the existing intent/write proof is dispatched.
The supported HTTPS path remains available through an isolated, static-first launch
entry with visible close/X instructions and a best-effort close on return. Its LIFF
ID is compiled configuration, never a query-selected target. Pending receivers reveal
and focus the reverse-code field when foregrounded or when readiness arrives later.
No server transport, authorization or storage policy changes. Native app dispatch
and the physical iOS keyboard remain owner device acceptance items.
See [details and limitations](../../../docs/LINE_LOGIN_LAUNCH.md).
