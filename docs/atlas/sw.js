const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-1cdb7b72b730`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-C-vQ3YDT.js","assets/ChartEngine-rO-pV6wc.css","assets/ChartPage-Ccit_JWJ.css","assets/ChartPage-DNBGnMmR.js","assets/Common-DK8XgHBt.js","assets/Common-ghF4RX17.css","assets/ConsoleDialog-BwnKj-PB.js","assets/ConsolePage-CE859C9d.css","assets/ConsolePage-DpdTscly.js","assets/DrawableCandleChart-ClsvvBOe.css","assets/DrawableCandleChart-D4QtKbBa.js","assets/FinancialsPage-Dg5EyRxz.js","assets/FinancialsPage-UUAW98dz.css","assets/JapanRatesView-CT9JGVug.js","assets/LiveQuotes-Db8uPJX8.js","assets/MarketCalendar-BvWs-E_6.js","assets/MarketClosures-D7AddrE7.js","assets/MarketTiming-DxqTWKzA.js","assets/MarketsPage-2DBY0Ktb.css","assets/MarketsPage-kP_Y4PhR.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-Ds3yf1-1.js","assets/OfficialAlerts-CiQ8es8K.js","assets/PageFooterNote-BMSNH6Kx.js","assets/PeRiverCard-BqCpZhNN.js","assets/PeRiverCard-CQuBlGuS.css","assets/PeRiverChart-B25GVGFW.js","assets/RatesView-C1PbwKq7.js","assets/RelativeStrengthPanel-BixBMuRU.js","assets/ReportGate-M4QtQjZ4.js","assets/ScreenerPage-CR5EoC3D.js","assets/ScreenerPage-DvH3rwAO.css","assets/SecRadar-D0kRZG3h.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-DgYicxIL.js","assets/StockResearch-Dg2Jhqie.js","assets/ThemesPage-CrSh5QYG.css","assets/ThemesPage-DwapI8MU.js","assets/TimeChart-D5B39RoK.js","assets/VolumePanel-DhhFw9hi.js","assets/WarrantsPage-BJes2HnV.js","assets/WarrantsPage-CLyxZDYg.css","assets/WorldAssetsView-CLdZuelv.js","assets/WorldPage-CGQFiny3.js","assets/WorldPage-CofhzqT9.css","assets/analysisComparisons-CvnJVehW.js","assets/analysisComparisons-D_nmfxi1.css","assets/arrow-up-Ct-wSvP5.js","assets/breakouts-C-dxff7O.js","assets/breakouts-G-l8GZ_z.css","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-Cw8YXLRs.js","assets/chart-h2tyZhBG.js","assets/chevron-down-DMu6AjqE.js","assets/dataSources-5SD7SpDM.js","assets/index-C9wmOjzu.css","assets/index-jh05VtuY.js","assets/index-xKB0_hQD.js","assets/layers-CI6wLxdQ.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/listPicker-CY6ftdhn.css","assets/listPicker-Cpw2Xxai.js","assets/loadFailureReason-BORBSegD.js","assets/qualityLabels-k2c7Y15V.js","assets/quoteStatus-B43m2KDS.js","assets/rates-B1F9L6-N.js","assets/rates-CSQxt-s6.css","assets/react-DPIr46qh.js","assets/sliders-horizontal-CXuQFAGa.js","assets/storage-bv9aEjHy.js","assets/useDialogFocus-BPdpkKcd.js","assets/worldPerformance-DWYOYGH0.js"];

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
