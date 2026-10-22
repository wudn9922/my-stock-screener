import { useState } from "react";
import { worldAssetsSchema, type WorldAsset } from "../report/research";
import {
  HistoryPlot,
  num,
  pct,
  Provenance,
  Stat,
  Status,
  useResearch,
} from "../ui/research/Common";
const keys = {
  commodities: ["wti", "brent", "gas", "gold", "silver", "copper"],
  fx: ["usdtwd", "usdjpy", "eurusd", "usdcny", "usdkrw"],
};
const periods: Record<string, string> = {
  "1d": "1 日",
  "1w": "1 週",
  "1m": "1 月",
  "3m": "3 月",
  ytd: "今年以來",
  "1y": "1 年",
};
function AssetCard({ asset }: { asset: WorldAsset }) {
  const [window, setWindow] = useState("1Y");
  const days =
    window === "1M"
      ? 31
      : window === "3M"
        ? 93
        : window === "1Y"
          ? 366
          : window === "5Y"
            ? 1830
            : Infinity;
  const last = asset.history?.at(-1)?.[0];
  const start = last ? Date.parse(last) - days * 86400000 : 0;
  const history = asset.history?.filter(([d]) => Date.parse(d) >= start) ?? [];
  return (
    <article className="card">
      <h3>{asset.name ?? "資料尚未取得"}</h3>
      <dl className="research-stats">
        <Stat
          label={`最新可用價格 · ${asset.unit ?? "單位待確認"}`}
          value={num(asset.latest, asset.baseCurrency ? 4 : 2)}
          note={`${asset.asOf ?? "日期未確認"} · ${asset.frequency === "monthly" ? "月資料（整月均價）" : asset.frequency === "weekly" ? "週資料" : "每日統計，延遲發布"}`}
        />
      </dl>
      <p className="source-note">
        {asset.quoteDirection ??
          (asset.instrumentType === "spot"
            ? "現貨價格；非期貨"
            : "實體市場月均基準；非每日現貨或期貨")}{" "}
        {asset.baseCurrency &&
          `（基準 ${asset.baseCurrency}／報價 ${asset.quoteCurrency}）`}
      </p>
      <dl className="research-stats">
        {Object.entries(periods).map(([key, label]) => (
          <Stat
            key={key}
            label={label}
            value={pct(asset.changes?.[key])}
            note={
              asset.frequency === "monthly" && (key === "1d" || key === "1w")
                ? "月資料不適用"
                : undefined
            }
          />
        ))}
      </dl>
      <details>
        <summary>歷史走勢與資料來源</summary>
        <div className="research-controls">
          <label>
            區間
            <select value={window} onChange={(e) => setWindow(e.target.value)}>
              {["1M", "3M", "1Y", "5Y", "ALL"].map((w) => (
                <option key={w}>{w}</option>
              ))}
            </select>
          </label>
        </div>
        <HistoryPlot
          points={history}
          label={`${asset.name}歷史${asset.frequency === "monthly" ? "月均價" : "統計價格"}`}
          unit={` ${asset.unit ?? ""}`}
        />
        <p className="source-note">
          歷史不補值；變化以區間基準日當日或之前最近有效觀測值計算。每日資料容許最多
          7 日發布落差，月資料 45 日；超過即 N/A。YTD
          相對去年最後一期可用值。月資料日期標示月份起日，並不表示該日即已發布。
        </p>
      </details>
      <Provenance
        data={asset}
        maxAgeDays={asset.frequency === "monthly" ? 62 : 10}
      />
    </article>
  );
}
export default function WorldAssetsView({
  view,
}: {
  view: "commodities" | "fx";
}) {
  const { data, error } = useResearch("world-assets", worldAssetsSchema);
  if (!data) return <Status error={error} />;
  return (
    <>
      <p className="source-note">
        {view === "commodities"
          ? "EIA 每日現貨能源／世界銀行每月原物料基準；各品項頻率與最新觀測日分別標示。"
          : "官方每日統計參考匯率，非即時交易報價或銀行牌告；報價方向逐組標示。"}
      </p>
      <div className="research-world-grid">
        {keys[view].map((key) =>
          data.series[key] ? (
            <AssetCard key={key} asset={data.series[key]} />
          ) : (
            <p role="status" key={key}>
              {key} 尚未取得
            </p>
          ),
        )}
      </div>
    </>
  );
}
