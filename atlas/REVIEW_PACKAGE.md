## Current follow-up: iOS top-level LINE app launch (2026-10-27)

Real-iPhone report on #185: the hidden `line://app` iframe never opened LINE from the
home-screen PWA (iOS WebKit ignores custom schemes in subframes). iOS/iPadOS now
activate a disposable `line://app/{LIFF_ID}?intent&write` link (top-level) inside the
same synchronous gesture; only `line://app/` may be navigated, never http(s), no
reload or window.open. Android keeps the subframe: LINE documents no `intent://` form
and a top-level failure could replace the receiver. If the page is still visible and
focused ~1.5 s later (no hidden/blur/pagehide), the existing manual link becomes a
prominent button with a hint; nothing is auto-opened. Proof scope, reverse code,
network binding, TTL, one-use claim and server grants are unchanged. Browser tests
stub the scheme click and assert no main-frame navigation/pagehide. No server change.

## Previous follow-up: iOS launch sheet and receiver focus (2026-10-27)

Primary self-review; no independent sign-off. #184 was already merged, so this is
a separate PR from current main. Mobile user gestures dispatch a legacy line://app
URI in a disposable child frame, never navigating the RAM-only receiver or opening
a window first. LINE's official deprecation and takeover warning are explicit in
[the runbook](../docs/LINE_LOGIN_LAUNCH.md); this is not described as a newly supported
secure app-link scheme or as headless proof of iPhone native dispatch. Existing
reverse code, network binding, read/write proofs and server grants remain mandatory.

Fallbacks load a real static-first HTML entry before offering the official HTTPS
LIFF link; close failures leave readable X instructions. The helper gets its LIFF
ID from the build, ignores query-selected IDs/redirects, strips its own query, uses
no-referrer and never polls, initializes SDK or writes storage. Original pending
receivers scroll/focus the code field on foreground and on late readiness.
Tests model OS dispatch without claiming a real app launch; they cover no primary
popup/navigation, frame cleanup, wrong code/network regressions, focus, blocked
close, no-JS explanation and malicious target rejection. No SQL, Edge Function,
production settings, merge or deployment. Actual test counts are in the PR.

## Previous follow-up: reverse confirmation and same-network binding (2026-10-27)

Accepted the reported approval-phishing gap: a visual pairing warning alone cannot
stop a receiver who convinces a visitor to approve its link. The original receiver
must now manually enter a separate six-digit code shown only after verified LINE
completion. Server randomness uses unbiased rejection sampling; the row stores only
a secret-salted, domain/intent-bound hash. Empty readiness polls neither reveal the
code/identity nor consume attempts. SQL row locking counts wrong codes and deletes
on the fifth; cancellation returns no ciphertext. Existing read/write proofs, visual
pairing code, TTL, encryption and final account/grant confirmation remain required.

Begin and complete bind an independently domain-salted IPv4 /24 or IPv6 /64 hash.
Only CF-Connecting-IP from the current hosted Supabase ingress is accepted. No
X-Forwarded-For / X-Real-IP fallback; malformed/missing values and Cloudflare's shared
cross-zone Worker sentinel fail closed. Read-only GET probes confirmed this actual
endpoint rejects a forged CF header while a forged XFF survives; function ingress
logs were queried only for booleans, never client IPs/tokens. Primary official-source
references, evidence table, scope and network routing limitations are in
[the handoff runbook](../docs/LINE_LOGIN_HANDOFF.md). Shared NAT/VPN membership does
not prove identity; neither control is claimed to stop disclosure of the reverse
code to a phisher. LINE approval explicitly says only to approve a login just
initiated on one's own device.

The manual SQL supports old schema upgrades and repeated application. New columns
are nullable for legacy rows, which fail closed and must restart; the six-argument
RPC overload is removed. Permissions stay RLS/private/service-only SECURITY INVOKER.
No production schema/function/frontend deployment, merge, grant or personal data
change was performed. SQL -> function -> frontend remains a required rollout order.

Native disposable PostgreSQL covers concurrent wrong guesses, limit/deletion,
legacy rows/overload removal, missing/different networks and one-use consumption.
Browser regressions execute the real shared handler and cryptography with simulated
trusted ingress, plus the pinned real SDK in separate storage partitions. They cover
same-device flow, correction of a wrong code, five guesses and network rejection.
Actual test counts and exact-head CI are recorded in the PR. Primary review only;
no external independent sign-off or physical iPhone/real LINE consent claim.

## Previous follow-up: real SDK storage partitions and one-time LINE handoff (2026-10-26)

The official SDK 2.31.2 reproduced the reported loop: its own partition exchanges
the callback code, while a new browser context with the same callback has no PKCE
verifier and remains logged out. Earlier account-return doubles did not model
that behavior. The deployed root also redirected before primary SDK init.

Mobile / standalone login now uses a separate LIFF window and a two-minute
one-use handoff. Receiver and sender proofs are separate; only hashes are stored.
The sending page requires an explicit top-level approval and matching visual
pairing code before transferring anything; merely visiting a link does not
complete a handoff, and framed approval is rejected. The visual code is not an
authorization key. The verified ID token is AES-GCM encrypted with a server-only key and bound to
its intent. The receiver confirms the account and still needs the normal fresh
access.get grant. No URL or candidate identity can grant persistence. Private
RLS storage, service-only invoker RPC, bounded capacity, expiry and atomic
consumption are tested against native disposable PostgreSQL. The root now uses
the same Atlas bootstrap; desktop SDK fallback and guest RAM-only behavior stay.

Real SDK browser tests use test ECDSA keys and provider HTTP fixtures, including
two completely independent browser contexts. Existing SDK fallback tests now
explicitly model a desktop user agent; mobile/PWA behavior is exercised in the
new real-SDK handoff suite. No authentication assertion was relaxed. Official
provider consent / physical iPhone return is not claimed.

Read-only public deployment/configuration and aggregate server logs were checked.
Production advisories showed only the existing no-policy RLS informational
notices ([reference](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)), matching the service-only policy. No production schema, grant, function
or data was changed. SQL -> function -> Atlas entry publication is an explicit
rollout prerequisite in [the handoff runbook](../docs/LINE_LOGIN_HANDOFF.md).
Primary review only; no independent external signoff. Actual checks are in the PR.

## Previous follow-up: LINE login handoffs and bounded explicit startup (2026-10-26)

The pre-router gate recognized only OAuth code/error returns with a state query
parameter. It missed LIFF primary liff.state/referrer and credential-fragment
handoffs, which the query router could normalize away before SDK initialization.
It also imposed a state requirement that the SDK's code/PKCE processing does not
require. Supported LIFF markers now reach SDK init before routing; consumed credential
fragments are then removed without retaining them in a canonical route; arbitrary
unmarked code parameters still do not initialize login or grant any access.

An explicit login click previously waited without a bound if SDK init stalled.
Its 15-second wait now ends with a retryable message while sharing the unresolved
SDK init. The timed-out click cannot later call provider login; only a fresh
click can continue once the SDK becomes ready. Callback startup still preserves
the URL and uses the existing recovery screen instead of rendering over init.

Public LIFF configuration was checked without cookies/tokens: the existing
endpoint covers Atlas and includes openid/profile. A downloaded official SDK
2.31.2 completed init and generated the expected PKCE authorization request in
an isolated browser using static public fixtures; all provider navigation was
blocked before leaving the fixture. An anonymous read-only authorization request
accepted the Atlas return path. Supabase aggregate logs showed normal user-settings
responses, with no account/token contents queried. No provider configuration,
production account, grant, database, function or deployment was changed.

Seven unit regressions failed on the prior implementation (six handoff shapes
and the stalled click). Browser coverage checks URL ordering, explicit retry,
late completion, server-grant validation and existing guest/account isolation
on desktop, mobile Chromium and iPhone WebKit. Actual results are in the PR.
Primary review is recorded here; independent signoff and successful real-account
consent / physical iPhone return-to-PWA verification are not claimed.

## Current follow-up: free self-use PWA (2026-10-26)

The existing manifest already supported standalone display; installation was
undiscoverable, ordinary browser sessions could never authenticate, and an open
installed app had no deployment-version check. Settings now provide iPhone/iPad
Safari instructions, LINE-browser escape/copy guidance, explicit Android native
prompts with honest cancellation/completion state, and public build-version
checks. Safe-area-aware dialog bounds support portrait and landscape. Existing
manifest identity, scope, icons and the normal GitHub Pages deployment remain.

Browser/PWA LINE authentication is explicit. Guests neither load LIFF nor
persist application actions. Recognized callbacks await SDK initialization
before routing; a bounded recovery screen preserves the callback if it stalls.
Only the existing server-verified access policy enables device/cloud stores.
Signout invalidates retained token callbacks and isolates the workspace without
deleting saved account drawings; inside LINE it does not call unsupported SDK
logout. Fresh external launches require tapping login to recover the SDK
session and reverify permissions. The existing public access endpoint rejected
an invalid test token with HTTP 401; no production account or grant was changed.

Version metadata and HTML carry the same deterministic build fingerprint.
Checks use no credentials or app storage, reject other scopes/invalid responses,
and never automatically reload over drawing work. Explicit reload retains the
route and discloses that guest work is temporary. The screener keeps its worker
retirement and no-background-cache policy; current installability does not
require a worker. No offline-launch/new-market-data or background-alert promise
is made. There are no new paid services, packages or native toolchains.

Primary review accepted fixes for safe-area scrolling, non-blocking LINE timeout
recovery, unsupported inside-LINE logout and compact login explanation layout.
Read-only review found that LINE's user agent would restart a stalled login
after the guest-recovery link. An explicit RAM-only guest URL now suppresses
identity initialization and refresh until a new login click; it cannot grant
storage, and is removed from subsequent OAuth redirects.
The first full PR run found that the new 44px account actions reduced the
landscape chart below its existing minimum. Short landscape chart headers now
use smaller outer spacing, preserving the account touch targets and the
unchanged chart geometry assertions.
Tests cover platform instructions, native prompt outcomes, standalone detection,
icons, update/reload errors, guest zero-persistence, denial/mismatched grants,
callback ordering/cancellation/timeouts, token expiry/signout and restoring
saved drawings after a fresh launch and explicit reauthorization. Actual totals
and exact-head CI results are recorded in the PR. The runbook documents real
device installation and LINE-provider prerequisites; mocked WebKit is not a
physical iPhone installation or successful real provider login. No external
independent signoff, merge or deployment is claimed.

## Current follow-up: market charts, navigation recovery and concise analysis (2026-10-25)

Market index previews now use the report's configured SMAs at 2px, retaining
full-history warmup, and display an independent volume scale. Native pan/pinch
and explicit zoom/reset controls operate inside the card; dedicated links open
the full workspace. Passive stock previews and individual-stock indicator
defaults remain unchanged. Interactive market charts retain attribution.

The reported navigation blackout was not reproduced in a fresh desktop load of
the exact published assets fetched with TLS verification. A reproducible missing
lazy-bundle case is now handled by route boundaries: navigation stays mounted,
an actionable fallback replaces the failed page, and explicit reload retries
the current deployment. Hidden workspace failures remain inside the hidden host.
A real MarketDataProvider/MarketTiming import cycle was removed using leaf
timeframe metadata, preserving public exports. Production builds now reject
cyclic cross-chunk reexports; an in-memory restoration of the old cycle fails
that gate. This is an eliminated risk, not proof of the user's precise cause.

Commodities include genuine Binance spot BTC/USDT, with USDT quote currency,
BTC base volume and completed UTC daily sessions, including weekends. Its
permanent record and hash/pair-validated dated supplement are reviewed source
records; existing published pair snapshots remain authoritative. No BTC/USD
substitution, browser exchange dependency or source-archive rewrite is used.

Volume analysis presents directional ratios first, then centered anomaly
controls. The two volatility values are larger independent cards. Shared
explanations use compact collapsed controls; per-stat help sits beside the
metric title, and accounting notes follow the financial charts. Observation
dates, stale/unavailable states, errors and meaningful empty states remain
visible. Root accepted and corrected attribution, hidden-boundary containment
and explicit BTC unavailability handling during implementation review. Read-only
agent review also identified a malformed Binance row escaping the retention
path; row schema validation now converts it into a source failure, preserving
the existing CSV and candle snapshot. A regression replays the malformed row.

Focused browser regressions cover configured MA/volume painting, native chart
interaction, repeated navigation, missing chunks/reload, BTC source boundaries,
analysis order/centering, large volatility cards and note expansion by keyboard.
Actual test totals and exact-head Actions results are recorded in the PR.
This package records primary review and implementation-agent contributions;
it does not claim independent sign-off or physical-device verification. No
merge or deployment was performed.

## Current follow-up: retain valid market data during closures (2026-10-25)

