const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-671be87e0a93`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-DZITfx8e.js","assets/ChartPage-BUkzj6uQ.js","assets/ChartPage-DVGcjQfq.css","assets/Common-BkZb3-Zj.js","assets/Common-ghF4RX17.css","assets/ConsolePage-D83_kb6x.js","assets/ConsolePage-DJrfLPTW.css","assets/DrawableCandleChart-CfntcGuh.css","assets/DrawableCandleChart-DQMpWFXQ.js","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-C7n-4dSw.js","assets/JapanRatesView-ThbQMYtq.js","assets/MarketsPage-BtFxL4eV.css","assets/MarketsPage-dxWbgVTR.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-BUkXSWdA.js","assets/OfficialAlerts-BswNXYSU.js","assets/PageFooterNote-B_KBF2M3.js","assets/PeRiverCard-CJkdl3F4.js","assets/PeRiverCard-DzbIOZza.css","assets/PeRiverChart-Dh37cEQl.js","assets/RatesView-D17tohY7.js","assets/RelativeStrengthPanel-Ci9s6x9-.js","assets/ReportGate-CLVvPSUO.js","assets/ScreenerPage-ktNDUDOo.css","assets/ScreenerPage-lzsxZd9x.js","assets/SecRadar-D9--uREF.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-BO23H-XG.js","assets/StockResearch-DbpNTyen.js","assets/ThemesPage-B8KuRf37.js","assets/ThemesPage-CrSh5QYG.css","assets/TimeChart-IqMBDHWV.js","assets/VolumePanel-DHKrRFE8.js","assets/WorldAssetsView-MNK4Of_j.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-MrI2RrUf.js","assets/analysisComparisons-CvnJVehW.js","assets/analysisComparisons-D_nmfxi1.css","assets/arrow-up-Dl4pg79L.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-CxdXlt2r.js","assets/chart-h2tyZhBG.js","assets/check-UXHNeQUI.js","assets/chevron-down-DXA5CtEB.js","assets/dataSources-CsKMx7R_.js","assets/earth-Da_EZJ-i.js","assets/index-4c25gM3L.js","assets/index-jh05VtuY.js","assets/index-r6u3YA4b.css","assets/layers-BKhj5-ke.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/listPicker-CY6ftdhn.css","assets/listPicker-DIqnqwte.js","assets/loadFailureReason-RmGf2nvx.js","assets/quoteStatus-BagmeIic.js","assets/rates-CSQxt-s6.css","assets/rates-sbOt8WiN.js","assets/react-DPIr46qh.js","assets/research-DmjvFtwq.js","assets/sliders-horizontal-DkzZlw_i.js","assets/storage-C-QRaIK8.js","assets/trash-2-DQ0blXOD.js","assets/useDialogFocus-DDZfHLGw.js","assets/worldPerformance-DWYOYGH0.js"];

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
