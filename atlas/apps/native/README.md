# Atlas native wrapper

This optional Capacitor shell packages the existing React/Vite app. It uses the same `dist` output, Lightweight Charts engine, drawing model, IndexedDB workspace and deterministic Demo data as the web product. Native builds bundle their assets; the config has no `server.url` and the wrapper does not register the web app's service worker.

`appId` is currently `io.atlasresearch.terminal` as a provisional development identifier. The release owner must verify that it is unique and select the final store identity before distribution.

## Requirements

- Node.js 22.12 or newer for the Web app and Node.js tooling.
- macOS with Xcode for an iOS simulator/device build.
- Android Studio with its supported Android SDK and Gradle setup for Android.
- A signing identity and provisioning setup only for signed distribution builds.

The managed Linux Cloud environment does not provide Xcode, iOS signing, or a configured Android SDK. Configuration readiness does not claim an IPA/APK build, signing, or physical-device UAT.

## Install and package the Web assets

From this directory:

```sh
npm ci
npm run build:web
```

The `build:web` script runs the root `npm run build` with `VITE_NATIVE_WRAPPER=1`, so the native asset bundle is produced from the same source while skipping production service-worker registration. The output is `../../dist`, resolved relative to this Capacitor project. Capacitor copies those built assets into each native project during sync.

## Add and sync native projects

Run the add command once per platform from this directory:

```sh
npm run cap:add:ios
npm run cap:add:android
```

After rebuilding the Web assets, sync the platform being developed:

```sh
npm run build:web
npm run sync:ios
# or
npm run sync:android
```

Capacitor's `add`, `sync`, `open`, and `run` commands operate on generated `ios/` and `android/` project folders. Use `npm run open:ios` or `npm run open:android` to launch the native IDE. Use `npm run run:ios` or `npm run run:android` when the relevant simulator/device and platform toolchain are configured.

## Native data availability

The wrapper is useful offline with the deterministic, visibly simulated Demo provider and local IndexedDB data. Current SEC and Yahoo requests use relative `/api/...` endpoints supplied by the Web development/preview server; a bundled Capacitor app has no such server. Those live providers therefore remain unavailable inside the native wrapper until a reachable HTTPS backend/API base and its CORS policy are implemented and deployed. No credentials or provider secrets belong in this package or the frontend bundle. Provider contracts and UI behavior are unchanged by this wrapper configuration.

## Platform checks

Capacitor Core 8.5.2's bundled `SystemBars` configuration sets dark system-bar style (light status/navigation text and icons) and CSS safe-area insets. The app's safe-area CSS reads the injected `--safe-area-inset-*` variables first, then falls back to browser `env(safe-area-inset-*, 0px)`. On older Android WebViews, SystemBars may pad the WebView itself and inject zero values to prevent the app from applying the same inset twice; on newer WebViews it passes through the real values.

The Android template targets SDK 36, where Android 16 enforces edge-to-edge layouts. Capacitor 8's SystemBars documentation advises enabling AndroidX `EdgeToEdge` in the generated `BridgeActivity` when inset handling is active; check the generated `MainActivity` and apply that platform setup if needed (Capacitor 9 handles it automatically). Verify safe-area spacing and gesture navigation on target Android versions and devices. On iOS, verify notch/home-indicator spacing and status-bar contrast. These checks require native simulator/device UAT and are still pending.