The deployed Sunday report had an empty Taiwan full-market scan and zero-count
breadth. The daily CSVs had acquired market-wide zero-volume placeholders dated
2026-10-23, while real trading ended on 2026-10-22. Scanning applied its unchanged
volume floor to those placeholders; breadth selected their modal date; filter
metrics also consumed the extra bar. Full-universe data itself remained intact.

A shared read-only session projection now excludes weekends and broad,
known-zero market dates before scan, metrics, custom-group and breadth analysis.
Weekday inference requires independent histories and adequate recent coverage;
any positive or unknown volume prevents that inference. A halted individual
stock on an active market date is not rewound. Taiwan and US cohorts remain
separate. Raw caches, permanent archives, report volume thresholds, same-date
filter guards and public-owner permission checks retain their existing behavior.
Partial breadth refreshes preserve the other market's prior valid snapshot.
Scan groups display their actual observation date alongside existing metadata.

The recovery CLI prepares separate review files only. It validates retained scan
prices/volumes against current raw histories, requires the latest genuine
session, keeps original metrics, computes the newly added complete-window
average volume, and rebuilds breadth. The actual replay restores all 92 saved
Taiwan candidates without rejection and breadth of 1,949 stocks (MA20:
494/1,947 = 25.4%; MA60: 720/1,945 = 37.0%), dated 2026-10-22. Other markets and
groups remain byte-equivalent as JSON values. Source hashes and recovery
provenance are in `docs/closed-market-recovery-proof.json`; the browser fixture
contains a documented three-row real excerpt. Generated repair artifacts belong
on a separate PR targeting `generated`, never in the source PR.

Primary self-review checked sparse/unknown-volume data, long closures, individual
halts, cross-market isolation, missing-source retention, mismatched saved rows,
input immutability and the early public-owner privacy guard. The existing CSV
cleaners converted missing volume to zero; their analysis views now retain the
unknown-volume lineage before the closure inference, with an actual-CSV reader
regression. New browser cases
cover Sunday and long-closure clocks, breadth, candidates, average-volume and
prior-session-volume filters on desktop, phone Chromium and iPhone WebKit.
Actual verification totals and exact-head Actions results are recorded in the
PR. No independent review, physical-device verification, merge or deployment is
claimed.

## Current follow-up: index directional volume above market entry rates (2026-10-25)

Taiwan and US market pages now show an index-volume card directly above full-market entry success rates. The card defaults to the market's first configured index, offers every configured index in that market, and exposes the existing 5/10/20-session directional ratios immediately. It contains no abnormal-volume calculation or RVOL thresholds. Stocks retain their abnormal-volume controls and share the unchanged directional-ratio calculation and presentation.

The analysis and overview candles share one daily request, including its source/as-of/cache metadata. Calculations use each selected index's own completed daily bars, capped at both the current time and the response's original observation time so an old pre-close response cannot acquire a completed session later. Zero volume, invalid OHLC and unverified jumps over 40% break adjacent comparisons; flat prices and equal volumes retain the existing denominator rules. Missing directions are N/A. Index changes clear prior values while loading, failed requests can be retried, and source descriptions remain folded.

Regressions cover exact directional numerators/denominators, window boundaries, zero/invalid data, Taiwan and DST-sensitive US closes, stale pre-close responses, shared-request deduplication, retry recovery, correct card order, index/market isolation and three browser profiles. The primary engineer performs self-review; independent or physical-device sign-off is not claimed. Final validation belongs in the PR, which must remain unmerged and undeployed.

## Current follow-up: compact normal chart on phones (2026-10-25)

The valuation explanation is now a floating info control immediately to the right of the P/E values. K線, 財務, 波動率, 價量 and 建議均線 share one compact row; the volatility menu retains both existing calculations and their explanations. The chart's volume menu mounts the analysis directly, while financial-page disclosures retain their existing expandable presentation.

The primary engineer accepted the first CI finding that compact chart tabs must retain the existing 36px minimum touch target. Their outer padding is removed on phones, preserving the compact row height and the original financial-navigation assertion.

Chart-route layout allocates the remaining height after the account notice and any legacy-import content. The normal chart extends to the top of site navigation; market-source details are available from an info control beside refresh instead of reserving footer rows. Actionable legacy import prompts retain their readable minimum chart area. The engine and calculation/storage boundaries are unchanged, with resizing handled by the existing observer and public Lightweight Charts APIs.

Browser coverage measures button alignment and P/E help placement, verifies the real chart canvas resizes with its host, checks the normal chart's bottom edge against navigation, and exercises 320/375/390px portrait, landscape, resized viewports and safe insets. Opening either explanation must leave the plot height unchanged; opening 價量 must show results without another disclosure click. Existing stock switching, MA toggles, drawing and fullscreen regressions are included in validation. This follow-up uses primary-engineer self-review; no independent or physical-device sign-off is claimed. Final checks are recorded in the PR, which remains unmerged and undeployed.

## Current follow-up: visible monthly history and reversible MA choices (2026-10-25)

The existing generated report still had weekly cohorts and no monthly points. Full-market output and old full-market parsing now omit the weekly cohort. A real historical replay is committed in `docs/report/breakouts-backfill.json`, with its source and calculation proof beside it. The latest-report loader lazily bundles that record and supplements a missing history only when the market date and all parsed calculation parameters match. Live latest statistics, explicit newer histories (including gaps), and original saved reports remain authoritative. The recalculation time is disclosed in the existing collapsed explanation.

The replay validated 3,480,830 archived OHLC rows at archive commit `4c934296ac3bb1003d0d4a7f106d21a099cddb87`, restored 7,328 rolling histories, and fetched the complete official Taiwan and Nasdaq lists. It physically clipped Taiwan at 2026-10-22 and US at 2026-10-23 to match the existing report, then recalculated April through September plus the reference date with the unchanged engine and coverage gates. All fourteen market/date points pass 90% coverage: Taiwan 98.13–99.29%, US 96.07–98.60%. Each method has complete samples for all four outcomes. A separate input comparison confirmed that applying the existing main correction journal produces exactly the same 7,328 input frames; its sole quarantined session is already absent from the archive inputs. No source archive or production report was written.

Volume analysis is an independent chart-header control alongside ATR20%, average amplitude and recommendations. Official disclosures remain last and subdued inside recommendations. Candidate MAs now toggle on/off, preserving locked instances and other stocks/timeframes. Mobile analysis controls occupy their own row so all four controls remain reachable without covering the chart/financial tabs or bottom navigation.

Primary engineer accepted and fixed two review findings: an incompatible signal definition must never acquire the saved history (full parameter and box-threshold checks), and mobile controls must not overlap the stock tabs. Browser geometry assertions now reflect the accepted independent analysis row while retaining overlap and overflow gates. Source proof, immutable-input checks, shared-load/refresh/error handling, archive isolation, actual bundled historical values, individual MA persistence, and three browser profiles have regressions. Final checks are recorded in the pull request; no external sign-off, merge or deployment is claimed.

## PR 171 chart and research follow-up (2026-10-24)

This follow-up makes all moving-average candidates individually applicable, defaults newly added chart MAs to fixed 2px widths, and seeds fixed-stock editing from applied daily SMAs only. Existing explicit styles and stored preset data remain compatible. The unused preset UI is removed because indicators already belong to each stock/timeframe; the chart-local shortcut list is now labelled 圖表收藏, with its purpose explained separately from the condition-tracking console lists.

The two daily volatility values are independent controls next to 建議均線. Its floating menu contains the MA candidates, then volume analysis, and subdued official disclosures last; panels stay within the available viewport above navigation. Directional volume ratios compare actual adjacent session volumes while abnormal-volume RVOL20 remains unchanged. The backtest UI uses one SMA/EMA with independent upper-entry and lower-exit percentages, closed-bar crossings and unchanged next-open execution.

Full-market reports recalculate six prior monthly reference dates from cached bars, truncating the input at every date and retaining listing/coverage/sample rules. The UI replaces the near-week switch with monthly histograms, dated current values and a default five-month view. Missing historical coverage is a gap; older generated reports explicitly await the new monthly payload. Current listings imply survivorship bias, disclosed in the calculation note. Historical raw archives remain immutable. Financial column charts expose time zoom, pan and reset while preserving original values, gaps and YoY alignment.

Read-only peer findings returned to the primary engineer and explicitly accepted: support long Taiwan holiday closures when selecting prior reference sessions; include displayed volatility percentages in accessible button names; dismiss a sibling popup during keyboard focus navigation. Each fix has a regression. Verification results and the exact-head Actions run are recorded in the PR. No merge, deployment or production data collection is authorized by this change.

PR 169 had already been merged before this follow-up, so these changes are reviewed separately in PR 171. The broader drawing regressions also refresh two stale expectations from the existing readable-axis/gesture-status design: axis width follows the configured font size and still reserves most of the plot, and picker geometry compares the same active-tool state before and after opening.

The first exact-head CI run exposed moving world candle CSVs paired with frozen browser-fixture metadata. The fixture reader now clips retained real bars at the frozen data date and checks its terminal date/close; the OHLC assertion is unchanged. Real full-market monthly generation completed successfully in 13m31s (TW 1,945/1,981 and US 5,273/5,348 valid current-session histories), but the following research checks exhausted the old 15-minute job budget. That job now allows 30 minutes for the expanded workload with all coverage and result checks intact. Touched calendar filters use pandas' equivalent `day_of_week` property to avoid deprecated-alias warning spam.

## PR 169 follow-up: Pages disclosures and collector cache (2026-10-24)

The unmocked Pages smoke now expands the SEC 8-K coverage note before its existing visible `CIK 對應` assertion. The other smoke targets remain visible outside the collapsed provenance, stat notes and chart legends; their assertions are unchanged.

The optional daily watchlist job restores the newest `universe-full-v1-` daily-bar cache through `actions/cache/restore` only. The collector prefers `data/<symbol>.csv`, then reads the cache's `bars/<symbol>.csv` only when the primary file is absent, with the existing CSV parser and report-session alignment. Missing fixed references still preserve observations and now name the affected symbols. Filesystem-backed tests cover cache-only Taiwan and US stocks, excluding newer sessions, primary-source priority and both sources missing.

Local validation: 1,151 unit tests, lint, normal/Pages builds, 10 disposable PostgreSQL permission tests and all 10 offline integration suites (238 tests) passed. Browser and final CI outcomes are recorded in the PR. No production collector, deployment or merge is invoked.

## Atlas interface refinements (2026-10-24)

The requested twelve changes compact financials, remove automatic chart MAs, expose archived entry-success calculations, use 20-session average share volume with Taiwan lot display, align fixed-MA filtering across saved-list evaluations, and reduce permanent explanatory text. [Scope, review triage and data boundaries](docs/UI_REFINEMENTS_2026-10-24.md) document the final behavior. Review findings were returned to the primary engineer and explicitly accepted before implementation. Final verification is recorded in the pull request; no external review, deployment, merge, or physical Safari sign-off is claimed.

## Current review: account storage authorization (2026-10-23)

Guests and ungranted LINE users can use chart drawing, groups, stock MAs and lists in memory without app-managed persistent writes. The verified owner alone can choose none/device/cloud grants; owner identity is enforced by the server. Device mode scopes all personal storage and disposable caches by account and mode. Account/policy changes replace the workspace and cancel stale operations. No shared legacy record is automatically adopted, migrated, deleted or uploaded. Both Edge Functions and SQL enforce cloud permission, and scheduled consumers reject ungranted private operations and other-account public artifacts. The bounded policy table is changed only by explicit owner actions, never by login. A device-mode owner can still administer grants. Mode-specific notices and non-cloud list renaming preserve usable guest/device behavior.

The PR also provides an explicit one-click legacy import, so upgrading requires no manual export backup. After server verification, device/cloud users can inspect source counts on the console or chart page and choose Import or Not now. Legacy IndexedDB is read without an upgrade; known unscoped localStorage settings are validated separately. Import merges missing items into the current account/mode and keeps the account version on conflicts. Source records remain untouched for retries or a second account, while per-account/browser import or dismissal markers suppress repeated prompts. The console storage notice can show the prompt again. Guests/none never inspect or import legacy data; cloud copies use the existing server-checked sync. Authentication, cache and old sync-binding metadata are excluded. An account load/validation failure blocks legacy merging rather than treating unread account records as absent.

Legacy-import verification: clean `npm ci`, full Vitest 96 files / 1,133 tests passed; the subsequent failed-account-load guard and coordinator/sync regressions passed 43 focused tests. Typecheck, lint, normal and screener Pages builds passed. The legacy import and account privacy browser selection passed 33 tests, plus 3 additional server-only cloud drawing cases: 36 passed, zero failed, across desktop Chromium, iPhone-size Chromium and iPhone WebKit. Tests cover v1–v4 read-only source migration, no automatic import, guest/none source-read denial, current-account conflicts, repeat imports, another account on the same browser, markers/reopening, grant changes and in-flight cancellation. Phone prompt screenshots were inspected for readable counts/buttons and chart visibility; this is not a physical-device sign-off. Final exact-head CI results are recorded in the PR.

