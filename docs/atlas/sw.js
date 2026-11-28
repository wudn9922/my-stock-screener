const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-b775e8c22446`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-BJ8OLs8m.js","assets/ChartEngine-rO-pV6wc.css","assets/ChartPage-39vyzzel.js","assets/ChartPage-Ccit_JWJ.css","assets/Common-CtYqGx0Y.js","assets/Common-ghF4RX17.css","assets/ConsoleDialog-Cq6rbAs3.js","assets/ConsolePage-CE859C9d.css","assets/ConsolePage-lnAq1A_m.js","assets/DrawableCandleChart-CTPCBhO1.js","assets/DrawableCandleChart-ClsvvBOe.css","assets/FinancialsPage-BATK25gT.js","assets/FinancialsPage-UUAW98dz.css","assets/JapanRatesView-lhvibWrZ.js","assets/LiveQuotes-CMwPQuVP.js","assets/MarketTiming-DClP3hyr.js","assets/MarketsPage-2DBY0Ktb.css","assets/MarketsPage-BZgeOXM1.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-Dh2VUiGg.js","assets/OfficialAlerts-B-22Xocb.js","assets/PageFooterNote-nX_VSmBE.js","assets/PeRiverCard-C9yCtnRq.js","assets/PeRiverCard-CQuBlGuS.css","assets/PeRiverChart-D31isSg1.js","assets/RatesView-N3DVbuFt.js","assets/RelativeStrengthPanel-BPc_2LRk.js","assets/ReportGate-BjJaSEi7.js","assets/ScreenerPage-DvH3rwAO.css","assets/ScreenerPage-dwTQk9Mf.js","assets/SecRadar-BWOfrcbG.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-LtrjCjCI.js","assets/StockResearch-C8QDTjfE.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-DlyXWFDl.js","assets/TimeChart-DDRyK64D.js","assets/VolumePanel-C5TR24Gg.js","assets/WarrantsPage-CLyxZDYg.css","assets/WarrantsPage-D-HPioYk.js","assets/WorldAssetsView-DYxZEhXz.js","assets/WorldPage-CergnwRP.js","assets/WorldPage-CofhzqT9.css","assets/analysisComparisons-CvnJVehW.js","assets/analysisComparisons-D_nmfxi1.css","assets/arrow-up-BtarAmPB.js","assets/breakouts-G-l8GZ_z.css","assets/breakouts-XhWqZan6.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-co8rcQVe.js","assets/chart-h2tyZhBG.js","assets/chevron-down-DUpSVam9.js","assets/dataSources-BmPbgcmO.js","assets/index-BT36zcie.js","assets/index-C9wmOjzu.css","assets/index-jh05VtuY.js","assets/layers-DVR9j1z1.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/listPicker-Bqv6JKsk.js","assets/listPicker-CY6ftdhn.css","assets/loadFailureReason-CfQnHf9e.js","assets/qualityLabels-B0QuawIK.js","assets/quoteStatus-C1JzdWaf.js","assets/rates-Be7eyIzi.js","assets/rates-CSQxt-s6.css","assets/react-DPIr46qh.js","assets/research-De4yyqfs.js","assets/sliders-horizontal-Bs-SCgj2.js","assets/storage-bv9aEjHy.js","assets/useDialogFocus-CEsxvmDq.js","assets/worldPerformance-DWYOYGH0.js"];

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
