const scopeURL = new URL(self.registration.scope);
const scopePath = scopeURL.pathname.endsWith('/')
  ? scopeURL.pathname
  : `${scopeURL.pathname}/`;
const scopeRoot = new URL(scopePath, scopeURL.origin);
const CACHE_PREFIX = `atlas-shell-${encodeURIComponent(self.registration.scope)}-`;
const CACHE = `${CACHE_PREFIX}atlas-shell-v1-c3721b9a74d5`;
const SCREENER_MEMORY_ONLY = scopePath.startsWith('/my-stock-screener/');
const manifestURL = new URL('manifest.webmanifest', scopeRoot);
const iconURLs = ['icon.svg', 'icon-192.png', 'icon-512.png'].map(
  (path) => new URL(path, scopeRoot),
);
const assetsPath = new URL('assets/', scopeRoot).pathname;
// Replaced with public JS/CSS chunk names by the existing versioned-build plugin.
const BUILD_ASSETS = ["assets/ChartEngine-DT8KLN-R.js","assets/ChartPage-Dy9WD1EI.js","assets/ChartPage-KtxOtJeh.css","assets/Common-CQ_qciai.js","assets/Common-ghF4RX17.css","assets/ConsolePage-D3bKqDe6.css","assets/ConsolePage-DSSHKd3l.js","assets/DrawableCandleChart-CfntcGuh.css","assets/DrawableCandleChart-Ci2tFvAb.js","assets/FinancialsPage-7WFXsGuJ.css","assets/FinancialsPage-UKi2ZegD.js","assets/FixedStockParameters-CPa6tyyi.js","assets/JapanRatesView-Boy17pR9.js","assets/MarketsPage-Dd0bv2Qy.css","assets/MarketsPage-Dm82E6GQ.js","assets/MiniChart-4yIdjprr.css","assets/MiniChart-5FA882Uf.js","assets/OfficialAlerts-DVyhAnbL.js","assets/PageFooterNote-Cs0KYKPm.js","assets/PeRiverCard-Cqi63eIG.js","assets/PeRiverCard-vJDElTOD.css","assets/PeRiverChart-BfJuXFUU.js","assets/RatesView-DkSy6daa.js","assets/RelativeStrengthPanel-PvFTnaI2.js","assets/ReportGate-BVvWrFAB.js","assets/ScreenerPage-BYzES-4C.js","assets/ScreenerPage-ktNDUDOo.css","assets/SecRadar-KTK-zxHY.js","assets/StockChartList-8VHfhKaK.css","assets/StockChartList-DQuiEEWo.js","assets/StockResearch-CBQK5Opo.js","assets/ThemesPage-Cqfh33ns.js","assets/ThemesPage-CrSh5QYG.css","assets/TimeChart-B-x_MWvB.js","assets/VolumePanel-B_tcDrPv.js","assets/WorldAssetsView-CA7dYUBL.js","assets/WorldPage-CK13ntSc.js","assets/WorldPage-CofhzqT9.css","assets/analysisComparisons-B7vdqb8J.css","assets/analysisComparisons-cgoG4Cyx.js","assets/arrow-up-C0nk27C3.js","assets/breakouts-backfill-DShxafvh.js","assets/btcUsdtArchive-CSlKU43W.js","assets/chart-h2tyZhBG.js","assets/check-Bvpw063w.js","assets/chevron-down-59R2LH29.js","assets/dataSources-eQmJNEed.js","assets/earth-BADBu9o0.js","assets/index-BrNRKCMX.js","assets/index-CqWhyZyJ.css","assets/index-jh05VtuY.js","assets/layers-1EJle2Px.js","assets/lineLogin-BVkBchhr.js","assets/lineLoginConfig-CDuRX9og.js","assets/loadFailureReason-fHJn2ZAB.js","assets/quoteStatus-DKR4pdkT.js","assets/rates-CSQxt-s6.css","assets/rates-mbxh3_Lt.js","assets/react-DPIr46qh.js","assets/research-P-BjdQNI.js","assets/storage-aKsqrkUr.js","assets/trash-2-B0dlGu-2.js","assets/useDialogFocus-D44owc4j.js","assets/worldPerformance-DWYOYGH0.js"];

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
