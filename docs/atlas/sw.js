const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-fb9e424cd7e9`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-BnJVXsrt.js","assets/ChartPage-CwfsbgMs.js","assets/ChartPage-KtxOtJeh.css","assets/Common-XzQKV6Cj.js","assets/Common-ghF4RX17.css","assets/ConsolePage-D3bKqDe6.css","assets/ConsolePage-DzclH3DQ.js","assets/DrawableCandleChart-BYv2wRS1.js","assets/DrawableCandleChart-CfntcGuh.css","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-FN34AXlR.js","assets/FixedStockParameters-DzNY79Wg.js","assets/JapanRatesView-1Hxe4910.js","assets/MarketsPage-Dd0bv2Qy.css","assets/MarketsPage-DyYr1-lo.js","assets/MiniChart-0T-Chmv1.js","assets/MiniChart-4yIdjprr.css","assets/OfficialAlerts-CCcsKOBW.js","assets/PageFooterNote-Cgm26pcu.js","assets/PeRiverCard-CE49weCO.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-BeyPpryC.js","assets/RatesView-D007-Kz9.js","assets/RelativeStrengthPanel--2MU_h3_.js","assets/ReportGate-BkBGoOnN.js","assets/ScreenerPage-DFd3xoho.js","assets/ScreenerPage-DSwOFJ74.css","assets/SecRadar-DBUbKStE.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-CJGal-3S.js","assets/StockResearch-BVsm7zvi.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-DPSWLseb.js","assets/TimeChart-Hs9uLVzL.js","assets/VolumePanel-Ker3Scri.js","assets/WorldAssetsView-Ls9iZoyw.js","assets/WorldPage-C9di8Kni.js","assets/WorldPage-CofhzqT9.css","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-D0bb4Uds.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-DU_s0fBt.js","assets/chart-h2tyZhBG.js","assets/check-MnTKbGVo.js","assets/chevron-down-C_5-6Fk4.js","assets/dataSources-D4YANw2y.js","assets/earth-CnZ1d6Z6.js","assets/index-B4qar8Tp.js","assets/index-CGZaehdl.css","assets/index-jh05VtuY.js","assets/layers-C6f5dakB.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-B87nfRJ7.js","assets/rates-CSQxt-s6.css","assets/rates-TJ8-7aEj.js","assets/react-DPIr46qh.js","assets/research-CovMC7Py.js","assets/storage-aKsqrkUr.js","assets/trash-2-DSfpcevS.js","assets/useDialogFocus-DWVXvqQU.js","assets/worldPerformance-DWYOYGH0.js"];

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
