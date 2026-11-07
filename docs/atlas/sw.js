const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-ac5b77745a5b`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-CRoocROh.js","assets/ChartPage-CKE7jhjo.js","assets/ChartPage-KtxOtJeh.css","assets/Common-D3HiBtLB.js","assets/Common-ghF4RX17.css","assets/ConsolePage-D2o2PFxQ.js","assets/ConsolePage-DPTVVTwc.css","assets/DrawableCandleChart-CfntcGuh.css","assets/DrawableCandleChart-tYU3wyDR.js","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-Bk_P4cpz.js","assets/FixedStockParameters-uAnX8JSo.js","assets/JapanRatesView-BoOkpad0.js","assets/MarketsPage-BBqGB-0S.css","assets/MarketsPage-CaQlon9f.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-DD5FZ_sl.js","assets/OfficialAlerts-P5WTsvv6.js","assets/PageFooterNote-DOmHZUCu.js","assets/PeRiverCard-BB4Xu5eK.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-Cy4mJTHy.js","assets/RatesView-MPu4sSxU.js","assets/RelativeStrengthPanel-CO4i00jn.js","assets/ReportGate-BT0gZqCA.js","assets/ScreenerPage-DK7ONVCX.js","assets/ScreenerPage-DSwOFJ74.css","assets/SecRadar-D9_rt8Bc.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-DC2ui-bf.js","assets/StockResearch-DBnWblP0.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-XtnyPAyo.js","assets/TimeChart-1q_alGfr.js","assets/VolumePanel-YokQ65VM.js","assets/WorldAssetsView-CUgowy8i.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-sRAbE1l8.js","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-DnjABgZx.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-C4u1s28X.js","assets/chart-h2tyZhBG.js","assets/check-o4LN08kY.js","assets/chevron-down-Dq59SWSx.js","assets/chevron-right-CdseipOG.js","assets/dataSources-CYZetidF.js","assets/earth-ClUckvC5.js","assets/index-BAGLwsf7.js","assets/index-CH87S-1G.js","assets/index-CJD5VWRD.css","assets/index-jh05VtuY.js","assets/layers-B1xuTiQT.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-D2k_HrPc.js","assets/rates-7LpXdbpd.js","assets/rates-CSQxt-s6.css","assets/react-DPIr46qh.js","assets/research-X_lLY_nz.js","assets/storage-DTU9qw1T.js","assets/trash-2-tHafgbkX.js","assets/useDialogFocus-n_4c0Hfx.js","assets/worldPerformance-DWYOYGH0.js"];

function isInScope(url) {
  return url.origin === scopeRoot.origin && url.pathname.startsWith(scopePath);
}

function isApiPath(pathname) {
  return pathname.startsWith('/api/') || pathname.startsWith(`${scopePath}api/`);
}

function isCacheableAsset(url) {
  if (!isInScope(url) || isApiPath(url.pathname)) return false;
  return (
    url.pathname.startsWith(assetsPath) ||
    url.pathname === new URL('startup.js', scopeRoot).pathname ||
    url.pathname === manifestURL.pathname ||
    iconURLs.some((iconURL) => url.pathname === iconURL.pathname)
  );
}

function extractBuildAssets(html) {
  const assets = new Map();
  for (const match of html.matchAll(/(?:src|href)\s*=\s*["']([^"']+)["']/gi)) {
    try {
      const url = new URL(match[1], scopeRoot);
      if (isCacheableAsset(url)) assets.set(url.href, url);
    } catch {
      // Ignore malformed or unsupported references in the generated HTML.
    }
  }
  return [...assets.values()];
}

async function fetchAndCache(cache, url) {
  const response = await fetch(new Request(url.href, { method: 'GET', credentials: 'same-origin' }));
  const responseURL = new URL(response.url || url.href);
  if (!response.ok || !isInScope(responseURL) || isApiPath(responseURL.pathname)) {
    throw new Error(`Could not cache scoped app resource: ${url.href}`);
  }
  await cache.put(url.href, response.clone());
  return response;
}

self.addEventListener('install', (event) => {
  if (SCREENER_MEMORY_ONLY) { event.waitUntil(self.skipWaiting()); return; }
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      const response = await fetchAndCache(cache, scopeRoot);
      const html = await response.clone().text();
      const resources = new Map(
        [manifestURL, ...iconURLs, ...extractBuildAssets(html), ...BUILD_ASSETS.map(path => new URL(path, scopeRoot))]
          .filter(isCacheableAsset)
          .map((url) => [url.href, url]),
      );
      await Promise.all(
        [...resources.values()].map((url) => fetchAndCache(cache, url)),
      );
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys())
        if (name.startsWith(CACHE_PREFIX) && (SCREENER_MEMORY_ONLY || name !== CACHE)) await caches.delete(name);
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  if (SCREENER_MEMORY_ONLY) return;
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || !isInScope(url) || isApiPath(url.pathname)) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(async () => {
        const cache = await caches.open(CACHE);
        return (await cache.match(scopeRoot.href)) ?? new Response('Offline', { status: 503 });
      }),
    );
    return;
  }

  if (isCacheableAsset(url))
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE);
        const hit = await cache.match(request, { ignoreVary: true });
        if (hit) return hit;
        const response = await fetch(request);
        const responseURL = new URL(response.url || request.url);
        if (response.ok && isInScope(responseURL) && !isApiPath(responseURL.pathname))
          await cache.put(request, response.clone());
        return response;
      })(),
    );
});
