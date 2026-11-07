const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-5c7c3fa5ea7c`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-DOecYx7W.js","assets/ChartPage-DjzlgeqG.js","assets/ChartPage-KtxOtJeh.css","assets/Common-CJ0BYwGC.js","assets/Common-ghF4RX17.css","assets/ConsolePage-3-Lt8q-J.css","assets/ConsolePage-CS_9p37i.js","assets/DrawableCandleChart-CfntcGuh.css","assets/DrawableCandleChart-DhOfJYFZ.js","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-DgkaubfW.js","assets/FixedStockParameters-BDoyoNBj.js","assets/JapanRatesView-CKOv39ze.js","assets/MarketsPage-BBqGB-0S.css","assets/MarketsPage-CKJbP4jR.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-ChiHtafL.js","assets/OfficialAlerts-D_GpvWNo.js","assets/PageFooterNote-Bx1YiYwN.js","assets/PeRiverCard-CxBCV3SZ.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-DIkhxvhQ.js","assets/RatesView-CrX-xahD.js","assets/RelativeStrengthPanel-Bovp_v2S.js","assets/ReportGate-CZ6wOhH9.js","assets/ScreenerPage-Cf3vzNPj.js","assets/ScreenerPage-DSwOFJ74.css","assets/SecRadar-BxyLjpRf.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-CLGB1PSc.js","assets/StockResearch-C-lwCrfb.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-Cxs_eFDY.js","assets/TimeChart-BRcDXMvB.js","assets/VolumePanel-DXZi8GwS.js","assets/WorldAssetsView-C3_hBOsK.js","assets/WorldPage-6G5cQnit.js","assets/WorldPage-CofhzqT9.css","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-BBR2wCpU.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-B0vF06UL.js","assets/chart-h2tyZhBG.js","assets/check-DMSPxREv.js","assets/chevron-down-BmTGhPym.js","assets/chevron-right-By5wlrQl.js","assets/dataSources-Cehl-RQ9.js","assets/earth-D8fzoAXO.js","assets/index-CH87S-1G.js","assets/index-CJD5VWRD.css","assets/index-HlvvVeaH.js","assets/index-jh05VtuY.js","assets/layers-kcrH4eKB.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-DHQFuzH-.js","assets/rates-CSQxt-s6.css","assets/rates-D7jsvFzk.js","assets/react-DPIr46qh.js","assets/research-CqdMMdLM.js","assets/storage-DTU9qw1T.js","assets/trash-2-ucvO84_9.js","assets/useDialogFocus-DLo_As6M.js","assets/worldPerformance-DWYOYGH0.js"];

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
