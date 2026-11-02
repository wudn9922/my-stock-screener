import { useEffect, useMemo, useState } from "react";
import { getDailyBarResult } from "../app/dataSources";
import type { BarResult } from "../market-data/MarketDataProvider";
import { getMarketProfile } from "../market-data/MarketProfile";
import { relativeStrength, stockRs } from "../pages/relativeStrength";
import { loadUniverse, type UniverseLoadResult } from '../report/universe';
import { TimeChart } from "./rates/TimeChart";
import { HelpDisclosure } from "./HelpDisclosure";
import { formatPercent } from "./format";
import "./analysisComparisons.css";

const percent = (value: number) => `${value.toFixed(1)}%`;
export function RelativeStrengthPanel({ symbol }: { symbol: string }) {
  const tw = getMarketProfile(symbol).market === "TW";
  const [benchmark, setBenchmark] = useState(tw ? "^TWII" : "SPY");
  const [sessions, setSessions] = useState(63);
  const [loaded, setLoaded] = useState<{
    stock: BarResult;
    benchmark: BarResult;
  }>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [universe, setUniverse] = useState<UniverseLoadResult>();
  useEffect(() => {
    let active = true;
    void loadUniverse().then(result => { if (active) setUniverse(result); });
    return () => { active = false; };
  }, [symbol]);
  const rs = universe?.status === 'ok' ? stockRs(universe.universe, symbol) : null;
  useEffect(() => {
    let active = true;
    setLoaded(undefined);
    setError("");
    void Promise.all([getDailyBarResult(symbol), getDailyBarResult(benchmark)])
      .then(([stock, benchmark]) => {
        if (active) setLoaded({ stock, benchmark });
      })
      .catch(() => {
        if (active) setError("無法取得比較行情，請稍後重試。");
      });
    return () => {
      active = false;
    };
  }, [symbol, benchmark, attempt]);
  const comparison = useMemo(
    () =>
      loaded
        ? relativeStrength(
            loaded.stock.bars,
            loaded.benchmark.bars,
            symbol,
            benchmark,
            sessions,
          )
        : null,
    [loaded, symbol, benchmark, sessions],
  );
  const lines = useMemo(
    () =>
      comparison
        ? [
            {
              id: symbol,
              color: "#3987e5",
              points: comparison.points.map((point) => ({
                time: point.time,
                value: point.stock,
              })),
            },
            {
              id: benchmark,
              color: "#c98500",
              points: comparison.points.map((point) => ({
                time: point.time,
                value: point.benchmark,
              })),
            },
          ]
        : [],
    [comparison, symbol, benchmark],
  );
  return (
    <section className="analysis-comparison" aria-label="個股與大盤強弱比較">
      <h3>相對大盤</h3>
      <div className="analysis-rs" data-testid="stock-rs">
        <span>RS 百分位 <small>1–99</small></span>
        <b>{rs ? rs.rank : '—'}</b>
        <small>{!universe ? '載入全市場 RS…' : rs
          ? `${tw ? '台股' : '美股'}全市場 · 基準 ${rs.benchmark === '^TWII' ? '加權指數' : rs.benchmark} · 資料日 ${rs.date}${rs.stale ? '（舊資料）' : ''}`
          : universe.status === 'error' ? '全市場 RS 暫時無法取得' : '全市場 RS 尚無可用資料'}</small>
      </div>
      <div className="analysis-controls">
        <label>
          比較基準
          <select
            aria-label="比較基準"
            value={benchmark}
            onChange={(event) => setBenchmark(event.target.value)}
          >
            {(tw
              ? [
                  ["^TWII", "加權指數"],
                  ["^TWOII", "櫃買指數"],
                ]
              : [
                  ["SPY", "標普 500（SPY）"],
                  ["^IXIC", "NASDAQ"],
                  ["^GSPC", "標普 500 指數"],
                ]
            ).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          比較期間
          <select
            aria-label="相對強弱期間"
            value={sessions}
            onChange={(event) => setSessions(Number(event.target.value))}
          >
            {[
              [21, "1 個月"],
              [63, "3 個月"],
              [126, "6 個月"],
              [252, "1 年"],
            ].map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error ? (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            className="ghost-button"
            onClick={() => setAttempt((n) => n + 1)}
          >
            重試比較
          </button>
        </p>
      ) : !loaded ? (
        <p role="status">載入比較行情…</p>
      ) : !comparison ? (
        <p role="status">共同交易日不足，暫無法比較。</p>
      ) : (
        <>
          <p className="analysis-stat" data-testid="relative-strength-result">
            {symbol} {formatPercent(comparison.stockReturn)} · {benchmark}{" "}
            {formatPercent(comparison.benchmarkReturn)}
            <br />
            <b>
              {comparison.excess >= 0 ? "領先" : "落後"}大盤{" "}
              {Math.abs(comparison.excess).toFixed(2)} 個百分點
            </b>
          </p>
          <TimeChart
            lines={lines}
            format={percent}
            ariaLabel="個股與大盤累積報酬比較"
            zeroLine
            className="analysis-time-chart"
          />
          <p className="analysis-caption">
            {comparison.start} → {comparison.end} · {comparison.sessions}{" "}
            個共同交易日 · 藍：{symbol}／金：{benchmark}
          </p>
          <HelpDisclosure label="相對強弱計算與來源">
            RS 百分位沿用全市場選股報表的 3 個月相對強弱排名（1–99），越高越強；固定使用報表的市場基準與資料日，不隨下方比較期間或基準切換。未有全市場排名時不推估。
            共同起點歸零，比較價格累積報酬；超額報酬＝個股報酬減大盤報酬，不含股息。僅使用雙方皆有資料的交易日，不補值。
            {comparison.sessions < sessions
              ? "歷史不足所選期間，顯示實際可比範圍。"
              : ""}
            來源：{loaded.stock.source}／{loaded.benchmark.source}
            ，延遲行情，最新日可能仍在交易中。
          </HelpDisclosure>
        </>
      )}
    </section>
  );
}
