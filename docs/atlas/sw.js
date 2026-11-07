const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-a8643def905e`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-OFJ8n0pu.js","assets/ChartPage-CHUMv1dv.js","assets/ChartPage-KtxOtJeh.css","assets/Common-BYunmZV2.js","assets/Common-ghF4RX17.css","assets/ConsolePage-3-Lt8q-J.css","assets/ConsolePage-CCIwYmqg.js","assets/DrawableCandleChart-CVPeAniX.js","assets/DrawableCandleChart-CfntcGuh.css","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-CMJAxl5g.js","assets/FixedStockParameters-CvsaaXdv.js","assets/JapanRatesView-Bub_lKun.js","assets/MarketsPage-B3xx6yoL.js","assets/MarketsPage-BBqGB-0S.css","assets/MiniChart-4yIdjprr.css","assets/MiniChart-grCEcZji.js","assets/OfficialAlerts-DrVkwEAv.js","assets/PageFooterNote-D401QzVt.js","assets/PeRiverCard-DZq2F-4s.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-3pKW6XiZ.js","assets/RatesView-DFGX1Kbg.js","assets/RelativeStrengthPanel-Bp1Ittdo.js","assets/ReportGate-CSWFzZvA.js","assets/ScreenerPage-D1KiAR0N.js","assets/ScreenerPage-DSwOFJ74.css","assets/SecRadar-Bq2cnW-N.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-D7QDChdK.js","assets/StockResearch-Cdm_Aw3c.js","assets/ThemesPage-CBrY52M-.js","assets/ThemesPage-CrSh5QYG.css","assets/TimeChart-ISgkyxDV.js","assets/VolumePanel-BSYJiAQK.js","assets/WorldAssetsView-DOnAPQie.js","assets/WorldPage-CofhzqT9.css","assets/WorldPage-hALdr3sT.js","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-DzEcf7wF.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-B4wmSDaY.js","assets/chart-h2tyZhBG.js","assets/check-DewNYqde.js","assets/chevron-down-Dy6N7cJe.js","assets/chevron-right-685UnFs8.js","assets/dataSources-B8NbtarP.js","assets/earth-H4zol6qQ.js","assets/index-BaGZ9efi.js","assets/index-CH87S-1G.js","assets/index-CJD5VWRD.css","assets/index-jh05VtuY.js","assets/layers-CzBjcAVI.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-D32x5tWb.js","assets/rates-CSQxt-s6.css","assets/rates-DBTzrSaK.js","assets/react-DPIr46qh.js","assets/research-Buptlf1I.js","assets/storage-DTU9qw1T.js","assets/trash-2-DNPq_QQH.js","assets/useDialogFocus-mioOFE85.js","assets/worldPerformance-DWYOYGH0.js"];

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
