import { HelpDisclosure } from '../ui/HelpDisclosure';
import { useEffect, useState } from "react";
import {
  worldAssetsSchema,
  researchUrl,
  type WorldAsset,
} from "../report/research";
import { researchBars } from "../report/researchCandles";
import type { Bar } from "../market-data/MarketDataProvider";
import { DrawableCandleChart } from "../ui/DrawableCandleChart";
import { useNearViewport } from "../ui/MiniChart";
import { Provenance, Status, useResearch } from "../ui/research/Common";
import type { PageProps } from './pageProps';
import { WORLD_ASSETS } from '../market-data/worldAssets';
import { WorldAssetProvider } from '../market-data/WorldAssetProvider';
import { formatPrice, formatPercent, toneClass } from '../ui/format';
const keys = {
  commodities: ["wti", "brent", "gas", "gold", "silver", "copper", "btcusdt"],
  fx: ["usdtwd", "usdjpy", "eurusd", "usdcny", "usdkrw"],
};
function AssetChart({ asset, id, retainedBars, openChart }: { asset: WorldAsset; id: string; retainedBars?: Bar[] } & Pick<PageProps, 'openChart'>) {
  const definition = WORLD_ASSETS.find(item => item.id === id)!;
  const { ref, near } = useNearViewport<HTMLElement>("300px");
  const [loaded, setLoaded] = useState<{ bars: Bar[]; error?: string }>();
  useEffect(() => {
    if (!near || retainedBars || !asset.candlePath || !asset.symbol) return;
    const abort = new AbortController();
    void fetch(researchUrl(asset.candlePath.replace(/\.json$/, "")), {
      signal: abort.signal,
      cache: "no-cache",
    })
      .then(async (response) => {
        if (!response.ok)
          throw new Error(`K線資料暫時無法取得（HTTP ${response.status}）。`);
        const body = await response.json();
        const bars = researchBars(body, asset.symbol!);
        if (
          body.bars.at(-1)?.[0] !== asset.asOf ||
          bars.at(-1)?.close !== asset.latest
        )
          throw new Error("K線與報價快照正在更新，請重新載入。");
        return bars;
      })
      .then(
        (bars) => {
          if (!abort.signal.aborted) setLoaded({ bars });
        },
        (e) => {
          if (!abort.signal.aborted)
            setLoaded({
              bars: [],
              error: e instanceof Error ? e.message : "K線載入失敗",
            });
        },
      );
    return () => abort.abort();
  }, [near, retainedBars, asset.candlePath, asset.symbol]);
  return (
    <article ref={ref} className="card world-candle-card">
      <h3>{asset.name ?? id}</h3>
      <button type="button" className="ghost-button" onClick={() => openChart({ symbol: definition.symbol, name: asset.name, tf: '1D' })}>開啟完整圖表 · 均線／畫線</button>
      <p className="source-note">
        {asset.instrumentType === "futures"
          ? `期貨 · ${asset.contract ?? asset.providerSymbol ?? ""}（來源合約標籤）`
          : asset.instrumentType === "crypto-spot"
            ? `現貨 · ${asset.providerSymbol ?? ""} · ${asset.quoteDirection} · 成交量 ${asset.volumeUnit ?? asset.baseCurrency}`
            : (asset.quoteDirection ?? "尚未取得OHLC報價")}{" "}
        · {asset.unit ?? ""} · 資料日 {asset.asOf ?? "尚未確認"}{retainedBars ? " · 保存資料" : ""}
      </p>
      {near ? (
        <DrawableCandleChart
          key={asset.symbol ?? id}
          identity={`world:${asset.symbol ?? id}`}
          bars={retainedBars ?? loaded?.bars ?? []}
          source={asset.name ?? id}
          unit={asset.unit ?? ""}
          precision={asset.pricePrecision ?? (asset.baseCurrency ? 4 : 2)}
          calendar={asset.instrumentType === 'crypto-spot' ? 'continuous-utc' : 'regular'}
          showIndicators
          loading={!retainedBars && !loaded && !!asset.candlePath}
          error={
            loaded?.error ??
            (!asset.candlePath ? "尚未取得真正的開高低收資料。" : undefined)
          }
        />
      ) : (
        <div className="candle-host skeleton" />
      )}
      {!!asset.excludedInvalidOHLC && (
        <p className="source-note">
          本次來源資料有 {asset.excludedInvalidOHLC}{" "}
          筆開高低收不一致，已排除並保留缺口。
        </p>
      )}
      <Provenance data={asset} />
    </article>
  );
}