Implementation boundaries: [ADR-016](docs/architecture/ADR-016-account-storage-authorization.md); [deployment, retention and management instructions](../docs/ACCOUNT_STORAGE_PERMISSIONS.md). Production SQL, both functions and service deployment have not been executed. Do not merge or claim the public website is updated. Device data cannot be remotely erased; no claim is made about browser-owned history/HTTP cache or LINE infrastructure logs.

Verification checkpoint after integrating main: 1,062 Vitest tests passed, including actual Edge handlers, account access/expiry and persistent storage separation; the three added worker-retirement tests also passed. The full Python CI suite passed 1,601 tests. Disposable PostgreSQL 17 integration passed 10/10, including revoke/write serialization and direct-table privileges. The initial focused desktop Chromium, iPhone-size Chromium and iPhone WebKit run passed 27/27. Typecheck, lint, normal and screener Pages builds passed. Final exact-head CI and complete browser results are recorded in the PR. No independent-model review or physical-device sign-off is claimed.

The broad browser run exposed a pre-existing race: a hidden lazy panel assertion succeeded before its chart header mounted, then its synchronous size measurement read a null element. The test now explicitly waits for the header and valuation before retaining the same size assertions. Cloud-chart mocks also intercept drawing requests so test tokens never reach production. A blocked CacheStorage/unregistration cleanup now allows the RAM workspace to render; unit coverage checks both denied cleanup and preservation of unrelated app caches.

The full site sweep also reached a previously unselected heatmap long-press test that called Chromium-only CDP in WebKit. Chromium retains its native touch dispatch; WebKit now exercises the DOM touch-pointer sequence and release-click suppression with the same tooltip, URL and no-dialog assertions. This is not a physical WebKit touch-hold verification. Heatmap regressions are now included in the PR workflow rather than skipped.

Earlier validation corrected an existing world candle/CSV mismatch using all 11 actual sources dated 2026-10-22. The subsequent main generated-branch cutover now owns those regenerated snapshots: this branch follows main and does not reintroduce removed generated copies. Retained world CSV records remain on main; current generated snapshots and offline fixture readers are validated by CI. No K-line history was removed by the legacy import.

## Integrated main: generated-branch PR 2 (2026-10-23)

Generated report copies leave main while source, retained records, old LINE PNGs
and history remain. Unit and browser research consumers now use frozen source
excerpts and retained world CSVs; CI additionally validates restored live inputs.
See [scope, provenance and Pages byte proof](../operations/generated-branch-pr2.md).
No UI, chart engine or generator behavior changes. Local/CI results are recorded
in the PR; no independent review or physical-device Safari sign-off is claimed.

## Previous review: compact chart header (2026-10-23)

The volatility row, its explanation, and the recommended-MA row and summary added five rows under the P/E and shrank the phone chart to about 260 px. `ui/StockStatsMenu.tsx` replaces them with one "波動・均線" button (beside the P/E on desktop; on the existing K線／財務 row on phones via a new `stats` grid area) whose panel floats over the chart and closes on Escape or an outside tap (dialogs opened from it do not close it). Both components stay mounted while closed, so data loads as before. Measured with the site e2e mocks: phone chart 260 → 353 px, desktop 553 → 616 px. `MaRecommendation` no longer has its own disclosure and now shows each pick's structural score and every candidate's score, with a note that the starred pick is chosen by the reversal-return gate and is not always the highest score.

Local verification: typecheck, lint, production and Pages builds passed; Vitest 83 files / 980 tests passed. Playwright desktop and phone Chromium for the full `atlas-review.yml` site selection: 149 passed, 1 pre-existing skip. Specs were updated for the new interaction (open the menu first; close it before tapping the toolbar it covers on phones) and assert the button shares an existing header row with no horizontal overflow; no assertion was removed. WebKit runs in CI only. No physical-device or independent review sign-off is claimed.

## Current review: ma-selector recommended moving averages on the chart (2026-10-22)

The chart header shows ma-selector's weekly recommended SMA for the symbol (short 15–33, medium 34–45, long 46–110) from `report/ma-selector/<SYMBOL>.json`, written by the new `ma-recommendations.yml` workflow from the public ma-selector repository (its production selector, unchanged; companion branch `claude/atlas-integration` there adds the batch script, `?ticker=` prefill and a link back to this chart). "套用到圖表" adds only the missing picks as daily SMA indicator instances (by id, never MA1/MA2) and switches to 1D; existing indicators, locks and other timeframes are untouched. "固定為個股參數" reuses the console's `FixedStockParameters` dialog (new `prefill` option keeps the picks in the input and shows the saved values separately) and saves `ungrouped` through the existing LINE-verified `stocks.save`. Missing files, SPA fallbacks and HTTP/network errors show a link to compute the symbol live in Streamlit instead; no values are invented.

Local verification: typecheck, lint and production build passed; Vitest 79 files / 945 tests passed (5 new). Playwright desktop and phone Chromium: the new `ma-recommendation.spec.ts` (apply, timeframe switch, fixed-parameter save payload, missing state, no horizontal overflow) plus stock-volatility, parameter-console, unified-console and site specs passed (52 cases). WebKit was not run locally; the spec is added to the `atlas-review.yml` selection. The weekly workflow has not run yet, so no live recommendation file exists until it is dispatched after the ma-selector branch merges. No physical-device or independent review sign-off is claimed.

Review scope: `src/report/maRecommendation.ts`, `src/ui/MaRecommendation.tsx`, `src/ui/FixedStockParameters.tsx`, `src/app/App.tsx` (one line), `src/ui/workspace.css`, `tests/ma-recommendation.test.ts`, `e2e-site/ma-recommendation.spec.ts`, `../.github/workflows/ma-recommendations.yml`.

## Current integration review: PR #148 with daily volume (2026-10-21)

Merged main `6b348b69758a330404b8bc73e03909d41c6803aa` into the composition branch with a merge commit, retaining PR #149's optional daily share-volume filter and all repeated quarterly/annual EPS and revenue conditions. `vol:N` is serialized alongside every fundamental token, evaluated inclusively with AND, and preserved by URL sharing, separate TW/US memory, local saved strategies and cloud validation. Draft volume-chip removal clears its custom input; Reset clears both volume and growth editor state.

CI run 39563277621 / job 119099356068 failed the same short-viewport Apply regression on desktop Chromium, phone Chromium and iPhone WebKit (152 passed, 3 failed, one pre-existing skip). The failure was reproduced locally: the non-shrinking navigation and selected-chip area exhausted the 280px viewport and pushed the footer below it. The new flex content wrapper lets navigation, chips and conditions share scrolling when the visible viewport is short, while the header/footer remain fixed. Short mode follows visualViewport height, including keyboards that do not resize the layout viewport. No existing test was removed, skipped or weakened.

Added combined-filter regressions cover an exact 1,000,000-share boundary, below-threshold and missing volume, each independently failing annual EPS gate, missing-data policy, independent chip removal, route round trips, fresh-context shared links, separate market memory, saved-list reload/evaluation, reset, and cloud strategies longer than 300 characters. All four repeated quarterly/annual EPS/revenue gate types are checked with volume. Browser coverage also asserts the complete Apply button fits a 280px viewport with stacked conditions and simulates a keyboard shrinking only visualViewport.

Local verification: 940 unit tests passed; lint, production and Pages builds passed; all five CI offline Python integration suites passed (9 + 38 + 6 + 22 + 18 cases). All nine final focused browser cases passed: combined filters, the unchanged short-viewport regression, and the visualViewport-only keyboard case, each on desktop Chromium, phone Chromium and iPhone WebKit. WebKit was downloaded successfully through its official fallback URL; its missing system libraries were extracted under /tmp and its downloaded launcher preserves LD_LIBRARY_PATH. These are local environment setup only, with no CI or browser-test exclusions. The full CI browser selection and fundamental regressions are also checked separately. No physical Safari or independent reviewer sign-off is claimed.

## Original composition review

The filter drawer uses orange accents, category navigation, removable draft chips and growth-condition cards. EPS and revenue gates can each have multiple quarterly or annual instances, combined with AND. Repeated `eps`, `epsy`, `rev`, and `revy` URL tokens preserve these instances. Removing the first instance promotes the next instance of that metric; saved single-condition strategies remain compatible. Malformed growth tokens are ignored without erasing valid conditions.

`acc:1` retains quarterly EPS acceleration; `accy:1` selects annual acceleration. Both tokens require both periods to accelerate. Each compares the latest two consecutive YoY slots for its period, with the existing turnaround and missing-data policy and no cross-period fallback. Row detail tooltips include every stacked gate. The existing EPS streak is kept in a collapsed advanced section: unlike an annual gate, it requires YoY strictly above zero and stops at missing years or flat/declining EPS.

Filter limits increase from 300 to 2,000 characters consistently in routes, local/saved-list validation and the user-settings Edge Function. Existing persisted strings require no migration. Cloud support for longer strategies requires deploying the changed Edge Function. No deployment is claimed.

Original PR verification before integration: clean `npm ci`, full unit suite (928 passed), production build and lint/typecheck passed. All 38 desktop/mobile Chromium filter cases passed, including no horizontal overflow, annual-vs-quarterly acceleration, stacked EPS/revenue rules, preview counts, reload, per-instance removal, draft cancellation and reset. After the final extra-gate input correction (clearing a threshold keeps its card available for editing), all 8 focused desktop/mobile regressions passed and lint/production build passed again. WebKit could not be installed because the managed outbound policy rejects both official download hosts with HTTP 403 (Domain forbidden). No Safari or independent review sign-off is claimed.

Review scope: `src/pages/screenerFilters.ts`, `src/pages/screenerItems.ts`, `src/pages/ScreenerPage.tsx`, `src/ui/FilterSheet.tsx`, `src/ui/filter-sheet.css`, `src/app/routes.ts`, `src/watchlists/model.ts`, `../supabase/functions/user-settings/watchlists.ts`, and the screener unit/browser regressions. Confirm AND behavior, independent annual/quarterly acceleration, per-instance removal, URL and saved-strategy restoration, draft cancellation/reset, missing data, turnaround handling and phone overflow.

## Integrated review: optional daily share-volume threshold (2026-10-21)

Daily collection does not filter the Taiwan ticker list by traded shares, so low-volume and zero-volume CSVs continue updating. After all CSV downloads/updates, the daily LINE report's Taiwan `tw_all` MA20 list requires the latest bar's volume to be at least `TW_MIN_VOLUME = 1_000_000` shares. This report-only threshold applies to both `.TW` and `.TWO`; the US S&P 500 MA20 list has no volume threshold. The full-market screener data remains unrestricted by volume, and its optional page filter is unchanged.

The screener's 「每日成交股數」 condition defaults to unrestricted. Presets and a custom non-negative integer input use shares in both markets (1 Taiwan lot = 1,000 shares). A positive threshold is inclusive; empty/zero clears it. The `vol:N` URL token uses the existing per-market filter storage, chip removal, reset and sharing paths. Missing share counts are excluded when an available threshold runs; groups without share data show the existing unavailable-condition treatment. Invalid custom inputs block Apply.

Report-only follow-up regression coverage confirms new and existing low-volume listed/OTC stocks are downloaded and written to CSV while excluded from `tw_all`; exactly 1,000,000 shares qualifies, earlier bars do not determine the threshold, and low/zero-volume US stocks still match the MA20 report. README and universe metadata distinguish unrestricted collection from the Taiwan LINE report's minimum. Full Python verification: `python -m unittest discover -s tests -v`, 1,354 tests passed in 134.863 seconds. The screener-page source, frontend tests and browser tests were not changed by this report-only follow-up.

Verification: `npm ci` from the existing lockfile/cache; 920 Vitest tests passed against latest main; lint and TypeScript checks passed; normal and Pages builds passed (`VITE_PUBLIC_BASE=/my-stock-screener/atlas/`). Python collection/metrics/full-universe regressions: 88 passed. Final browser cases: 6 desktop/mobile Chromium plus 3 iPhone WebKit passed. Reviewed screenshots in `.cache/volume-filter-screenshots/` confirm the custom input and preset layout fit the mobile sheet. Earlier browser attempts exposed incorrect test expectations for matched/unmatched counts and filtered totals; corrected fixtures/assertions were rerun. WebKit required locally extracted libraries and a downloaded launcher adjustment to preserve `LD_LIBRARY_PATH`; these are environment setup only. Browser tests use synthetic data and do not establish physical-device behavior or live full-market refresh performance.

The changes are based on main `674bdaa77`. The original audit recovered 23,211 bars in the older workspace; after retaining newer main data, this PR inserts 16,606 bars into 630 CSVs, preserves all existing main rows, and does not recreate 31 files removed by main. The report records both the prior audit summary and the integration baseline. Backfill and OHLC cleanup regressions add 6 passed Python cases to the 88 collection/metrics/universe cases above.

