import { useState } from "react";
import {
  volumeSchema,
  type VolumeResearch,
} from "../../report/research";
import {
  num,
  Provenance,
  ResearchDisclosure,
  ResearchNote,
  Stat,
  Status,
  useResearch,
} from "./Common";
import { VolumeRatios } from "./VolumeRatios";

export function VolumePanel({ symbol, inline = false }: { symbol: string; inline?: boolean }) {
  if (inline) return <section className="volume-analysis-results" aria-label="價量分析結果"><VolumeData symbol={symbol} /></section>;
  return (
    <ResearchDisclosure key={symbol} title="價量分析 · 異常量與放量／縮量比例">
      <VolumeData symbol={symbol} />
    </ResearchDisclosure>
  );
}

function VolumeData({ symbol }: { symbol: string }) {
  const { data, error } = useResearch(
    `volume/${encodeURIComponent(symbol)}`,
    volumeSchema,
    symbol,
  );
  if (!data) return <Status error={error} />;
  return <VolumeAnalysis data={data} />;
}

export function VolumeAnalysis({ data }: { data: VolumeResearch }) {
  const [abnormal, setAbnormal] = useState(2),
    [quiet, setQuiet] = useState(0.5);
  const abnormalState =
    data.latest.rvol === null
      ? "有效歷史不足"
      : data.latest.rvol >= abnormal
        ? "異常放量"
        : data.latest.rvol <= quiet
          ? "異常縮量"
          : "門檻範圍內";
  return (
    <>
      <section className="volume-analysis-ratios" aria-label="上漲／下跌放量與縮量比例">
        <VolumeRatios features={data.features}>
          <p>
            分母為有效上漲／下跌日；無有效上漲或下跌日為
            N/A。放量／縮量只比較當日與前一個交易日的成交量；相同成交量不歸入任一類。
          </p>
          <p>
            固定使用既有已收盤日
            K；零量、缺值、停牌相鄰日、已知公司行動及未確認的大幅跳價不產生價量訊號。價格採來源拆股還原、未還原股息；成交量調整無法獨立核實，除權息／拆股仍可能影響比較。
          </p>
          {data.risks.map((risk) => (
            <p key={risk}>{risk}</p>
          ))}
        </VolumeRatios>
      </section>
      <section className="volume-analysis-anomaly" aria-label="異常量">
        <h3>異常量</h3>
        <dl className="research-stats">
          <Stat
            label="異常量 RVOL20"
            value={`${num(data.latest.rvol)} · ${abnormalState}`}
          />
        </dl>
        <div className="research-controls">
          <label>
            異常放量 RVOL ≥
            <input
              aria-label="異常放量門檻"
              type="number"
              min="1"
              step="0.1"
              value={abnormal}
              onChange={(e) => setAbnormal(Math.max(1, Number(e.target.value)))}
            />
          </label>
          <label>
            異常縮量 RVOL ≤
            <input
              aria-label="異常縮量門檻"
              type="number"
              min="0.01"
              max="1"
              step="0.1"
              value={quiet}
              onChange={(e) =>
                setQuiet(Math.max(0.01, Math.min(1, Number(e.target.value))))
              }
            />
          </label>
        </div>
        <ResearchNote title="異常量計算說明">
          <p>異常量 RVOL20 比較當日前 20 個有效成交日均量，不含未來資料。</p>
        </ResearchNote>
      </section>
      <Provenance data={data} />
    </>
  );
}
