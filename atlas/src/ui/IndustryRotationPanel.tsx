import { useEffect, useState } from "react";
import {
  fetchIndustryRotation,
  sortIndustryRotation,
  type IndustryRotation,
} from "../report/industryRotation";
import { sectorLabel } from "../pages/industryModel";
import { HelpDisclosure } from "./HelpDisclosure";
import { formatPercent } from "./format";
import "./analysisComparisons.css";

function IndustryComparison({ market }: { market: "TW" | "US" }) {
  const [data, setData] = useState<IndustryRotation>();
  const [error, setError] = useState(""),
    [attempt, setAttempt] = useState(0);
  const [period, setPeriod] = useState<"d1" | "w1" | "m1" | "m3">("m1");
  const [sort, setSort] = useState<"returnPct" | "upShare" | "volumeRatio">(
    "returnPct",
  );
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    setData(undefined);
    void fetchIndustryRotation(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setData(value);
      })
      .catch((caught) => {
        if (!abort.signal.aborted)
          setError(
            caught instanceof Error ? caught.message : "無法讀取產業比較。",
          );
      });
    return () => abort.abort();
  }, [attempt]);
  const source = data?.markets[market],
    current = source?.periods[period];
  return (
    <div className="analysis-comparison">
      <div className="analysis-controls">
        <label>
          產業比較期間
          <select
            aria-label="產業比較期間"
            value={period}
            onChange={(event) => setPeriod(event.target.value as typeof period)}
          >
            {[
              ["d1", "1 日"],
              ["w1", "1 週"],
              ["m1", "1 個月"],
              ["m3", "3 個月"],
            ].map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          產業比較排序
          <select
            aria-label="產業比較排序"
            value={sort}
            onChange={(event) => setSort(event.target.value as typeof sort)}
          >
            <option value="returnPct">報酬由高到低</option>
            <option value="upShare">上漲比例由高到低</option>
            <option value="volumeRatio">成交量比由高到低</option>
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
            重試產業比較
          </button>
        </p>
      ) : !data ? (
        <p role="status">載入產業比較…</p>
      ) : !current ? (
        <p role="status">此市場尚無產業比較資料。</p>
      ) : (
        <>
          <p className="analysis-caption">
            {current.startDate} → {current.endDate} ·{" "}
            {source?.scope === "full" ? "全市場" : "流動性名單"} ·{" "}
            {market === "TW" ? "官方產業別" : "Sector"}
          </p>
          <ul
            className="analysis-comparison-list"
            aria-label="產業輪動比較結果"
          >
            {sortIndustryRotation(current.rows, sort).map((row) => (
              <li className="analysis-comparison-row" key={row.name}>
                <b>{market === "US" ? sectorLabel(row.name) : row.name}</b>
                <div className="analysis-metrics">
                  <span>報酬 {formatPercent(row.returnPct)}</span>
                  <span>
                    上漲{" "}
                    {row.upShare === null ? "—" : `${row.upShare.toFixed(1)}%`}
                  </span>
                  <span>
                    成交量比{" "}
                    {row.volumeRatio === null
                      ? "—"
                      : `${row.volumeRatio.toFixed(2)} 倍`}
                  </span>
                </div>
                <p>
                  {row.momentumChange === null
                    ? "兩期行情不足，暫無動能變化"
                    : `較前期${row.momentumChange >= 0 ? "改善" : "減弱"} ${Math.abs(row.momentumChange).toFixed(2)} 個百分點`}{" "}
                  · 報酬 {row.validCount}/{row.count} 檔 · 量能{" "}
                  {row.volumeCount}/{row.count} 檔
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
      <HelpDisclosure label="產業比較計算說明">
        報酬為目前產業成員的等權平均，上漲比例只計有有效報酬者。成交量比＝本期總股數÷前一期等長區間總股數，只計兩期皆有完整成交量的相同股票，不代表資金淨流入。動能變化只比較兩期完整行情的共同樣本；成員採目前分類，具有存續偏差。缺少快取或成交量時顯示「—」，不把未知值當零。
      </HelpDisclosure>
    </div>
  );
}
export function IndustryRotationPanel({ market }: { market: "TW" | "US" }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="research-disclosure"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>產業輪動比較</summary>
      <div className="research-disclosure-body">
        {open && <IndustryComparison market={market} />}
      </div>
    </details>
  );
}