Independent review is unavailable; no independent sign-off is claimed. The public site will receive the selector when the normal publishing workflow runs.

## Previous review: three entries and four success rules (2026-10-21)

User authorized updating PR #147, including the earlier ATR20%/mean-intraday-range feature, the reviewed substantial swing definition, three entry methods, four success rules and a styled comparison panel. Do not merge or enable auto-merge.

Swing waves require a close rebound of 2 × Wilder ATR20 from a trough, followed by a close reversal of 2 × the extreme-date ATR from the highest wick. Distances are frozen at extrema and confirmations become usable next bar. Sideways micro-pivots do not replace a reference. A strict intraday break is recorded as an attempt. A close below key + 0.1 × Wilder ATR14 fails all four outcomes but keeps the same wave high armed; later days may retry. A qualified close consumes that high. Frozen real-data regressions include MU's failed September 21 / retried September 22 entry, 3230, 3287, VEEV and 2305. No complete historical Livermore/1-2-3 system is claimed.

All methods share one five-complete-market-session cohort (D+1 through D+5). Rule 1 survives key −5%; rule 2 exceeds the signal-day high on D+1; rule 3 resolves the FIRST stop/target/next-day-high event, or succeeds after five-day survival; rule 4 requires target +5% AND next-day higher high, without an added −5% veto. Signal-day close failure overrides all. Opening gaps establish known order; otherwise opposing events on the same daily candle conservatively lose. Resolved first-event outcomes never change with later price action. Pending/missing observations never become failures. Legacy as-of-profit fields are retained for compatibility and never shown as the four new rates.

The market panel has a four-choice outcome selector, three method cards, exact numerator/cohort counts, pending/missing counts, and a collapsible 3×4 comparison matrix. It supports keyboard/native button interactions, phone targets, horizontal overflow containment, empty/pending/partial/legacy/error states and a short shared-failure explanation with detailed methodology. Old report versions wait for recomputation rather than relabelling earlier rates. Preview images use the production pivot trace and explicitly shade missing stock candles; guessed fixed-offset pivot dates are not used.

The full-market builder still restores complete exchange lists/histories, never shrinks its denominator to local CSVs and preserves the previous file if coverage is insufficient. PR CI recomputes a real artifact read-only; the resulting verified JSON is committed separately. Local subset trials are never published as full-market results. No production account, database, cache or notification writes are made.

Local verification: full Python suite 1,395 passed, including 84 focused breakout checks (38 shared/legacy, 6 full-market, 22 wave, 18 new outcome cases). Lint, typecheck and both builds passed; 20 desktop/phone Chromium breakout and volatility cases passed. All 927 frontend tests passed on the final full run; final CI results are recorded in the verification file and PR description. No independent reviewer or physical Safari sign-off is claimed.

Verified data: the real report from read-only run 39568611989 / job 119116912119 restores cache universe-full-v1-39433983829-1, with the artifact digest and full 3×4 results in the verification record. Both markets are dated 2026-10-19, with TW 1,948/1,979 and US 5,282/5,347 valid/listed stocks. Real-statistics desktop/phone renders match the artifact and have no runtime errors or horizontal overflow.

## Previous review: daily volatility and breakout entry methods (2026-10-20)

Individual-stock chart headers show Wilder ATR(20)% and the mean of the last 20 daily (high-low)/previous-close percentages below P/E. They request daily bars through the selected normalized provider independently of the chart interval, exclude unfinished sessions, require 21 valid bars and use the last daily close for ATR%. Symbol/provider/reload identity and cancellation prevent old values leaking into a new request. Expandable help shows formulas, the data date, cache status and price-basis limitations. No chart engine or drawing model changes.

Markets displays confirmed swing-high breakout, 20-day box breakout, and EMA20 upward crossing. Swing highs use a two-left/two-right local turn (rightmost equal high on plateaus), confirmed only after the right candles close and eligible from the following bar. The latest confirmed swing high persists until another pivot is confirmed; it never rolls off at an N-day boundary. A strict intraday break triggers even if the close falls back below the reference, without volume, a higher-low/full-123 requirement or a ten-day cooldown. Each pivot contributes only its first break; separate pivots can contribute multiple events in a window. The 20/60/120-day selector and rolling-high substitution are removed.

Swing stop-entry is approximated by max(open, pivot): a gap cannot fill at the lower old reference, and a failed intraday break cannot fill cheaply at that day's close. These are daily-bar research assumptions, not verified tick fills; no fees/slippage/minimum price increments are simulated. Box/EMA retain close entries, their existing cooldown and first-event policy. All rates require three trading days of maturity and mark to the report-date close. Full-market reports publish exactly these three methods; legacy N-day results remain in the internal engine tests only and are never relabelled as swing statistics.

All three methods now scan the full-market universe: Taiwan listed/OTC ordinary shares and US exchange ordinary shares/ADRs, using the existing universe-full listings and completed daily-history cache. Entry success is profitable mature signals divided by mature signals. The separate market up-rate and comparison are removed from the UI. Coverage reports the entire listing, loaded/missing histories, current valid bars and stale/non-trading stocks; per-method history eligibility is shown separately. No signal, unavailable history and unmatured signals are not failures. Legacy engine benchmark fields remain for compatibility but are not shown.

The full-market offline builder never shrinks the universe to available CSV names or falls back to S&P 500. It requires fresh complete exchange lists and at least 90% valid daily-bar coverage before atomically replacing the report; otherwise it preserves the prior file. Legacy partial-market reports are hidden. Production report generation moves to universe-full.yml after full-cache refresh, and the old limited-universe schedule step is removed. PR verification restores the default branch's existing cache read-only, builds the real full-market report and uploads evidence; it does not update the cache, production data or notifications.

See [calculation and verification record](docs/VOLATILITY_BREAKOUT_VERIFICATION.md). Independent review and physical Safari verification are not claimed. Submit for PR review only; do not merge or enable automatic merging.

## Historical review: parameter console follow-up (2026-10-20)

Selected watchlists now auto-save authenticated settings and delete cloud-first through a serialized per-list queue. Permanent stock MAs are dormant ungrouped stocks records; they survive list/member removal and only override an active member's defaults. The daily report and breakout consumers both exclude dormant rows. The Edge Function requires ATLAS_FIXED_PARAMETERS_ENABLED=1 after consumer deployment to avoid old monitor builds treating the new reserved group as active.

The parameter list restores search and group filters with an approximately five-row scroll area. Native edit/delete dialogs keep errors visible and provide confirm/cancel. Review [ADR-015](docs/architecture/ADR-015-fixed-stock-parameters.md) and the rollout in [UNIFIED_CONSOLE.md](docs/UNIFIED_CONSOLE.md). Actual offline test/CI results are recorded in the follow-up PR. Production deployment, real-account acceptance, physical Safari and independent review remain unverified.

## Historical review: unified console extension (2026-10-20)

The user explicitly authorized the unified parameter console, multiple saved screening/manual lists, last-opening membership comparisons, optional Supabase persistence and existing breakout monitoring, quarterly US statement presentation, peer comparison, rotation tracking, health disclosure and the reachable Apply action. See [implementation and deployment checklist](docs/UNIFIED_CONSOLE.md) and [ADR-014](docs/architecture/ADR-014-unified-console.md).

Primary review covers source completeness, tenant-scoped API access and optimistic concurrency, separation of daily observations from last-opening baselines, failed-data preservation, quarterly/annual navigation and source/null integrity. Browser regressions detected duplicate sibling React keys for the financial header/peer section when switching symbols; the peer key now has its own prefix. Tests also cover short viewports and the fixed Apply footer. New list storage does not replace existing chart IndexedDB. Independent review and physical Safari UAT are not claimed.

Supabase migration/service activation, true-account cross-device verification and real notifications remain pending. No live database writes or notifications were performed. This branch is for PR review and must not be merged automatically. The final verification record is maintained in UNIFIED_CONSOLE.md.

## Historical review: SEC bootstrap and Taiwan public verification (2026-10-08)

