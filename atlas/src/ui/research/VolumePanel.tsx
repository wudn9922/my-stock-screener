import { useState } from "react";
import {
  volumeSchema,
  volumeStructure,
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

export function VolumePanel({ symbol }: { symbol: string }) {
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
  const [window, setWindow] = useState(20),
    [shrink, setShrink] = useState(1),
    [expand, setExpand] = useState(1),
    [abnormal, setAbnormal] = useState(2),
    [quiet, setQuiet] = useState(0.5);
  const structure = volumeStructure(data.features, window, shrink, expand);
  const ratio = (value: typeof structure.upShrink) =>
    `${value.numerator}/${value.denominator} = ${value.pct === null ? "N/A" : num(value.pct, 1) + "%"}`;
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
      <h3>上漲／下跌放量與縮量比例</h3>
      <div className="research-controls">
        <label>
          觀測窗口
          <select
            aria-label="價量觀測窗口"
            value={window}
            onChange={(e) => setWindow(Number(e.target.value))}
          >
            {[5, 10, 20].map((w) => (
              <option key={w} value={w}>
                {w} 個交易日
              </option>
            ))}
          </select>
        </label>
        <label>
          縮量 RVOL &lt;
          <select
            aria-label="結構縮量門檻"
            value={shrink}
            onChange={(e) => setShrink(Number(e.target.value))}
          >
            {[0.5, 0.75, 1].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          放量 RVOL &gt;
          <select
            aria-label="結構放量門檻"
            value={expand}
            onChange={(e) => setExpand(Number(e.target.value))}
          >
            {[1, 1.5, 2].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
      </div>
      <dl className="research-stats">
        <Stat label="上漲縮量比例" value={ratio(structure.upShrink)} />
        <Stat label="下跌放量比例" value={ratio(structure.downExpand)} />
        <Stat label="上漲放量比例" value={ratio(structure.upExpand)} />
        <Stat label="下跌縮量比例" value={ratio(structure.downShrink)} />
      </dl>
      <ResearchNote
        title={`計算說明 · 有效觀測 ${structure.observations}/${window} 日${structure.observations < Math.max(5, window * 0.8) ? " · 樣本不足" : ""}`}
      >
        <p>
          分母為有效上漲／下跌日；無有效上漲或下跌日為
          N/A。相同成交量不歸入放量或縮量。RVOL 使用當日前 20
          個有效成交日均量，不含未來資料。
        </p>
        <p>
          固定使用既有已收盤日
          K；零量、缺值、停牌相鄰日、已知公司行動及未確認的大幅跳價不產生價量訊號。價格採來源拆股還原、未還原股息；成交量調整無法獨立核實，除權息／拆股仍可能影響比較。門檻是工具設定。
        </p>
        {data.risks.map((risk) => (
          <p key={risk}>{risk}</p>
        ))}
      </ResearchNote>
      <Provenance data={data} />
    </>
  );
}
