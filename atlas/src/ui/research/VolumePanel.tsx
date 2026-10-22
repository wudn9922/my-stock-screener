import { useState } from "react";
import {
  volumeSchema,
  volumeStructure,
  type VolumeFeature,
} from "../../report/research";
import {
  HistoryPlot,
  num,
  pct,
  Provenance,
  ResearchDisclosure,
  Stat,
  Status,
  useResearch,
} from "./Common";
export function VolumePanel({ symbol }: { symbol: string }) {
  return (
    <ResearchDisclosure
      key={symbol}
      title="價量分析 · 異常成交／流動性／近期價量結構"
    >
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
  const [window, setWindow] = useState(20),
    [shrink, setShrink] = useState(1),
    [expand, setExpand] = useState(1),
    [abnormal, setAbnormal] = useState(2),
    [quiet, setQuiet] = useState(0.5);
  if (!data) return <Status error={error} />;
  const structure = volumeStructure(data.features, window, shrink, expand);
  const ratio = (v: typeof structure.upShrink) =>
    `${v.numerator}/${v.denominator} = ${v.pct === null ? "N/A" : num(v.pct, 1) + "%"}`;
  const sample = data.features.slice(-window),
    baseline = data.features.at(-window - 1);
  const change = (
    a: number | null | undefined,
    b: number | null | undefined,
  ) => (a != null && b != null && b > 0 ? (a / b - 1) * 100 : null);
  const continuous =
    sample.length === window &&
    sample.every((r) => r.valid && r.returnPct !== null);
  const streak = (test: (r: VolumeFeature) => boolean) => {
    let n = 0;
    for (const row of [...data.features].reverse()) {
      if (
        !row.valid ||
        row.rvol === null ||
        row.returnPct === null ||
        !test(row)
      )
        break;
      n++;
    }
    return n;
  };
  return (
    <>
      <Provenance data={data} />
      <dl className="research-stats">
        <Stat
          label="最新完整交易日成交量（股）"
          value={num(data.latest.volume, 0)}
        />
        <Stat
          label="RVOL20"
          value={num(data.latest.rvol)}
          note={
            data.latest.rvol === null
              ? "前 20 個有效交易日不足"
              : data.latest.rvol >= abnormal
                ? "異常放量"
                : data.latest.rvol <= quiet
                  ? "異常縮量"
                  : "門檻範圍內"
          }
        />
        {[5, 20, 60].map((w) => (
          <Stat
            key={w}
            label={`${w} 日平均成交量（股）`}
            value={num(data.volumeAverages[w]?.value, 0)}
            note={`${data.volumeAverages[w]?.observations ?? 0}/${w} 筆`}
          />
        ))}
        {[5, 20].map((w) => (
          <Stat
            key={`adv${w}`}
            label={`ADV${w}（${data.currency}）`}
            value={num(data.amountAverages[w]?.value, 0)}
            note="成交金額採收盤×量代理；含當日"
          />
        ))}
        <Stat
          label="歷史成交量百分位"
          value={
            data.volumePercentile === null
              ? "N/A"
              : num(data.volumePercentile, 1) + "%"
          }
          note="前最多 252 個日曆樣本中的有效日"
        />
        <Stat
          label="近期最大量日期"
          value={data.maxVolumeDate ?? "N/A"}
          note="最近 60 個有效成交日"
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
      <h3>近期價量結構</h3>
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
        <Stat
          label={`近 ${window} 日價格變化`}
          value={pct(
            continuous ? change(sample.at(-1)?.close, baseline?.close) : null,
          )}
        />
        <Stat
          label={`近 ${window} 日成交量變化`}
          value={pct(
            continuous ? change(sample.at(-1)?.volume, baseline?.volume) : null,
          )}
        />
        <Stat
          label="連續縮量上漲"
          value={`${shrink === 1 ? data.upShrinkStreak : streak((r) => r.returnPct! > 0 && r.rvol! < shrink)} 日${shrink !== 1 ? "（最近 21 筆觀測）" : ""}`}
        />
        <Stat
          label="連續放量下跌"
          value={`${expand === 1 ? data.downExpandStreak : streak((r) => r.returnPct! < 0 && r.rvol! > expand)} 日${expand !== 1 ? "（最近 21 筆觀測）" : ""}`}
        />
        <Stat
          label="最近異常事件"
          value={
            [...data.features]
              .reverse()
              .find(
                (r) =>
                  r.rvol !== null && (r.rvol >= abnormal || r.rvol <= quiet),
              )?.date ??
            (abnormal === 2 && quiet === 0.5 ? data.lastAbnormalDate : null) ??
            "最近 21 筆未觀測"
          }
        />
      </dl>
      <p
        className={
          structure.observations < Math.max(5, window * 0.8)
            ? "research-warning"
            : "source-note"
        }
      >
        有效觀測 {structure.observations}/{window} 日
        {structure.observations < Math.max(5, window * 0.8)
          ? " · 低可信度，樣本不足"
          : ""}
        。分母為有效上漲／下跌日；無上漲或下跌日為
        N/A。相同成交量不歸入放量或縮量，無未來資料。
      </p>
      <HistoryPlot
        points={data.features.map((r) => [r.date, r.volume])}
        secondary={data.features.map((r) => [r.date, r.close])}
        label="成交量與收盤價"
        unit=" 股"
        bars
      />
      <details>
        <summary>流動性與完整數據</summary>
        <dl className="research-stats">
          <Stat label="20 日零成交量天數" value={data.zeroVolumeDays} />
          <Stat
            label="有效交易日比例"
            value={
              data.validDayRatio === null
                ? "N/A"
                : `${num(data.validDayRatio)}%`
            }
          />
          <Stat
            label="最新日內高低振幅"
            value={pct(data.latest.rangePct)}
            note="(高−低) ÷ 前收；非即時買賣價差"
          />
          <Stat
            label="Amihud（20 日）"
            value={num(data.amihud20, 6)}
            note={`${data.amihudObservations} 筆；日報酬絕對值 ÷ 本幣成交金額 × 100萬`}
          />
          {[5, 20].flatMap((w) => [
            <Stat
              key={`v${w}`}
              label={`${w} 日成交量總和（股）`}
              value={num(data.volumeTotals[w], 0)}
            />,
            <Stat
              key={`a${w}`}
              label={`${w} 日成交金額總和（${data.currency}）`}
              value={num(data.amountTotals[w], 0)}
            />,
          ])}
        </dl>
        <div className="research-table">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>收盤</th>
                <th>成交量（股）</th>
                <th>前20日均量</th>
                <th>RVOL</th>
              </tr>
            </thead>
            <tbody>
              {[...data.features].reverse().map((r) => (
                <tr key={r.date}>
                  <td>{r.date}</td>
                  <td>{num(r.close)}</td>
                  <td>{num(r.volume, 0)}</td>
                  <td>{num(r.priorAvg20, 0)}</td>
                  <td>{num(r.rvol)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      {data.risks.map((r) => (
        <p key={r} className="research-warning">
          {r}
        </p>
      ))}
      <p className="source-note">
        固定使用既有已收盤日
        K；零量、缺值、停牌相鄰日、已知公司行動及未確認的大幅跳價不產生價量訊號。RVOL
        分母只取當日前 20
        個有效成交日；一般均量含當日。價格：來源拆股還原、未還原股息；成交量調整無法獨立核實，除權息／拆股仍可能影響比較。不同幣別成交金額不直接跨市場排序。門檻是工具設定，非投資訊號。
      </p>
    </>
  );
}
