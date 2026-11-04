import { useEffect, useState } from "react";
import {
  fetchExtendedQuotes,
  type ExtendedSnapshot,
} from "../market-data/extendedQuotes";
import type { Quote } from "../market-data/MarketDataProvider";
import { formatPrice } from "./format";
export function ExtendedQuotes({
  symbol,
  quote,
  active,
}: {
  symbol: string;
  quote: Quote | null;
  active: boolean;
}) {
  const [state, setState] = useState<{
    symbol: string;
    data?: ExtendedSnapshot;
    error?: string;
  }>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active) return;
    const abort = new AbortController();
    void fetchExtendedQuotes(symbol, abort.signal).then(
      (data) => {
        if (!abort.signal.aborted) setState({ symbol, data });
      },
      (error) => {
        if (!abort.signal.aborted)
          setState({
            symbol,
            error:
              error instanceof Error && /尚未收錄|HTTP/.test(error.message)
                ? error.message
                : "延長時段快照連線失敗或格式無效",
          });
      },
    );
    return () => abort.abort();
  }, [symbol, active, attempt]);
  const current = state?.symbol === symbol ? state : null;
  const time = (asOf: number) =>
    new Intl.DateTimeFormat("zh-TW", {
      timeZone: "America/New_York",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(asOf * 1000);
  return (
    <div className="extended-quotes" aria-label="美股盤前盤後價">
      {(["pre", "post"] as const).map((mode) => {
        const inline =
          quote?.symbol === symbol ? quote.extended?.[mode] : undefined;
        const snapshot = current?.data?.[mode];
        const recorded =
          snapshot?.price && snapshot.asOf
            ? { price: snapshot.price, asOf: Date.parse(snapshot.asOf) / 1000 }
            : undefined;
        const value =
          inline && (!recorded || inline.asOf >= recorded.asOf)
            ? inline
            : recorded;
        const valid = value && value.asOf <= Date.now() / 1000 + 300;
        const page = mode === "pre" ? "pre-market" : "after-hours";
        return (
          <section key={mode}>
            <h3>{mode === "pre" ? "盤前" : "盤後"}</h3>
            {valid ? (
              <>
                <b>{formatPrice(value.price)} USD</b>
                <p>成交時間 {time(value.asOf)}（紐約）</p>
                <small>
                  {inline === value ? "Yahoo 延遲行情" : current?.data?.source}
                </small>
                {Date.now() / 1000 - value.asOf > 86400 && (
                  <p>歷史最近可用報價，非今日報價</p>
                )}
              </>
            ) : (
              <p role="status">
                {!current
                  ? "載入盤前盤後報價…"
                  : (snapshot?.reason ??
                    current.error ??
                    "來源未提供此時段成交報價")}
              </p>
            )}
            {snapshot?.status === "stale" && (
              <p role="status">{snapshot.reason}</p>
            )}
            <a
              href={`https://www.nasdaq.com/market-activity/stocks/${encodeURIComponent(symbol.toLowerCase())}/${page}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              查看官方{mode === "pre" ? "盤前" : "盤後"}頁面
            </a>
          </section>
        );
      })}
      <p>
        延長時段與正常交易報價分開；公開快照按排程更新，非即時行情。未成交／尚未收錄不使用收盤價代替。
      </p>
      <button
        type="button"
        className="ghost-button"
        onClick={() => setAttempt((n) => n + 1)}
      >
        重新讀取快照
      </button>
    </div>
  );
}
