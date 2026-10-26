# ADR-009 — Web/PWA baseline and optional native wrapper

Status: accepted for V1 (2026-10-06).

## Atlas screener self-use PWA (2026-10-26)

The screener deployment is an online-first installable PWA, using its existing
stable manifest identity and public HTTPS scope. Installation is free on iOS
and Android and does not require the optional native wrapper below. Settings
provide platform instructions/native install prompts and a public deployment
version check. Reload is explicit, preserving the current route and warning
about temporary guest work.

ADR-016's account authorization overrides the historical offline-shell baseline
below for this deployment. The screener continues to retire its old worker and
never adds background CacheStorage or guest persistence. Modern installability
does not require a service worker. LINE login outside LINE is explicit and uses
the same server-verified grant; LIFF primary handoffs and OAuth returns initialize
before URL routing.
Fresh external launches require an explicit login tap to restore the SDK
session and reverify access. See [self-use runbook](../PWA_SELF_USE.md).

Retain the React/Vite Web product as the only implementation. The existing production service worker precaches a versioned shell, supports failed-network navigation, and excludes API responses. IndexedDB restores local analysis offline, and deterministic Demo mode requires no network. Yahoo cache fallback is explicitly stale; absent cached data is unavailable, never fabricated. Installability uses the manifest, original icons, HTTPS/localhost and safe-area CSS.

A Capacitor wrapper configuration and runbook under apps/native will point at the same built dist assets after Web/browser regression passes. No Flutter/React Native or second chart implementation. Linux Cloud cannot sign/build iOS, so Xcode, signing and physical WebView/PWA UAT are external device work. Android SDK/Gradle setup must not block Web V1. Native remote SEC/Yahoo requires a deployed free-compatible backend/reverse proxy; no secrets go into the frontend bundle.

## V1 wrapper configuration (2026-10-06)

`apps/native` pins Capacitor Core, CLI, iOS and Android to 8.5.2. Its config uses provisional development app id `io.atlasresearch.terminal` (release owner verifies uniqueness), app name `AtlasResearchTerminal`, and the root `../../dist` bundle. `build:web` invokes the existing root build with `VITE_NATIVE_WRAPPER=1`, which omits production service-worker registration; no remote `server.url` is configured. Core's SystemBars plugin uses dark style and CSS inset handling. App safe-area CSS reads Capacitor's `--safe-area-inset-*` variables before falling back to browser `env()` values; older Android WebViews can have insets applied as WebView padding with zero CSS values to prevent double spacing. The Android template targets SDK 36; check the generated BridgeActivity edge-to-edge setup because Capacitor 8 documents explicit AndroidX `EdgeToEdge` enablement for inset handling, with automatic setup in Capacitor 9. Bundled Demo/local workspace remain available offline. Current relative SEC/Yahoo APIs need a reachable HTTPS backend and CORS configuration before they can work from a native origin. Native SDK builds, signing and physical-device UAT remain pending.