function BitcoinChart({ asset, openChart }: { asset?: WorldAsset } & Pick<PageProps, 'openChart'>) {
  const [retained, setRetained] = useState<{ asset: WorldAsset; bars: Bar[] }>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (asset !== undefined) return;
    let live = true;
    void import('../report/btcUsdtArchive').then(module => module.loadBtcUsdtArchive()).then(
      result => { if (live) setRetained(result); },
      reason => { if (live) setError(reason instanceof Error ? reason.message : 'BTC/USDT 保存行情暫時無法載入。'); },
    );
    return () => { live = false; };
  }, [asset]);
  if (asset !== undefined) {
    if (asset.candlePath && (asset.symbol !== 'CRYPTO-BTCUSDT' || asset.providerSymbol !== 'BTCUSDT' ||
        asset.baseCurrency !== 'BTC' || asset.quoteCurrency !== 'USDT' || asset.instrumentType !== 'crypto-spot'))
      return <article className="card world-candle-card"><h3>BTC/USDT</h3><Status error="BTC/USDT 來源商品或報價幣別不符，暫不顯示行情。" /></article>;
    return <AssetChart asset={{ ...asset, name: asset.name ?? 'BTC/USDT' }} id="btcusdt" openChart={openChart} />;
  }
  if (retained) return <AssetChart asset={retained.asset} id="btcusdt" retainedBars={retained.bars} openChart={openChart} />;
  return <article className="card world-candle-card"><h3>BTC/USDT</h3><Status error={error} /></article>;
}
const assetProvider = new WorldAssetProvider();
function AssetRow({ id, asset, openChart }: { id: string; asset?: WorldAsset } & Pick<PageProps, 'openChart'>) {
  const definition = WORLD_ASSETS.find(item => item.id === id)!;
  const [loaded, setLoaded] = useState<{ asset: WorldAsset; bars: Bar[] }>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    void assetProvider.load(definition.symbol, controller.signal, asset).then(result => {
      if (!controller.signal.aborted) setLoaded(result);
    }, () => { if (!controller.signal.aborted) setError('報價暫時無法取得'); });
    return () => controller.abort();
  }, [definition.symbol, asset]);
  const quote = loaded?.asset ?? asset;
  const previous = loaded?.bars.at(-2)?.close;
  const latest = loaded?.bars.at(-1)?.close;
  const change = previous !== undefined && latest !== undefined ? (latest / previous - 1) * 100 : null;
  return <button type="button" className="asset-list-row" onClick={() => openChart({ symbol: definition.symbol, name: quote?.name ?? definition.name, tf: '1D' })}>
    <span><b>{quote?.name ?? definition.name}</b><small>{definition.ticker} · {quote?.unit ?? definition.unit}</small></span>
    <span className={toneClass(change)}><b>{formatPrice(latest ?? (error ? undefined : quote?.latest), quote?.pricePrecision ?? definition.precision)}</b><small>{formatPercent(change)}</small></span>
    <small>{error ?? `${quote?.asOf ?? '載入中…'}${quote?.status === 'stale' ? ' · 來源過期' : ''}`}</small>
  </button>;
}
export default function WorldAssetsView({
  view,
  openChart,
}: {
  view: "commodities" | "fx";
} & Pick<PageProps, 'openChart'>) {
  const [gallery, setGallery] = useState(false);
  const { data, error } = useResearch("world-assets", worldAssetsSchema);
  if (!data) return <Status error={error} />;
  return (
    <>
      <div className="asset-view-controls" role="group" aria-label="商品顯示方式">
        <button type="button" className="ghost-button" aria-pressed={!gallery} onClick={() => setGallery(false)}>清單</button>
        <button type="button" className="ghost-button" aria-pressed={gallery} onClick={() => setGallery(true)}>連續看全部圖表（{keys[view].length}）</button>
      </div>
      <HelpDisclosure label="報價與商品說明">
        {view === "commodities"
          ? "原物料期貨日K；來源按近月合約銜接，展期日期與價差調整規則未完整揭露，非現貨價格。BTC/USDT 為 Binance 現貨，全天交易、以 UTC 日線統計，報價幣為 USDT。"
          : "匯率日K為延遲參考報價；非銀行牌告或 Fed 統計匯率，報價方向逐組標示。"}
      </HelpDisclosure>
      {!gallery ? <section className="card asset-list" aria-label={view === 'fx' ? '匯率清單' : '原物料清單'}>
        {keys[view].map(id => <AssetRow key={id} id={id} asset={data.series[id]} openChart={openChart} />)}
      </section> : <div>
        {keys[view].map((id) =>
          id === "btcusdt" ? <BitcoinChart key={id} asset={data.series[id]} openChart={openChart} /> : data.series[id] ? (
            <AssetChart key={id} id={id} asset={data.series[id]} openChart={openChart} />
          ) : (
            <p key={id} role="status">
              {id === "btcusdt" ? "BTC/USDT" : id} 暫無K線資料
            </p>
          ),
        )}
      </div>}
    </>
  );
}
