const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-1a0a3704d81f`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-X_iuU_2u.js","assets/ChartPage-DTn4q-MY.js","assets/ChartPage-KtxOtJeh.css","assets/Common-CStjf6Tn.js","assets/Common-ghF4RX17.css","assets/ConsolePage-3-Lt8q-J.css","assets/ConsolePage-CD-Osqfw.js","assets/DrawableCandleChart-BFTBJVdl.js","assets/DrawableCandleChart-CfntcGuh.css","assets/FinancialsPage-4Dpnornb.js","assets/FinancialsPage-7WFXsGuJ.css","assets/FixedStockParameters-a-8dYBym.js","assets/JapanRatesView-HZ6sMS4m.js","assets/MarketsPage-BBqGB-0S.css","assets/MarketsPage-zwqG2lQG.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-V11RULLA.js","assets/OfficialAlerts-P4llyw4T.js","assets/PageFooterNote-BTKPZG4Q.js","assets/PeRiverCard-Icl53QpW.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-BAN7udYb.js","assets/RatesView-A027OGSQ.js","assets/RelativeStrengthPanel-DNLabUY9.js","assets/ReportGate-GQFRpFX6.js","assets/ScreenerPage-DQQui_R8.js","assets/ScreenerPage-DSwOFJ74.css","assets/SecRadar-C7gE8KBh.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-DNkcgtWB.js","assets/StockResearch-DYUHSDyb.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-f7LQNOIK.js","assets/TimeChart-BAVFF9mI.js","assets/VolumePanel-Bi9AZTf_.js","assets/WorldAssetsView-Cn0o06xy.js","assets/WorldPage-52B8G8Yc.js","assets/WorldPage-CofhzqT9.css","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-COLoC-Q8.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-Bv-NMWk_.js","assets/chart-h2tyZhBG.js","assets/check-BCJxtz0k.js","assets/chevron-down-BTZihw8D.js","assets/chevron-right-BJKTfzp7.js","assets/dataSources-sZoS6O31.js","assets/earth-C-L3R9JY.js","assets/index-BxgJtGi2.js","assets/index-CH87S-1G.js","assets/index-CJD5VWRD.css","assets/index-jh05VtuY.js","assets/layers-DX9qdnl6.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-Dorc3cTx.js","assets/rates-Bjg7ZeHX.js","assets/rates-CSQxt-s6.css","assets/react-DPIr46qh.js","assets/research-JjEE26bW.js","assets/storage-DTU9qw1T.js","assets/trash-2-BKe9_Tgt.js","assets/useDialogFocus-D9jhTAKn.js","assets/worldPerformance-DWYOYGH0.js"];

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
