const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-628fead7818d`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-DaiC_Reg.js","assets/ChartEngine-rO-pV6wc.css","assets/ChartPage-Ccit_JWJ.css","assets/ChartPage-DTV7P3Z8.js","assets/Common-DPsf-fXg.js","assets/Common-ghF4RX17.css","assets/ConsoleDialog-o3gqKNnr.js","assets/ConsolePage-4Ix67rLh.js","assets/ConsolePage-B5c2n1Bo.css","assets/DrawableCandleChart-ClsvvBOe.css","assets/DrawableCandleChart-hUWj4KOO.js","assets/FinancialsPage-D2xSwURy.js","assets/FinancialsPage-UUAW98dz.css","assets/JapanRatesView-KgOJyLR6.js","assets/LiveQuotes-C8cTuNzj.js","assets/MarketCalendar-DRhb-JNU.js","assets/MarketClosures-fy5GiEmx.js","assets/MarketTiming-B6wgeGgD.js","assets/MarketsPage-2DBY0Ktb.css","assets/MarketsPage-CESj1_aL.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-x1ahiksf.js","assets/OfficialAlerts-CMD0CZrd.js","assets/PageFooterNote-C3OGosbE.js","assets/PeRiverCard-B1QCJYcW.js","assets/PeRiverCard-CQuBlGuS.css","assets/PeRiverChart-CH7Tvr3k.js","assets/RatesView-BuD4Mqji.js","assets/RelativeStrengthPanel-UgBiuyD8.js","assets/ReportGate-Bzl_Is4u.js","assets/ScreenerPage-BWj7ML9X.js","assets/ScreenerPage-DvH3rwAO.css","assets/SecRadar-DW8y6Fzp.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-DdI0RMVe.js","assets/StockResearch-D-feEBF8.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-DaJTvWCb.js","assets/TimeChart-Do_UITIH.js","assets/VolumePanel-DqYj8Sf-.js","assets/WarrantsPage-CLyxZDYg.css","assets/WarrantsPage-D-Te4w2Z.js","assets/WorldAssetsView-DL32nkXC.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-DXZ4JDJJ.js","assets/analysisComparisons-CvnJVehW.js","assets/analysisComparisons-D_nmfxi1.css","assets/arrow-up-DuJKeCAT.js","assets/breakouts-CVv5oI_n.js","assets/breakouts-G-l8GZ_z.css","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-BxLlreDq.js","assets/chart-h2tyZhBG.js","assets/chevron-down-B8nEuSIv.js","assets/dataSources-Bw5Hw9Ur.js","assets/index-C9wmOjzu.css","assets/index-CmC-_Voj.js","assets/index-jh05VtuY.js","assets/layers-B1TzC8m7.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/listPicker-CY6ftdhn.css","assets/listPicker-DNRQ2K54.js","assets/loadFailureReason-C7Z3Yutp.js","assets/qualityLabels-CbDwszJK.js","assets/quoteStatus-DJ_R3tZz.js","assets/rates-CSQxt-s6.css","assets/rates-DYNHBAk8.js","assets/react-DPIr46qh.js","assets/sliders-horizontal-TAoEHwc7.js","assets/storage-bv9aEjHy.js","assets/useDialogFocus-By71JnCC.js","assets/worldPerformance-DWYOYGH0.js"];

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
