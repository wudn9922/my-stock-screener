import { useEffect, useState } from "react";
import type { MarketDataProvider } from "../../market-data/MarketDataProvider";
import {
  sameTimeVolume,
  type SameTimeVolume,
} from "../../market-data/SameTimeVolume";
import { loadFailureReason } from "../../market-data/loadFailureReason";
import { ResearchNote } from "./Common";

export function SameTimeVolumePanel({
  symbol,
  provider,
}: {
  symbol: string;
  provider: MarketDataProvider;
}) {
  const [state, setState] = useState<{
    symbol: string;
    provider: MarketDataProvider;
    result: SameTimeVolume;
  }>();
  useEffect(() => {
    const abort = new AbortController();
    const load = () =>
      void provider.getBars(symbol, "5m", undefined, abort.signal).then(
        (result) => {
          if (!abort.signal.aborted)
            setState({
              symbol,
              provider,
              result: sameTimeVolume(result, symbol),
            });
        },
        (error) => {
          if (!abort.signal.aborted)
            setState({
              symbol,
              provider,
              result: {
                status: "unavailable",
                reason: loadFailureReason(error),
              },
            });
        },
      );
    load();
    const timer = setInterval(load, 60000);
    return () => {
      clearInterval(timer);
      abort.abort();
    };
  }, [symbol, provider]);
  const result =
    state?.symbol === symbol && state.provider === provider
      ? state.result
      : null;
  const unit = /\.TWO?$/.test(symbol) ? 1000 : 1,
    label = unit === 1000 ? "張" : "股";
  const volume = (n: number) =>
    `${(n / unit).toLocaleString("zh-TW", { maximumFractionDigits: 2 })} ${label}`;
  return (
    <section className="same-time-volume" aria-label="前一交易日同時間量比">
      <h3>前一交易日同時間量比</h3>
      {!result ? (
        <p role="status">載入 5 分鐘成交量…</p>
      ) : result.status === "unavailable" ? (
        <p role="status">無法計算：{result.reason}</p>
      ) : (
        <>
          <b className="same-time-value">{result.ratio.toFixed(2)} 倍</b>
          <p>
            {result.date}／{result.previousDate}，累計至 {result.cutoff}
            （交易所時間）
          </p>
          <p>
            本次 {volume(result.currentVolume)} · 前次{" "}
            {volume(result.previousVolume)}
          </p>
          {result.stale && (
            <p role="status">最近可用交易資料，非今日即時量比</p>
          )}
          <ResearchNote title="同期量比來源與算法">
            <p>
              採已完成的正常交易時段 5 分鐘
              K，同樣累計到上方時間；不含盤前盤後。缺時段、零分母或資料不足不估算。來源：
              {result.source}
              （延遲資料）。週末／休市時比較標示的最近兩個有資料交易日。
            </p>
          </ResearchNote>
        </>
      )}
    </section>
  );
}
