import { useState } from "react";
import { holderBaseline, type LargeHolder } from "../../report/twChips";
import { HistoryPlot, num, Stat, ResearchDisclosure } from "./Common";
export function LargeHolders({ rows }: { rows: LargeHolder[] }) {
  return (
    <ResearchDisclosure title="大戶持股比例與集保戶人數">
      <LargeHoldersData rows={rows} />
    </ResearchDisclosure>
  );
}
function LargeHoldersData({ rows }: { rows: LargeHolder[] }) {
  const [window, setWindow] = useState(4),
    [group, setGroup] = useState<"over400" | "over1000">("over400");
  const last = rows.at(-1),
    prev = holderBaseline(rows, 1),
    baseline = holderBaseline(rows, window);
  if (!last)
    return (
      <p className="source-note">
        大戶分級歷史尚未取得；既有股東人數仍可使用。
      </p>
    );
  const points = (v: number | null) =>
    v === null ? "N/A" : `${v > 0 ? "+" : ""}${num(v, 3)} 個百分點`;
  return (
    <>
      <h4>大戶持股比例與集保戶人數</h4>
      <p className="source-note">
        分級資料日 {last.date} · 首筆 {rows[0]?.date} · 共 {rows.length} 週
      </p>
      {(Date.now() - Date.parse(last.date)) / 86400000 > 10 && (
        <p className="research-warning">
          大戶分級資料較舊，顯示最後有效觀測值。
        </p>
      )}
      <dl className="research-stats">
        {(["over400", "over1000"] as const).flatMap((key, i) => [
          <Stat
            key={key}
            label={`${i === 0 ? "400" : "1,000"} 張以上（嚴格超過）`}
            value={`${num(last[key].pct)}%`}
            note={`較前週 ${points(prev ? last[key].pct - prev[key].pct : null)}`}
          />,
          <Stat
            key={`${key}people`}
            label={`${i === 0 ? "400" : "1,000"} 張大戶人數`}
            value={num(last[key].people, 0)}
            note="集保戶數"
          />,
        ])}
      </dl>
      <div className="research-controls">
        <label>
          比較週期
          <select
            value={window}
            onChange={(e) => setWindow(Number(e.target.value))}
          >
            {[4, 13, 26, 52].map((w) => (
              <option key={w} value={w}>
                {w} 週
              </option>
            ))}
          </select>
        </label>
        <label>
          比例級距
          <select
            value={group}
            onChange={(e) => setGroup(e.target.value as typeof group)}
          >
            <option value="over400">超過 400 張</option>
            <option value="over1000">超過 1,000 張</option>
          </select>
        </label>
      </div>
      <dl className="research-stats">
        <Stat
          label={`近 ${window} 週總集保戶變化`}
          value={
            baseline
              ? `${last.count - baseline.count > 0 ? "+" : ""}${num(last.count - baseline.count, 0)} 人`
              : "N/A"
          }
          note={baseline ? `比較 ${baseline.date}` : "歷史不足／基準週缺值"}
        />
        {(["over400", "over1000"] as const).map((key, i) => (
          <Stat
            key={key}
            label={`近 ${window} 週 ${i === 0 ? "400" : "1,000"} 張比例變化`}
            value={points(baseline ? last[key].pct - baseline[key].pct : null)}
            note={baseline ? `比較 ${baseline.date}` : "歷史不足／基準週缺值"}
          />
        ))}
      </dl>
      <HistoryPlot
        points={rows.map((r) => [r.date, r.count])}
        secondary={rows.map((r) => [r.date, r[group].pct])}
        label="集保戶人數／大戶持股比例"
        unit=" 人"
      />
      <p className="source-note">
        藍線為集保戶人數、金線為大戶比例（各自刻度，拖曳日期查值）。第 12～15
        級為超過 400,000 股，第 15 級為超過 1,000,000 股；第 16
        級差異調整不計入，第 17
        級庫存股數作分母，並核對官方比例。名稱沿用市場常用「以上」，實際分級為嚴格超過門檻。這是集保戶分布，不代表所有最終受益人已精確歸戶，也不是法人交易資料。公開下載僅最新一週，本站保留已累積歷史；不補造過去一年。
      </p>
    </>
  );
}
