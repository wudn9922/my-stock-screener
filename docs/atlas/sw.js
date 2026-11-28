const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-752bf15980b4`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-C7SXwa7D.js","assets/ChartEngine-rO-pV6wc.css","assets/ChartPage-Ccit_JWJ.css","assets/ChartPage-Lwwy0Rcz.js","assets/Common-Ce2D1MfC.js","assets/Common-ghF4RX17.css","assets/ConsoleDialog-BEs4Moha.js","assets/ConsolePage-B0nFf2Vy.js","assets/ConsolePage-CE859C9d.css","assets/DrawableCandleChart-ClsvvBOe.css","assets/DrawableCandleChart-bY3v3__K.js","assets/FinancialsPage-D-dAYo59.js","assets/FinancialsPage-UUAW98dz.css","assets/JapanRatesView-WklJwI04.js","assets/LiveQuotes-CADtX5kf.js","assets/MarketTiming-DkFcORgZ.js","assets/MarketsPage-2DBY0Ktb.css","assets/MarketsPage-DnBIdpTY.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-DM7DovvJ.js","assets/OfficialAlerts-BHB3EGAG.js","assets/PageFooterNote-DhTP0sPE.js","assets/PeRiverCard-CQuBlGuS.css","assets/PeRiverCard-D3d2LCaN.js","assets/PeRiverChart-BoLeR0-k.js","assets/RatesView-CCRVUe43.js","assets/RelativeStrengthPanel-rgKO8iKv.js","assets/ReportGate-CDrw5N0N.js","assets/ScreenerPage-CXtQq1Aa.js","assets/ScreenerPage-DvH3rwAO.css","assets/SecRadar-CZwJ3XS5.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-BCEzZDWw.js","assets/StockResearch-CEBNCfqy.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-lLNsmGeH.js","assets/TimeChart-D-I79J0s.js","assets/VolumePanel-DGJNOFWJ.js","assets/WarrantsPage-BNYG1ntJ.js","assets/WarrantsPage-CLyxZDYg.css","assets/WorldAssetsView-DbXeDgmS.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-DKM4IiOh.js","assets/analysisComparisons-CvnJVehW.js","assets/analysisComparisons-D_nmfxi1.css","assets/arrow-up-BgFIzEuD.js","assets/breakouts-Ct9rF7pH.js","assets/breakouts-G-l8GZ_z.css","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-BK25XhUf.js","assets/chart-h2tyZhBG.js","assets/chevron-down-zGccFfi_.js","assets/dataSources-CqWunedq.js","assets/index-BAPtLrQj.js","assets/index-C9wmOjzu.css","assets/index-jh05VtuY.js","assets/layers-w_lIuE-P.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/listPicker-BFBF720g.js","assets/listPicker-CY6ftdhn.css","assets/loadFailureReason-hhDITygk.js","assets/qualityLabels-BeMF13I6.js","assets/quoteStatus-B-x9IpdP.js","assets/rates-6PmAVQfT.js","assets/rates-CSQxt-s6.css","assets/react-DPIr46qh.js","assets/research-BemN0Pj6.js","assets/sliders-horizontal-Bz2WLhGA.js","assets/storage-bv9aEjHy.js","assets/useDialogFocus-BWTTTyZY.js","assets/worldPerformance-DWYOYGH0.js"];

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
