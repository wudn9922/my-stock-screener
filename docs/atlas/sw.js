const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-54ec6341596e`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-CIsZW4TN.js","assets/ChartPage-KtxOtJeh.css","assets/ChartPage-WbdzIqEw.js","assets/Common-DWgQTock.js","assets/Common-ghF4RX17.css","assets/ConsolePage-3-Lt8q-J.css","assets/ConsolePage-BYVk_cyK.js","assets/DrawableCandleChart-CfntcGuh.css","assets/DrawableCandleChart-De30N3j6.js","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-qy3jYIPz.js","assets/FixedStockParameters-B_iqQRNH.js","assets/JapanRatesView-DPHbR6zb.js","assets/MarketsPage-BBqGB-0S.css","assets/MarketsPage-X6zJ1xcV.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-DybEyQgF.js","assets/OfficialAlerts-BenkF_pQ.js","assets/PageFooterNote-BCiuiljg.js","assets/PeRiverCard-c5nvUcK1.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-DtY2av7T.js","assets/RatesView-B6638ucW.js","assets/RelativeStrengthPanel-DZq8yVNt.js","assets/ReportGate-Q_Vg7XKw.js","assets/ScreenerPage-82W_6ssy.js","assets/ScreenerPage-DSwOFJ74.css","assets/SecRadar-I9wyuf9V.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-CoFBqDxu.js","assets/StockResearch-IBcC6AeN.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-DAAJUV7y.js","assets/TimeChart-DrUgzXqn.js","assets/VolumePanel-h8eiukgW.js","assets/WorldAssetsView-UKcNp4xW.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-D4xjOuI9.js","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-C1ctaXUN.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-U4PHciHl.js","assets/chart-h2tyZhBG.js","assets/check-d9kAB5m-.js","assets/chevron-down-_6lZSUXQ.js","assets/chevron-right-D_qYVkXy.js","assets/dataSources-BfAtjtLt.js","assets/earth-BsHwDrV_.js","assets/index-6njAxDbb.js","assets/index-CH87S-1G.js","assets/index-CJD5VWRD.css","assets/index-jh05VtuY.js","assets/layers-D56eo8um.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-C59gmXq8.js","assets/rates-CSQxt-s6.css","assets/rates-Dq2eZsfM.js","assets/react-DPIr46qh.js","assets/research-BBv7UTaX.js","assets/storage-aKsqrkUr.js","assets/trash-2-DyaXrsMG.js","assets/useDialogFocus-D3_egY5x.js","assets/worldPerformance-DWYOYGH0.js"];

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
