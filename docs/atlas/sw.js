const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-0883c766c3ad`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-B5bDOA56.js","assets/ChartEngine-rO-pV6wc.css","assets/ChartPage-Bhtwvrzq.js","assets/ChartPage-Ccit_JWJ.css","assets/Common-CHdrpJ9A.js","assets/Common-ghF4RX17.css","assets/ConsoleDialog--6DesHW9.js","assets/ConsolePage-0T4se6Y3.js","assets/ConsolePage-B5c2n1Bo.css","assets/DrawableCandleChart-ClsvvBOe.css","assets/DrawableCandleChart-gaoM-sPZ.js","assets/FinancialsPage-Clw6On2D.js","assets/FinancialsPage-UUAW98dz.css","assets/JapanRatesView-BYz2Dnoq.js","assets/LiveQuotes-Do5pbl4X.js","assets/MarketCalendar-CdUM9FMr.js","assets/MarketClosures-C3UDWTbu.js","assets/MarketTiming-C5dp2itt.js","assets/MarketsPage-2DBY0Ktb.css","assets/MarketsPage-BawXLbKp.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-C6tvw1DL.js","assets/OfficialAlerts-D5zTjRv3.js","assets/PageFooterNote-WoqrP3K4.js","assets/PeRiverCard-CQuBlGuS.css","assets/PeRiverCard-DHcpbEdG.js","assets/PeRiverChart-BMaYAbB0.js","assets/RatesView-D2zAegcm.js","assets/RelativeStrengthPanel-Qzj0gfDr.js","assets/ReportGate-DmuQFaDr.js","assets/ScreenerPage-DMVE4ZJ3.js","assets/ScreenerPage-DvH3rwAO.css","assets/SecRadar-CkFnvm6C.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-ZgchHxpA.js","assets/StockResearch-B7r9m3g0.js","assets/ThemesPage-CkBlOJ0v.js","assets/ThemesPage-CrSh5QYG.css","assets/TimeChart-CC2JEwXG.js","assets/VolumePanel-_e0r1JAI.js","assets/WarrantsPage-CCLJS2ag.js","assets/WarrantsPage-CLyxZDYg.css","assets/WorldAssetsView-DS4dSOdj.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-CrFsVLFM.js","assets/analysisComparisons-CvnJVehW.js","assets/analysisComparisons-D_nmfxi1.css","assets/arrow-up-DOZXHLXL.js","assets/breakouts-1XbjrNEv.js","assets/breakouts-G-l8GZ_z.css","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-cV261RAq.js","assets/chart-h2tyZhBG.js","assets/chevron-down-BWR6lisa.js","assets/dataSources-Xl1efQi8.js","assets/index-BbUf_rCk.js","assets/index-C9wmOjzu.css","assets/index-jh05VtuY.js","assets/layers-C69afY3z.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/listPicker-C9oyBpcs.js","assets/listPicker-CY6ftdhn.css","assets/loadFailureReason-C63VtXmu.js","assets/qualityLabels-C9wDq_QB.js","assets/quoteStatus-DteMsDnq.js","assets/rates-CSQxt-s6.css","assets/rates-mxMFlqV_.js","assets/react-DPIr46qh.js","assets/sliders-horizontal-B4oXeHFg.js","assets/storage-bv9aEjHy.js","assets/useDialogFocus-DUBSEF4x.js","assets/worldPerformance-DWYOYGH0.js"];

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