**Status: engineering, deployment, and public verification complete; physical-device UAT and independent review remain pending.** Commit [3feb6337aeab4e686c0f61e259bece7250be3d40](https://github.com/wudn9922/lightweight-drawing-lab/commit/3feb6337aeab4e686c0f61e259bece7250be3d40) completed Actions run [37785146220](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37785146220) at 2026-10-08T13:36:11Z. TLS HTTP verification passed at 13:38:04.608649+00:00; public iPhone WebKit passed at 13:37:56.193Z. Evidence: [HTTP](docs/taiwan-financial-live-http.json), [browser](docs/taiwan-financial-live-browser.json). All 16 seeded SEC packs are publicly validated; NVO/QQQ/SPY/TSM remain unavailable after SEC 403/cooldown, with no fresh runner data claimed. The latest hosted 26-symbol market refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Retained market packs keep their original observation dates and are not current-quote claims. XOM's market-pack status is separate from its SEC seed (two quarterly, zero annual facts).

Primary accepted and closed LIVE-DOC-01/02: the older picker deployment is now identified as historical in README, and the latest hosted market-refresh counts and retained-stale limits are stated separately from the earlier local snapshot check and SEC seed statuses.

The versioned seed set under `data/financial-snapshots/` has 16 normalized packs: AAPL, MSFT, NVDA, TSLA, AMD, META, GOOGL, AMZN, SMCI, NFLX, JPM, WMT, XOM, AVGO, PLTR, and MU. Four other allowlist entries may remain unavailable. XOM uses current CIK `0002115436` and has two quarterly, zero annual facts. Seed data is used only when cache is missing/invalid or has strictly older `fetchedAt`; all original timestamps, nulls, and audit metadata are preserved. Aged cache is marked stale, refresh is attempted after 24 hours, and failed refresh retains valid data. Seed-write errors are isolated by issuer. Local mocked-403 proof: 16 seed-backed `fresh`, NVO/QQQ/SPY/TSM unavailable, four SEC calls, seed timestamps unchanged. No User-Agent/IP rotation or proxy workaround is used.

Latest checks: 225 unit tests across 29 files, lint, typecheck, normal/Pages builds, targeted collector/provider unit suite 18, normal browser 126 passed / 10 original skips, and Pages browser 32/32 passed. Combined browser result is 158 passed / 10 original skips. Source review covered 269 files. HTTP validation passed every asset byte comparison, font license, and all 16 pack schemas/audits/sources/nulls/original timestamps. Public iPhone WebKit passed with zero API/runtime errors: TWD/native Taiwan weekly/monthly values; AAPL quarterly and annual Revenue audit UI; three timeframe-owned locked SMAs and a future horizontal drawing across persistence/reload/export v4. Raw Revenue and derived FCF provenance passed HTTP pack validation; WebKit did not assert FCF UI. Root inspected public screenshots; chart values including 2550/2600 and date labels appeared coherent. BOOT-01/02 are accepted and closed. Next: open the existing [Atlas public URL](https://wudn9922.github.io/lightweight-drawing-lab/) and try Financials and Taiwan flows; reopen if cached, but do not clear site data. Gemini/Claude review and physical-device UAT remain pending.

## Completed review: drawing picker and ATR stroke defaults (2026-10-08)

Status: **Implementation, local verification, deployment and remote browser verification complete; independent review and physical-device UAT pending.** Source commit [0e4317a108fb9e6acc4b688355d2c55ac7dbaaf1](https://github.com/wudn9922/lightweight-drawing-lab/commit/0e4317a108fb9e6acc4b688355d2c55ac7dbaaf1) was published non-force to `main` with 217 reviewed tree entries (45 changed files). [Actions run 37741318516](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37741318516) succeeded with build and deployment at 2026-10-08T07:08:24Z. TLS-verified HTTP asset/hash checks match the tested Pages build; see [HTTP evidence](docs/picker-atr-live-http.json). The public iPhone WebKit smoke passed at 2026-10-08T07:09:15.433Z; see [browser evidence](docs/picker-atr-live-browser.json).

Frozen implementation removes the left rail in favor of a 44×44px outside-plot toolbar launcher with an icon and “繪圖”, plus a categorized modal for all ten tools. At widths up to 1099px, the duplicate delayed-snapshot CTA is hidden and the provider selector remains visible. Existing Select/Pan, magnet, history, zoom, future-area and reset actions remain available. Price-axis minimum width is 40px. New drawings and SMA/EMA use Wilder ATR(14) × 0.02 in price space, converted through public price-scale coordinates; legacy/custom widths and locks remain fixed, pixel fallback is retained, and Volume remains pixel-based. IndexedDB/settings v4 carries optional `widthMode`; market snapshot schema stays v3. See [ADR-012](docs/architecture/ADR-012-drawing-picker-and-atr-strokes.md) and [the verification checkpoint](docs/DRAWING_PICKER_ATR_VERIFICATION.md).

Primary accepted and fixed P2-PICKER-01–05: pointer tracking cleanup, Volume 2px default restoration, owned modal/gesture Escape handling, bounded category scrolling in short landscape, and cancel-tool-before-fullscreen-exit ordering. Sol's read-only review confirmed all five closed. The price-domain interpretation is frozen by primary; no user clarification blocks implementation.

Current checks: clean `npm ci`, 178 unit tests across 23 files, lint, typecheck, normal and Pages builds PASS. Latest normal output is `index-BboRNSm3.js`, Pages output is `index-CgkbpnRO.js`, and CSS is `index-C_ebhjyC.css`. Targeted picker browsers passed 24/24 across all four profiles in 1.9 minutes. The first full 132-case run stopped on stale exact Width selectors after SMA24/EMA56 accessible names changed. The second full run was interrupted by a mobile UAT assertion expecting always-visible Zoom buttons from the removed rail; runner 79856 exited 130. Primary accepted a test-only branch that checks desktop's 44px external geometry and mobile's picker-scoped 44px controls outside chart DOM, dismissal, hidden-when-closed state and stable chart height. Numeric volume/legend/reload assertions and all skips are retained; no product regression or source change is claimed. Focused all-profile mobile-uat and v1-journey coverage passed 20 cases across four profiles in 3.0 minutes; Sol accepted the adapted tests without weakening coverage. Final normal browser gate: 122 passed / 10 original expected skips / 0 failed in 12.0 minutes (`/tmp/atlas-picker-browser-final2.log`, session 3541, exit 0). Final Pages browser gate: 20 passed / 0 failed in 1.7 minutes (`/tmp/atlas-picker-pages-browser-final.log`, session 24739, exit 0). Combined: 142 passed / 10 original expected skips / 0 failed across all four profiles. TLS-verified HTTP assets match the tested build. The public iPhone WebKit smoke passed at 2026-10-08T07:09:15.433Z: six categories/ten tools, ATR settings and IDB reload/export v4, SMCI/NFLX delayed quote and 1D/1W/1M alignment, locked MA and monthly-future drawing reload, symbol/timeframe ownership, zero API requests and runtime errors. Root visually reviewed fresh 44px mobile chart/picker screenshots and marked PASS. Evidence: [HTTP](docs/picker-atr-live-http.json) and [browser](docs/picker-atr-live-browser.json). Independent Gemini/Claude review and physical iPhone/iPad UAT remain pending.

Next: the user opens the same public URL and tries the categorized Drawing Tools picker, ATR stroke settings/defaults, and compact mobile controls. Existing saved widths/settings are not forced to ATR; choose ATR in a drawing or SMA/EMA setting to try it on an existing item. Close old tabs and reopen if cached; do not clear site data. Independent Gemini/Claude review and physical iPhone/iPad UAT remain pending. No V2 work is authorized.

## Completed review: current-period quote consistency correction (2026-10-08)

Status: **Current-price extension implementation and verification 100% complete.** Primary triage and Sol read-only review closed P1-MONTH-01, P1-SCOPE-02, P1-CURRENT-03, and P2-CURRENT-04. Corrected source commit [ee10f6743e92de8cb1eb9bf994ce26235eb3eac0](https://github.com/wudn9922/lightweight-drawing-lab/commit/ee10f6743e92de8cb1eb9bf994ce26235eb3eac0) was published without force to `main` (205 files); [Actions run 37711284860](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37711284860) succeeded and deployed it. TLS HTTP asset/hash verification passed; the iPhone WebKit profile passed at 2026-10-08T01:18:28.720Z. Records are [here](docs/timeframe-price-live-http.json) and [here](docs/timeframe-price-live-browser.json). Quote-to-current-bar checks and the listed app journeys passed. Physical-device UAT and independent Gemini/Claude review remain pending; automated WebKit is not physical Safari. Do not start V2.

P1-CURRENT-03: the previous live SMCI quote was $44.94 as of 2026-10-07 20:00 UTC, while native current 1M remained at $43.46 from 2026-10-06. The accepted source fix heals only a validated missing latest daily close from matching metadata, requires fresh daily date/close agreement, and rebuilds only current 1W/1M buckets from validated daily OHLCV. It preserves native history and records provenance. Schema v3 rejects latest 1D and current 1W/1M quote/date/bucket mismatches; failed period guards omit that interval, while a missing/inconsistent fresh daily result preserves the prior whole snapshot. Sol's read-only review closed this source finding.

P2-CURRENT-04: primary accepted and fixed the stale cached-daily race. A fresh native period response triggers a single 1D10y retry only when the helper identifies older cached daily metadata; the same timeout signal is retained, raw cache is replaced, and unrelated helper failures do not retry. The focused proxy middleware regression passes.

Local gates: clean `npm ci`, 167 tests across 20 files, lint, typecheck, normal and Pages builds all PASS. Normal browsers: 98 passed / 10 original expected skips / 0 failed (12.2 minutes). Pages browsers: 20 passed / 0 failed (2.1 minutes). Combined: 118 passed / 10 expected skips / 0 failed. All 20 local schema-v3 packs have all seven supported timeframes. Refreshed SMCI daily/1W/1M close is $44.94, with volumes 90,284,251 (1W) and 161,285,951 (1M); refreshed NFLX close is $69.70, with volumes 102,053,115 (1W) and 182,309,915 (1M). These are refresh observations, not fixed future prices. Normal and Pages entry assets are `index-DV6Ur9iz.js` and `index-B36DL1MA.js`; chart/storage chunks are unchanged. The first four Pages failures were test-only: an older filtered NFLX monthly fixture omitted October 1/2. The fixture builder now uses actual current daily/weekly/monthly captures and provenance; assertions were preserved and no product guard was loosened.

Live smoke at 2026-10-08T01:18:28.720Z loaded same-origin delayed Yahoo snapshots. SMCI quote $44.94 and NFLX $69.70, as of 2026-10-07 20:00 UTC, matched the latest 1D/1W/1M close. Current period OHLCV, including weekly/monthly volume sums, is recorded in the browser evidence. The smoke also passed timeframe-specific SMA and drawing isolation, locked MA reload, monthly-future drawing locked reload, and settings export v3, with zero API requests or runtime errors. HTTP asset hashes matched the tested production build. The private smoke harness was corrected to capture the persisted expected snapshot after locking and keep gestures inside the plot; these were harness-only adjustments, and application source did not change after the passing gates.

Next: user opens the existing [public site](https://wudn9922.github.io/lightweight-drawing-lab/), selects Yahoo delayed data for actual prices, and tries the intended flows. Close old tabs and reopen them if the prior app version is cached; do not clear site data. Physical iPhone/iPad UAT and independent external review remain pending. Do not start V2.

# Atlas V1 independent review package

## Static SEC snapshot and Taiwan market review details (2026-10-08)

**Status: deployed and publicly verified with partial coverage.** Earlier Actions run 37764054644 had SEC company-ticker 403/cooldown and AAPL 404; the current deployment serves the 16 versioned seed packs. The four unseeded symbols remain unavailable after SEC 403/cooldown, without a fresh runner-data claim. Independent Gemini/Claude review and physical iPhone/iPad UAT remain pending; this package is not external sign-off.

### Frozen decisions

- SEC collection reuses the current `FinancialNormalizer` and preserves its quarter derivation, EPS rules, nulls, and raw/derived provenance. Normalized snapshot schema v1 is separate from settings/export v4.
- Generated output and status manifest under `public/financial-data/` are Git-ignored; normalized public seed packs under `data/financial-snapshots/` are intentionally versioned. A valid seed is used only when cache is missing/invalid or seed `fetchedAt` is strictly newer; timestamps, nulls, and raw/derived audit metadata remain intact. Aged cache is marked stale, refresh attempted after 24 hours, and prior valid packs retained on failure. Browser cache is bounded/disposable and validated.
- Per-symbol SEC outcomes are explicit (`fresh`, `retained`, `unavailable`, `unsupported`). A partial failure keeps any valid prior pack and must not block price deployment. The client uses the fixed truthful project identity; no User-Agent/IP rotation or proxy workaround is allowed for the observed SEC 403.
- Financial collection filters the mixed market-symbol list to its 20 US-profile tickers; Taiwan symbols are excluded from SEC requests and manifest. Keep six Taiwan market snapshots: `2330.TW`, `0050.TW`, `2317.TW`, `2454.TW`, `6488.TWO`, `8069.TWO`. A US-profile ticker/ETF without supported SEC companyfacts stays explicitly unsupported; preserve previous valid US packs.
- Use a committed schema-validated official TWSE/TPEx directory fallback, TWD, `Asia/Taipei`, and a 09:00–13:30 local session. Do not invent holiday data, Taiwan SEC facts, or a TSM ADR alias. Resolve ambiguous bare codes only with an explicit suffix.
- Yahoo's actual exact-13:30 Taiwan terminal-auction sample is a normalized explicit `sessionCloseObservations` entry, not an ordinary interval or fabricated bar. Research includes only validated marks as `CLOSED INSTANT` at `asOf >= timestamp`. Taiwan intraday counts may include this additional observation; preserve US calendar/count behavior.
- Advance the market Actions cache profile namespace; preserve the existing hourly weekday schedule. Refresh of the official Taiwan directory is optional and must retain its committed fallback on error. Database/settings/export v4 remains unchanged.
- Freeze the price-axis font/width design: use `Atlas Narrow Axis`, a 70%-horizontal-scale derivative of Barlow Condensed Regular under SIL Open Font License 1.1 ([OFL.txt](src/assets/fonts/OFL.txt)). Configure minimum width 0, 11px, and `minMove: 0.01`; trim redundant trailing zeroes only. No abbreviation, cropped/hidden labels, coarser rounding, or 8px text. Long numbers may expand naturally. This chart font also affects time-axis labels, while app UI font choices remain unchanged.
- Primary accepted P1-AXIS-06 is closed: the corrected font scales advances and outlines (SHA-256 `5e465e809833ef0fa73c5a65827e921c0e02aba1facc263d606838c1bd126d1f`); Sol passed all 694 glyph geometries, the integrated axis check passed all four profiles, and fresh measurement showed at least 30.8% width reduction for ordinary samples. Root inspected local and public screenshots; axis labels including 2550/2600 and dates appeared coherent. The earlier overlapping-font evidence is superseded. Local normal/Pages gates and public HTTP/WebKit verification pass; physical-device UAT remains pending.

### Final local verification

The latest local verification is 225 unit tests across 29 files, lint/typecheck, normal/Pages builds, targeted collector/provider unit suite 18, normal browser 126 passed / 10 original expected skips, and Pages 32/32 passed (3.3 minutes). Combined browser result: 158 passed / 10 skips. Source review covered 269 files. Public HTTP passed all asset byte comparisons and validated all 16 SEC packs; public iPhone WebKit passed as detailed above. Mocked SEC-403 collection left 16 seeds `fresh`, NVO/QQQ/SPY/TSM unavailable, made four SEC calls, and preserved timestamps. An earlier local market snapshot check recorded 25 available and `0050.TW` lacking 1W/1M. The latest hosted 26-symbol refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Original observation dates remain attached to retained packs; no current quote is inferred. The Taiwan directory contains 2,251 entries; the other five selected Taiwan symbols have all seven timeframes. XOM's market status is separate from its SEC seed, which has two quarterly and zero annual facts. FIN-01/02, TW-03/04, AXIS-06, and BOOT-01/02 are closed. Physical-device UAT and independent external review remain pending.

### Review map and test plan

Review `docs/architecture/ADR-013-static-fundamentals-and-taiwan.md`, `docs/TAIWAN_FINANCIAL_SNAPSHOT_VERIFICATION.md`, `.github/workflows/pages.yml`, `.gitignore`, `scripts/refresh-fundamentals.ts`, `scripts/refresh-symbols.ts`, `src/fundamentals/FinancialSnapshotSchema.ts`, `FinancialSnapshotProvider.ts`, `MarketProfile.ts`, `SymbolCatalog.ts`, and their focused tests. Confirm the collector's symbol-level retention/error summary does not hide whole-run infrastructure failures; verify the cache namespace/key paths, 24-hour freshness, same-origin fetch path and stale/offline labels. Check no raw SEC response reaches the browser, no financial values are fabricated, and no financial/ticker cache is exported with user settings.

Verify Taiwan canonical suffixes and ambiguity handling, six-symbol static limit, session/date behavior, currency display, closed/future bar boundaries, explicit validated `sessionCloseObservations` at 13:30, `CLOSED INSTANT` research inclusion only for `asOf >= timestamp`, and unchanged US semantics/counts. Inspect official-directory fallback preservation under refresh failure. For the corrected font, confirm advance and outline scaling match, integrated preload and service-worker/offline behavior, five-second startup fallback, no glyph overlap, full price labels, trailing-zero-only trimming, 11px axis size, and no company UI font change. Geometry review (694 glyphs), corrected four-profile integrated axis check, fresh ordinary-sample width measurement, and public chart raster inspection have passed. The next user step is to open the existing public URL and try the Financials and Taiwan flows; reopen if cached, but do not clear site data. Physical-device UAT and independent external review remain pending.

## iPhone screenshot UAT correction — 2026-10-07

## iPhone screenshot UAT correction — 2026-10-07

Scope: red/green volume and fixed full-window MA20, visible numerical SMA/EMA values, passive compact external legend, taller mobile chart, external rail zoom, app-shell fullscreen/CSS fallback. No model/schema/drawing engine/provider changes. Review source: ChartEngine, IndicatorEngine, Volume, App, useChartFocus, styles, IndicatorPanel, indicators-v1 tests and mobile-uat browser regressions. Official vendored Lightweight Charts skills and installed5.2.1 typings were used.

Sol6.1High planned and reviewed read-only; Luna6Max implemented frozen chart and UI tasks. Primary triage accepted: (1) existing Volume period1 must not accidentally create MA1—use fixedMA20 and retain schema; (2) preserve any-explicit-Volume suppression of legacy volume regardless visibility/scope; (3) blank series titles after relocating names/values to legend; (4) focused desktop Manage must open a sheet within the fullscreen root; (5) failed native exit retains a usable Exit control; (6) keep44px external band with bounded horizontal numeric scrolling, no new20×16 interactive help; (7) ordinary input Escape remains unhandled when no chart/modal/focus action is owned. Each implemented by primary or after explicit primary decision. Two initial desktop test failures were hidden-panel selector ambiguity; selectors now select visible controls and retain all assertions. Full regression then exposed selection-driven mobile status24→48 layout movement. Primary restored reserved48px mobile status independently of selection; original editing gate passed all3mobile/tablet profiles, and the new focus test asserts stable plot height after selection/lock. Subsequent gate found taller layout moved the Export toast over Lock controls; primary docked mobile notices above the plot and made text click-through while Dismiss remains interactive. Pressed timeframe is centered in its own scroller with bounded resize RAF/cleanup. Read-only Sol re-review found no remaining blocker. The final focused export/lock and focus gate passed8/8 all4profiles; Final full104-case gate94PASS/10originalexpectedSKIP/0FAIL; Pages8PASS, combined102PASS. Unit138/18files, clean ci/lint/normal+PagesbuildPASS. Source1426afc9 deployed in successful Actions37615088219; actualHTTPS/newasset hash comparisons and remoteiPhoneWK numericalvalues/MA20/lockreload/export/focus/PWA/backend-unavailable checksPASS, zeroAPIs/errors, TLSverificationenabled. docs/MOBILE_UAT_VERIFICATION.md and mobile-uat-live-*.json hold evidence. PhysicaldeviceUAT and independentexternalreviewremainpending.

Chart/indicator and final UI read-only review found no remaining implementation blocker. Gemini/Claude external review remains pending; this Sol review is not independent external sign-off. Physical Safari drawing feel/fullscreen browser chrome/PWA still requires real-device UAT. Validation results are recorded in docs/MOBILE_UAT_VERIFICATION.md and PROJECT_STATE.md after complete regression and deployment.

Status: **External Review Pending**. Gemini and Claude connector/reviewer tools are unavailable in this Codex Cloud task. No independent sign-off is claimed. This package can be pasted into Claude Web with the referenced source files. Execution agents implemented frozen specifications; every substantive finding below returned to the primary reasoning engineer for triage before fixes. This is internal engineering review, not an independent Gemini/Claude approval.

## Product and scope
Existing accepted Phase1/2 Atlas Research Terminal in /workspace, extended continuously to V1. One React/TypeScript/Vite Web/PWA, pinned Lightweight Charts5.2.1. No proprietary TradingView source/assets/AdvancedCharts, paid dependency, broker, real-money execution or V2 scope. Demo simulated; Yahoo unofficial delayed prototype; actual financials SEC EDGAR. Physical iPhone/iPad UAT remains pending.

## Read first
README.md, AGENTS.md, PROJECT_STATE.md, docs/architecture/ADR-001 through ADR-009. Baseline verification is archived in docs/VERIFICATION.md and docs/PHASE2_VERIFICATION.md; current V1 evidence will be in docs/V1_VERIFICATION.md. Do not treat earlier Phase1/2 counts as final V1 evidence.

## Drawing review
Files: src/drawing/DrawingModel.ts, DrawingStateMachine.ts, DrawingController.ts, DrawingPrimitive.ts, DrawingRenderer.ts, HitTester.ts, Movement.ts, MagnetEngine.ts, DrawingHistory.ts, Loupe.ts; src/tools/*.ts; ChartTransform.ts/TimeMapper.ts.

Same pointer ownership/RAF/history/persistence system for all ten tools. Trend/ray/rectangle/Fib/measurements have two Press→Drag→Release anchors, horizontal/vertical one, channel three. Anchors canonical timestamp/price; logical advisory across timeframe changes, pixels transient. Whitespace future area never fake OHLC. Body motion immutable drag-start snapshot. Magnet anchors only, never body/future. Selected unlocked handles use large invisible touch hitboxes. Locks rechecked at release/domain commit, locked drag native pan. Verify channel P3 and vertical-channel geometry, rectangle corner identity crossing, measurement timeframe remap vs elapsed wall time, clipping/HiDPI/styles/Fib hidden levels, loupe precision and restore paths. Renderer remains outside React; no pointermove setState or per-frame storage writes.

## Indicators/storage review
Files: src/indicators/*, src/storage/schema.ts/IndexedDBStore.ts, src/app/AppStore.ts, IndicatorPanel.tsx, DrawingSettings.tsx, WorkspaceSettings.tsx.

SMA, SMA-seeded EMA, actual Volume id-based and symbol-owned. Lock only visibility/unlock changes. Presets copy nested state and assign fresh IDs/symbols. Legacy Volume overlay persists for old symbols; explicit Volume takes control and removal must not resurrect the legacy overlay. DB2 upgrades old app/symbol records in a versionchange transaction; invalid migration aborts without erase. Export2 awaits queued writes and refuses storage failure; legacy1 import is validated then upgraded, unknown/invalid data rejected before one replacement transaction. Review orphan drawing alerts, duplicate IDs, symbol ownership, invalid levels/styles, and queued-save failure behavior.

## SEC/fundamentals review
Files: src/fundamentals/*; tests/fundamentals.test.ts, sec-client.test.ts, sec-provider.test.ts, sec-backend.test.ts, sec-expanded.test.ts; tests/fixtures/sec/*.json and both provenance files; docs/SEC_V1_VALIDATION.md.

Fixed official hosts, general ticker→CIK resolution/cache, truthful SEC User-Agent, throttle/dedup/cache/cooldown. Exact numeric/digit-string CIK validation normalized behind provider. Explicit US-GAAP concept priorities/unit/kind; null missing values. Conservative fiscal calendars and standalone-quarter extraction. Q2=H1−Q1, Q3=9M−H1, Q4=FY−9M only matching concept/unit/start/priorend and compatible filing vintage. Never subtract cumulative EPS. Audit raw inputs and derived formula/accessions/dates/units. CapEx positive outflow; FCF=OCF−CapEx only aligned sources. YoY/QoQ fiscal keys, zero denominator N/A. Scrutinize restatements/comparative facts, same-filing conflicts, stub years and unsupported custom/IFRS concepts.

JPM uses explicit RevenuesNetOfInterestExpense fallback, no overlapping interest aggregation. Seven issuer patterns: AAPL/MSFT/NVDA/TSLA/JPM/WMT/XOM. Official current XOM maps to CIK2115436 holding entity with limited actual facts; historical CIK34088 Exxon sample is clearly separate and never a resolver override. UI issuerName/CIK/audit identifies source. Filing markers use actual SEC filed dates and say SEC Filing, never assumed earnings timestamp.

## Backtest/signals correctness review
Files: src/strategy/*, tests/strategy.test.ts, BacktestPanel.tsx, ADR-006.

MA cross and price cross configured SMA/EMA. Caller explicitly supplies closed bars using MarketTiming scheduled session ends; current forming bars excluded. Signals compare warmed-up prior/current closed values; execution at NEXT available bar OPEN only. No next bar means no fill. Verify SMA-seeded EMA/prefix invariance, no-lookahead, gap fills, equality crossings, costs, short account cash/proceeds, marked open positions and metrics.

Single position sized100% current equity, entry reserves fees; no leverage. Zero default fees/slippage explicit, optional bps adverse fills and commissions on executed notional. Closed trades separate from still-open unrealized PnL; total return/drawdown marked equity, no fake forced liquidation. Short insolvency preserves actual nonpositive equity/open position, truncates subsequent simulation, states no broker margin model. PF uses net winning/loss trade PnL; null when no losses. Prototype data/corporate adjustment accuracy not certified; no real execution claim.

## Alerts/events/cache review
Files: src/alerts/AlertEngine.ts, AlertPanel.tsx, App.tsx, src/events/FilingEvents.ts, ChartEngine.ts, src/market-data/*, tests/alerts.test.ts/market-v1.test.ts/events.test.ts.

Local definitions enabled/disabled/deleted, symbol/timeframe scoped. Selected chart only, foreground app, one-minute normalized provider refresh. Initial history baseline suppresses retrospective spam, bar high-water mark dedupes, opposite condition re-arms. Re-enabling resets baseline. Drawing move resolves current price/reset; deletion invalidates/disables. Alert refresh never cancels chart gestures or resets backtest. No background/browser-closed/push claim. Review stale/cached closed bars and MA warmup semantics.

Provider capability controls timeframes; Demo7, Yahoo6 direct intervals (4H unavailable). Cache key provider/symbol/timeframe/range/kind, TTL, cloned results, dedup, bounded IndexedDB. Corruption/symbol mismatch/AbortError never falls back to stale; genuine unavailable network may return explicitly STALE CACHE. Offline missing data unavailable. Yahoo splits/dividends come from actual validated records; split numerator/denominator retained, unknown currency not guessed. No polling all watchlist quotes or paid feeds.

## Mobile/performance/security
Single chart stays primary. Phone sheets; iPad contextual side panel leaves chart interactive. Pointer capture/touch-action prevent page scroll. Controls44CSSpx with keyboard focus/form labels, no essential hover-only action. Escape/Delete/undo/redo skip input fields. Unified market/SEC/storage/import/strategy errors keep chart intact, opt-in Debug off by default. PWA production shell/offline local settings/Demo; no API caches pretending real-time. Native wrapper same Web assets/config/runbook, no signing claim.

Stress target2500bars,8mixedindicators,100MIXEDdrawings across tools, pan/zoom/crosshair/edit without runtime errors. Any RAF gap evidence is headless scheduling, not physical FPS, GPU/paint rate or finger latency. Physical-device drawing feel/loupe/touch/pinch/scroll/PWAinstall/safeareas/longsessions remain user UAT.

## Primary triage log (accepted fixes)
- 4H Demo history had insufficient bars: bounded generation now retains2500 real scheduled simulated slots without future OHLC; initial slow generation replaced with bounded weekday/session work.
- Vertical one-anchor renderer incorrectly required P2: exempted with renderer regression.
- Vertical body snapshot preserved time but drifted stored price: price delta forcedzero for vertical movement.
- Same-x channel baseline: horizontal width retained for a vertical channel; collapsed point finite.
- Fib custom hidden-level style defaults could reject new standard Fib: filtered defaults to standard levels, instance arrays copied.
- Backtest callback changed each parent render and reset output: stable callback.
- Arbitrary execution arithmetic could become non-finite: validation/overflow errors, commission bound, symbol normalization, deterministic tests.
- Numeric-string SEC CIK rejected matching identity: exact digit-string numeric validation/normalization, mismatch regression.
- JPM quarterly revenue missing despite actual bank-total concept: explicit mapped fallback after generic concepts.
- DB migration abort emitted unhandled tx.done rejection: completion promise observed while open/migration still fails safely; original database retained.
- Explicit Volume removal revived legacy overlay: symbol-owned compatibility flag prevents resurrection.
- Alert timer originally replaced chart dataset/backtest each minute: independent evaluation refresh, snapshot chart/manual refresh.
- Marker date alignment initially scanned all bars per event: bounded binary search on UTC calendar day.
- Drawing visibility was recoverable only via selection/settings: direct list Eye control added.

## Rejected / deliberately constrained
No current-XOM substitution to historical CIK, no guessed bank revenue sum, no cumulative EPS subtraction, no unsafe quarterly derivation, no fake future bars/events, no private chart API, no native Linux signing claim, no background local alerts, no physicalSafari equivalence from Playwright.

## Reviewer response format
Finding ID/severity/file/line/reproduction/evidence/proposed fix. Reviewer must NOT edit code. Primary will mark ACCEPT/REJECT with rationale; accepted changes get tests and rerun relevant/full gates. External reviewer sign-off must be recorded separately from this internal triage log.

## Browser integration triage (primary accepted, 2026-10-06)
- Mobile V1 selected-toolbar overlay blocked the existing locked Rectangle/Fib/Trend body-pan journeys after metadata/layout additions. Keep controls outside the plot in a stable-height status strip; preserve native pan and all old assertions. No pointer-system fork.
- WebKit datalist consumed ticker Enter without form submission, breaking symbol isolation journeys. Explicit input Enter normalizes/submits the current value while keeping form submission fallback. Original symbol tests rerun.
- Original baseline run intentionally stopped after diagnoses; failed gate is not claimed passing. Final four-project rerun will be recorded in V1_VERIFICATION.

- Primary accepted V1 mixed-object browser finding: DrawingPanel assumed every nonhorizontal object had P2 and crashed on one-anchor Vertical Line. List summary now checks actual anchor count and displays vertical date; mixed-tool list/regression rerun required.
- Cache browser test initially selected Demo rather than Yahoo metadata by omitting provider identity from its IndexedDB query. Accepted test-only identity filter; provider cache keys already distinguish both. Alert foreground/stale fallback browser tests passed before full rerun.
- Primary accepted finite strategy-ratio/aggregate overflow finding from internal execution-agent inspection: checked guards reject nonfinite ratios/sums, with executed extreme OHLC regressions. Marker persistence after closing research panel deliberately retained for chart inspection (not an orphan-symbol behavior).

- Primary accepted latent handler-restoration bug proved by stricter locked-channel pan regression: LWC 5.2.1 chart.options() returns a live options object, so disabling gestures mutated the saved restoration reference. Capture scroll/scale options by value before applying false. Release pointer capture with the actual up pointer ID after state-machine end. Existing lock-pan tests are strengthened to compare an already-defined range, preventing a first saved range from masquerading as movement. This is a Phase 1 hot-path fix required by V1, not a replacement pointer engine.
- V1 test helper incorrectly required an app record when only symbol indicators had been committed; default workspace legitimately has no app record yet. Read symbol records directly and await app record only when a workspace-setting commit is exercised. No product storage behavior change.

- Primary accepted a native Touch Event ownership bug after real CDP touch evidence: a drawing long press reached LWC's parallel Touch Event listener and entered crosshair tracking; the next locked-body drag then moved crosshair instead of panning, despite restored scroll options. The existing DrawingController now suppresses compatibility touch events only for owned drawing cycles through touchend; secondary touch cancels drawing and delegates native pinch. Geometry still uses Pointer Events and one RAF. The regression deliberately holds placement beyond the native long-press threshold and compares actual saved pan ranges. Temporary diagnostic reads of the installed open-source library were removed; production uses no private chart API.
- Primary accepted native CSS inset compatibility: use Capacitor's injected --safe-area-inset-* variables with standard env fallbacks. Core 8.5.2 injects zero when older Android WebView is already padded, so no duplicate inset is introduced; physical native edge-to-edge UAT remains pending.

## Browser evidence limits
Mobile Chromium drawing/pinch regressions dispatch real browser Touch Events through CDP. WebKit runs the iPhone/iPad device viewports and native mouse pointer capture with touch-modality overrides for drawing hitboxes/loupe; Playwright does not provide the equivalent CDP multi-touch path there. Those passing WebKit cases establish browser/layout/state behavior, not physical Safari finger latency, pinch feel or device FPS. Actual iPhone/iPad touch UAT remains mandatory before a device-quality sign-off.

## Final executed acceptance evidence
Clean root npmci PASS, unit135/135 across17files PASS, lint PASS, TypeScript/productionbuild PASS. Full88browsercases:78PASS,10expectedSKIP,0unexpectedFAIL,0flaky (desktop21/1, MobileChromium19/3, iPadWebKit19/3, iPhoneWebKit19/3). All core old/new journeys enabled. Skips only touch-only desktop and controlled desktop stress on otherprojects. Mixed2500/8/100all10tools plus101stcreate/edit/delete,pan/zoom/crosshair had zero runtime errors;89headlessRAF gapsmedian16.7p9516.8msare scheduling only. Nativeisolatedci/config/types/build-webPASS; finalnormalWebPWA buildrestored. ActualSEC7company/liveandlatestcompiledbackend4company audit smoke; YahooAAPL5m/1H/1D HTTP200. See docs/V1_VERIFICATION.md, v1-browser-summary.json, v1-performance-measurement.json, SEC_V1_VALIDATION.md and v1-live-*.json. No unfinished V1Cloud milestone or failing gate.

A source-only review archive is available at scratch/atlas-v1-review-source.zip. It includes code/tests/official test fixtures/ADRs/lockfiles/nativeconfig and this package, without node_modules, dist, credentials or local browser data. External reviewers should inspect the source as well as this narrative, return findings without editing, and avoid treating the passing suite as proof of universal SEC or real-device correctness.

## Authorized GitHub Pages hosting review — 2026-10-07

GPT-6.1 Sol High planned and independently read source without edits; GPT-6 Luna Max implemented frozen hosting-only changes. This is internal model engineering review, not external Gemini/Claude sign-off. Primary triage:

- ACCEPT HOST-01: remove unplanned Yahoo-backup rejection; preserve existing atomic import/provider preference, then explicit unavailable/manualDemo.
- REJECT HOST-02 recommendation to retain staticYahoo cachewrapper: backendabsence must remain explicit even iforiginhasoldcache; normalYahoo/Democache unchanged.
- ACCEPT HOST-03: exclude generateddist-pages fromESLint afterartifactbuild.

Reviewfiles: Vite/base/buildscript/workflow;HTMLlinks/mainregistration/nativeguard;manifest/scopedSWcacheandAPIfilters;staticprovider/UIguards;newPagesconfig/tests/ADR010/README. No chart/drawing/storage-model changes. Source snapshot comparison confirmshostingboundaries.

Historical intermediate gate evidence (superseded by the final results below): npmci;136unit/18files;lint;normal+Pagesbuild;8/8Pagesproductionbrowsercases coveringallfourprofiles, scopedcachecleanup/manifesticons, touchdrawing/locks/MAisolation/reload, portableYahoobackup/manualDemo, zeroAPIrequests, offlineDemo+SMA. Offlinecase specificallyrestoresSMA; onlinecase proveslocked-drawingreload. At that intermediate checkpoint the full normal regression and deployment were pending; final regression has since passed and publication is externally blocked as documented below. Do notclaim physicalSafari or externalreview.

PagescannotexecuteNodeYahoo/SECbackends; UIstatesunavailable, nofabricatedfinancials. No credentials inpublicationallowlist. GitHubPages administrationscopes mayrequireuseraccount action; sourcecommit successalone isnotlivewebsiteproof. IndexedDBoriginsharingbetweenprojectsdocumented; schema intentionallyunchanged.

Final hosting gate evidence: cleannpmci/136unit/lint/normal+Pagesbuild PASS; existingnormalV1browser78PASS/10expectedSKIP/0FAIL (8.1min); Pages8PASS/0FAIL (40.3sec), allfourprofiles. No Phase1/2/V1 regression. Publication remains a separate unverified step until actual HTTPS checks.

Historical authorization blocker (resolved by user authorization): publication attempts were BLOCKED: GitHubCLIcontentsPUT andconnectorcreate_file403; PagesPOST403; no commits/branches created; publicHTTPSsite404. Verified account/installation mismatch: authenticatedwudn9922, connectorinstallationonsaku0827/fast-launch-api only. Usermustauthorizetargetrepository. ThisisexternalGitHubauthorization, nottest/implementationfailure orauto-approvalreview rejection. No credentials inspected, exposed orbundled; no unauthorisedidentity/networkworkaround.

Primary ACCEPT HOST-04 documentation cleanup: mark intermediate pending-regression notes historical and put current external blocker/next action at top of PROJECT_STATE. Final Sol read-only review found no implementation blocker; actual owner authorization and HTTPS deployment remain external pending.

Source publication retry: userauthorizedwudn9922installation168757214;172files committedmain5f87026. HostedActionsci/unit136/lint/PagesbuildPASS; configurePagesonlyFAILbecause site notenabled. CurrentAppstillcannotcreatePages(403; missingadministration/pages). OwnerSourceGitHubActionssetting isnecessary externalsetup, supportedbyofficialconfigure-pagesactiondescription. No code/testfailure andnosecret/PATworkaround.

Primary ACCEPT HOST-05: update currentpublication summary withauthorizedinstallation/sourcecommit; markearlier no-commit/no-authorization paragraphs historical. Currentblocker isownerPagesenablement only; sourcecommit andhostedchecks succeeded.

## Live hosting completion

OwnerenabledPagesSourceworkflow; run37596025627 build+deploySUCCESS. HTML/allassets200 andremoteiPhoneWebKitDemo/SMAlockreload/PWAscope/staticSEC/noAPIs/noerrorsPASS withTLSverificationenabled. Evidence docs/pages-live-http.json/pages-live-browser-smoke.json. PhysicalSafariUATpending. RemoteCloudChromiumblockedbyproxyCAtrust; globalNSSCAmodificationwasrejectedbyautomaticreview duefuturetrustboundary andnotexecuted. No insecureTLSbypass. Alllocal136unit/86browserPASS+10expectedSKIP/lint/buildremainvalid; onlydocs/evidencechangeafterpublication. No V2featurework.

Primary ACCEPT HOST-06: GPT-6.1 Sol High finalread-onlyaudit confirmed liveHTTP/WebKitproof andaccurateCloudChromeBLOCKEDstatus; GPT-6 Luna Max relabeledthe earlierunqualifiedPublicationheading ashistorical. No applicationcodechange. Latestdocs/evidence deploymentrun37596875057 build/deploySUCCESS beforethis finalheading-onlycleanup.


## SEC material restated comparatives — PR #153 share-basis/Q4 revision (2026-10-22)

Merged latest main 71bcd1cacd0169286d8353e7e2dfa610c6eff57e into the original codex/sec-restated-comparatives-20261022 branch using merge commit d12e510b6fe82e39ae0b7daa444f0bb2bd25ac1b. The sole conflict was this review package; both sides were retained. No rebase, force-push, PR merge or data apply.

Only a later annual filing with a strictly greater than 2% difference against a current non-per-share income field may switch a period. EPS and dilutedShares cannot trigger switching. Per-share-only switches drop from 379 to zero. Existing per-row split/ADR conversion remains, followed by a conservative integer-factor retention guard (1% relative tolerance; also checks income-adjusted implied share ratios). Suspected share-basis candidates retain all existing per-share fields, recorded as basis-unrestated and mixed-basis, without claiming a ratio alone proves a corporate action. The reported CAPR, GGAL, EJH and HAO examples all remain unchanged.

Missing filing fields retain their current value with existing flags preserved. When Q1–Q3 are not available on the switched annual filing's basis, the entire stored quarterly series, including Q4, remains unchanged; annual and quarterly period issues identify quarterly-as-reported and retain the reason preventing cross-basis derivation. Unapplied quarter candidates do not acquire restatement revisions. Complete coherent restated quarters still permit the unchanged numeric/share/sign/error acceptance gates. No existing Q4 EPS is set to null.

Final dry run: 4,967 US files, 4,960 reviewed and seven explicitly unsupported; 1,172 switched annual periods in 580 symbols (788 revenue/netIncome-triggered periods in 425 symbols); 2,151 changed values. All nulls, including Q4: zero. All 1,599 formerly nulled Q4 EPS cells are retained. Two Q4 cells change: KVHI FY2013 basic/diluted -0.02 to 0.36, directly reported by the same restating filing. There are 1,152 quarterly-as-reported fiscal years. AD FY2023/FY2024 revenue becomes 100,469,000 / 102,933,000, while both quarterly series and Q4 EPS stay unchanged.

The feedback's 33-of-381 count cannot be reproduced literally from the pinned 8f0a8b8e report: it contains 382 annual diluted-EPS changes (376 have two non-null endpoints), and the named factors with relative error <=1% yield 25 cases. All 25 retain existing EPS. JSON shareBasisAudit includes every one of the 382 before/candidate/after outcomes and the exact formula; Markdown also lists a wider 58-case screen for rounding-sensitive review. This wider set is explicitly not proof of a split. No arbitrary list was relabelled as the requested 33 cases.

The full US snapshot was regenerated offline from the existing cache; after the final unapplied-quarter audit guard, all 18 files containing quarterly retention revisions were replayed, with unchanged numeric results. All 4,967 input hashes match the previous report and current files. All null/drift invariants pass. This revision sends zero SEC requests; original identified-UA, <=4 requests/second, first-403 stop and 12,000-request budget remain unchanged.

Reproduce the comparison audit by exporting the prior report first:

```bash
git show 8f0a8b8e:docs/report/sec-restated-comparatives.json > /tmp/sec-restatements-before-share-fix.json
python atlas/scripts/report_sec_restatements.py --prefer-latest-filing --offline --workers 4 --cache-dir /workspace/.cache/sec-restatement-cache --comparison-report /tmp/sec-restatements-before-share-fix.json
```

Final verification: python -m unittest discover -s tests — Ran 1493 tests in 186.294s, OK. All existing cases remain, with superseded null expectations updated to the new preservation policy; 23 focused restatement tests cover the AD example, coherent restated quarters, per-share-only rejection, share-factor retention/audits and switched-year quarterly-as-reported preservation. Main's new test dependencies (xlrd/PyYAML) were installed before the successful full rerun. Atlas: clean npm ci; 964 tests passed; lint and build passed. An initial frontend run during the report hit a 5-second fixture timeout; the unchanged full suite passed after the scan.

No financials data file, frozen Q4 answer set, missing-classification file, acceptance ledger or numeric acceptance gate was changed. Default-off selection remains unchanged; no CLI apply path exists. Independent review is unavailable; no independent sign-off is claimed. New-head CI is tracked in the PR description.

## 2026-10-22 public research extension — same-model self-review

User requested one-model continuous implementation and explicitly prohibited merging. No independent AI review was requested or claimed. See [the acceptance matrix](../docs/RESEARCH_ACCEPTANCE.md) and [source/method contract](../docs/RESEARCH_DATA.md). Primary self-review verified official category totals and CSV report dates, excluded today's volume from RVOL denominators, retained directional numerators/denominators and missing-state distinctions, rejected pre-separation institution categories instead of synthesizing zero, retained 649 newer report snapshots over older cache output, deduplicated SEC accessions across share-class aliases, persisted cooldown, and kept monthly/weekly data out of daily returns. The new chart dock state is transient and does not enter user settings/export schema; chart/indicator/drawing/LINE/authentication implementations were preserved. Local stock/drawing/timeframe regressions passed; final CI is reported by PR #152. Production has not been changed by this PR.

Official daily turnover follow-up: verified dated TWSE MI_INDEX and TPEx dailyQuotes responses; preserved raw market inputs with a 120-session cap. Institutional estimates use amount/shares only from the same official source, exclude incompatible closing-price bases, and still disclose market proxies rather than actual institutional cost. Cash-only enrichment preserves canonical OHLCV signals after cache eviction; tests compare it against full calculation. The full Python CI now checks out repository history required by legacy regression tests.

## PR #152 operations follow-up — same-model self-review

Accepted: recursive clock-insensitive writes; seven market archives with stable shared shape tables; range/200 transport and identity validation; preserve all 10,384 original records including US ticker TW. Run clocks remain in module indexes, SEC event receipt times rehydrate from its index. No chart/storage/auth boundaries changed.

Accepted: two source-timed schedules, market-selective volume, fresh Taiwan turnover artifact dependency, one non-force publish commit per run, collectors read-only without persisted credentials, checksum/path validation before overlay, main-only publication and research-code race guard. Missing SEC secret skips with no request and no snapshot change; fallback identity removed from the radar.

Measured payload 158,722,480 → 103,409,630 bytes, 10,389 → 12 files. Full real-data clock-only replay made no byte/mtime changes. Growth replay measured 1,907,836 bytes delta-packed; budget 2–4 MB/day packed, or roughly 23 MB/day before server packing, with documented source/backfill uncertainty. Existing PR history retained, no force push. No production run, merge, external AI or independent-review claim. Evidence and reproducible commands: `docs/RESEARCH_OPERATIONS.md`.

## 可畫線世界／題材圖表自我審查

以 main adfb6c7da 為基準另開 PR。重用 5.2.1 ChartEngine 與 Drawing Primitive；AppStore 初始化抽成共用 singleton，沒有新增資料庫或改登入，研究 namespace 與週期隔離。原物料／FX 真實日 OHLC；期貨明示 source-stitched front contract，FX 保持方向並排除無法通過 OHLC 邊界的來源資料。題材選參考 ETF／成分股，沒有假造等權組合高低價；其他題材統計／清單保留。

只新增 K 線永久 CSV，日期合併拒絕刪歷史；current metadata 與 JSON 視圖覆寫。白名單／checksum artifact handoff 延伸至 CSV；generated publisher records 將其保留 main。沒有操作正式開關、force main、合併或刪除既有資料。覆蓋、實際樣本、供應商限制、重現指令：docs/DRAWABLE_RESEARCH_CHARTS.md。同模型自我審查，沒有外部 AI。
# Task A data integrity review (2026-10-22)

Primary-engineer self-review; no independent sign-off. User scope: one PR, no merge.
See [data, source receipts and validation](../docs/DATA_INTEGRITY_TASK_A.md).
Reviewed acceptance/refetch/official fallback, permanent-row preservation, quarantine before
indicators and calendar aggregation (including offline cache), legitimate negative financial
net revenues, explicit-null merge precedence, same-date RS ranking and screener date scopes.
The known 7825 row remains stored and excluded; no unverified price was invented.


## Task B — reviewed SEC comparative apply (2026-10-23)

Primary implementer self-review; no independent human sign-off. Separate PR, do not merge.

- Apply pinned #153 report only: 580 symbols, 1,172 annual periods, 2,151 cells; zero nulls, zero lost flags. Every changed original retained in quality.revisions(kind=restated).
- Independent Git-tree verification compares all 6,947 financial files, retains alignment and EPS grades, proves exact old/new cell equality and all 9 Q4 answer/classification/ledger files byte-identical.
- 219 growth rows independently rebuilt; unrelated rows retained. NVDA metadata-only secEnd exposes FY2026 2026-01-25 beside unchanged 2026-01-31.
- Offline preflight rejects drift before any write; batch rolls back on write failure. SEC fetching is separate, identified and bounded; apply makes zero requests.
- SEC-focused: 39 passed, full financial-tree / growth / protected-file proof passed. Initial full Python at the Task A data baseline: 1,582 passed; after main research record updates: 1,584 run, one existing world snapshot/CSV consistency failure. Atlas clean npm ci, 985 tests / 84 files, typecheck, lint, production and Pages builds passed. Four new local desktop/mobile Chromium checks passed; first remote browser run: 230 passed / one desktop-inapplicable skip. Overall CI is not green because of the world input mismatch; no test was relaxed. Independent sec-write-proof CI runs regardless of that integration result.
- Evidence: docs/SEC_RESTATED_COMPARATIVES_APPLY.md and docs/report/sec-restated-comparatives-{apply,verification}.json.

Task B refresh regression: ordinary updates retain restatement flags, original revision history and secEnd; explicit reviewed SEC basis protects the five legacy-Yahoo-labelled annual rows from being reverted by Yahoo. No additional statement values were written.

## FY2021–FY2025 conservative Yahoo backfill (2026-10-23)

Primary implementer self-review; no independent human sign-off. Replacement for closed, unmerged #151;
new branch from main containing #164; do not merge. See [policy and replay](../docs/YAHOO_ANNUAL_BACKFILL.md).
Reviewed whole-period 2% veto across every overlapping field, zero/negative/unit handling, no EPS/shares,
null-only existing annual rows, preserved unknown flags/revisions, field provenance in notes/table/chart detail,
Yahoo/SEC/FinMind refresh retention, atomic batch rollback, and independent Git-tree + protected Q4 proof.

Shared provenance notes distinguish annual and quarterly field fills. The existing WebKit MA layout
regression assumed both desktop valuation and mobile tab elements existed while accepting either as
the shared row; it now waits for the toggle and measures each available sibling. The same-row and
zero-overflow assertions remain mandatory; no MA product behavior changed.

## Monthly MA, LINE trending stocks and single-symbol refresh (2026-10-27)

Primary-engineer self-review; no independent sign-off. User requested a PR without merging;
the follow-up changes MA cadence from the original two-month request to monthly.

- MA job runs on the first of each month at 02:17 UTC (10:17 Taipei), with manual dispatch and existing partial-failure retention intact. UI cadence text matches.
- The LINE report saves its ordered, at-most-five US trending symbols atomically after a successful push. Empty fetches preserve the last good report and its timestamps. The daily generated owner, ignored-output inventory, Pages assembly and legacy publication include this file. No recipient identifiers or message text are published.
- Screener and US markets read the same schema-validated JSON; loading/error/retry and aged-data states are explicit. Symbol buttons open the existing daily chart.
- Manual K-line refresh bypasses the selected symbol's normalized/raw/HTTP caches, validates results, keeps other symbols' caches, and protects refreshed entries against earlier in-flight writes. Weekly/monthly normalization still uses that symbol's daily rows. Failed refresh keeps the current chart; navigation cancels old refreshes. Existing drawing/indicator/view state is reused. The source remains Yahoo delayed or official static data.
- Verified clean `npm ci`, 1,315 Atlas tests (113 files, final full run with `--maxWorkers=1`), lint/typecheck, normal and screener Pages builds. Python: 2 trending-report tests, 24 LINE/screenshot regressions and 28 generated-storage tests. Generated-storage coverage proves the new report survives publication and Pages assembly. Initial concurrent full testing hit a five-second SEC fixture timeout; the isolated complete rerun passed without changing the test.
- Browser regression: 29 passed / 1 existing desktop-inapplicable skip across the new feature, MA recommendations, market-chart controls and workspace layout/symbol preferences on desktop Chromium, mobile Chromium and iPhone WebKit. The compact refresh button fixes the discovered phone-header height regression. WebKit's missing libraries were extracted into `/tmp`, checked with `ldd`/`ctypes`, then exercised with host validation disabled because `ldconfig` cannot discover the local libraries. This is headless evidence, not physical-device UAT.
- Read-only live smoke: the existing Supabase `market-chart` endpoint returned AAPL and 21 daily bars for `range=1mo`; no production function deployment, real LINE push or MA/data job was triggered. First production trending report appears after the next successful daily LINE report following merge.

## PR #186 requirement revision — US screener LINE group

Merged main/PR #185 with merge commit `dfab5672c`; both parents and both browser test lists are retained.
`index-volume.spec.ts` is byte-identical to main: the first three market cards remain index-volume,
breakout and breadth. Both standalone trending cards and their stylesheet are removed.

The US-only `LINE 熱門` group immediately follows the full-market chip. Its default order is the LINE
symbol order; each existing universe row retains its metrics, dates and profile, while absent symbols
remain explicit unmatched rows. The same screener rows, pills, sorting, filters and chart actions apply.
Saving uses the existing manual-watchlist format, retaining this set of symbols and the applied filters;
it does not accidentally save the whole US universe. File errors leave the chip visible with the required
missing-list message and retry. Fetch/send timestamps are shown, and stale status compares the fetched
Taipei day to the newest report date rather than a 36-hour wall-clock threshold.

Python changes only add the frontend symbol pattern as a skip-invalid validation before deduplication
and the five-symbol cap. Last-good retention, post-send saving, generated ownership and Pages publishing
remain unchanged. Local verification: clean npm ci; 1,322 Atlas unit tests; three trending Python tests,
24 existing LINE/screenshot tests and 28 generated-storage tests; the eight targeted desktop browser cases
passed. Phone/WebKit and the complete Atlas PR workflow are rerun for this revision; final CI results are
reported with the PR, without claiming independent review, deploying or merging.

Phone visual follow-up: prevent the fixed-group status tabs from shrinking under the LINE timestamp
and stale notices, and tighten the timestamp paragraph spacing. The group browser test checks that the
status-control container cannot clip its buttons. All 24 targeted browser cases passed on the preceding
revision; the same matrix and the full PR workflow are rerun on this layout follow-up.

## 導覽改版第一階段（main d59b07be4）

主要工程師自我審查，沒有宣稱獨立審核。使用者範圍為導覽與內容搬移，所有功能／控制／網址／資料都必須保留；沒有無法保留而被刪除的項目。

功能與舊網址對照、修改過的每個既有 spec、文件標題差異、390px／桌面截圖及 trace 說明見 docs/NAVIGATION_PHASE_ONE.md。MarketsPage、WorldPage、ThemesPage、ChartEngine、StorageNotice、ParameterConsole、main.py、SQL、Supabase、workflow 與市場資料沒有修改。清單與原有雲端／歷史邏輯共用原 ConsolePage，只調整 section 與導航目的地。

原 DEFAULT_PAGE、品牌連結、日期 chip、DataHealthPanel、RouteBoundary、lazy loading、chartVisited 及捲動邏輯保留。入口測試證明圖表／財務往返仍是同一 chart DOM；帳號／舊資料／LINE／PWA／清單整合斷言完整保留。先前桌面新測試使用 radio 的隱藏 input 點擊，已改用原可點擊 label，保留 checked 與重載後持久化斷言。

已執行 clean npm ci、npm test（1,505 通過）、lint、一般與 Pages build；完整 npm run test:site 的三瀏覽器結果、其失敗與修正記錄以 PR body 為準。不合併、不部署。
