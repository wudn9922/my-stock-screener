import { useState } from "react";
import { institutionsSchema } from "../../report/research";
import { formatMarketVolume, marketVolumeUnit } from "../format";
import {
  HistoryPlot,
  num,
  pct,
  Provenance,
  ResearchDisclosure,
  ResearchNote,
  Stat,
  Status,
  useResearch,
} from "./Common";
const roles = {
  foreign: "外資及陸資（含外資自營商）",
  trust: "投信",
  dealer: "自營商（自行買賣＋避險）",
};
export function InstitutionPanel({ symbol }: { symbol: string }) {
  return (
    <ResearchDisclosure
      key={symbol}
      title="法人買賣超 · 法人買超日價格區間（估計）"
    >
      <InstitutionData symbol={symbol} />
    </ResearchDisclosure>
  );
}
function InstitutionData({ symbol }: { symbol: string }) {
  const { data, error } = useResearch(
    `institutions/${encodeURIComponent(symbol)}`,
    institutionsSchema,
    symbol,
  );
  const [role, setRole] = useState<keyof typeof roles>("foreign"),
    [window, setWindow] = useState("20");
  if (!data) return <Status error={error} />;
  const label = marketVolumeUnit(symbol),
    divisor = label === "張" ? 1000 : 1,
    last = data.rows.at(-1),
    summary = data.summary[role],
    cost = data.costs[role][window];
  return (
    <>
      <Provenance data={data} />
      {data.marketPriceSource && <Provenance data={data.marketPriceSource} />}
      <div className="research-controls">
        <label>
          法人
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as keyof typeof roles)}
          >
            {Object.entries(roles).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <dl className="research-stats">
        <Stat
          label={`資料日買進（${label}）`}
          value={num(
            last ? last[role].buy / divisor : null,
            label === "張" ? 3 : 0,
          )}
        />
        <Stat
          label={`資料日賣出（${label}）`}
          value={num(
            last ? last[role].sell / divisor : null,
            label === "張" ? 3 : 0,
          )}
        />
        {[1, 5, 10, 20, 60].map((w) => {
          const v = summary.windows[w];
          return (
            <Stat
              key={w}
              label={`${w === 1 ? "資料日" : `近 ${w} 日`}買賣超（${label}）`}
              value={num(
                v?.net == null ? null : v.net / divisor,
                label === "張" ? 3 : 0,
              )}
              note={`${v?.observations ?? 0}/${w} 筆${v?.complete ? "" : " · 歷史不足"}`}
            />
          );
        })}
        <Stat
          label={summary.streak >= 0 ? "連續買超" : "連續賣超"}
          value={`${Math.abs(summary.streak)} 日`}
          note="截至實際資料日，觀測連續性依官方報表"
        />
        {role === "foreign" && (
          <Stat
            label="外資淨買超／當日成交量"
            value={pct(last?.netVolumePct)}
          />
        )}
      </dl>
      <HistoryPlot
        points={data.rows.map((r) => [r.date, r[role].net / divisor])}
        secondary={data.rows.map((r) => [r.date, r.close])}
        label={`${roles[role]}買賣超柱狀圖／收盤價`}
        unit={` ${label}`}
        bars
      />
      <details>
        <summary>每日明細與法人分類</summary>
        <div className="research-table">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>買進（{label}）</th>
                <th>賣出（{label}）</th>
                <th>淨買超（{label}）</th>
                <th>收盤 TWD</th>
              </tr>
            </thead>
            <tbody>
              {[...data.rows].reverse().map((r) => (
                <tr key={r.date}>
                  <td>{r.date}</td>
                  <td>{num(r[role].buy / divisor, 3)}</td>
                  <td>{num(r[role].sell / divisor, 3)}</td>
                  <td>{num(r[role].net / divisor, 3)}</td>
                  <td>{num(r.close)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="source-note">
          外資＝不含外資自營商之外陸資＋外資自營商；投信獨立；自營商＝自行買賣＋避險。各分項依官方股數核對合計，不由外資持股比率變動推算。淨額占量比優先使用同日官方成交股數，缺少時才採既有日K量。最新外資自營商淨額{" "}
          {formatMarketVolume(last?.foreignDealer.net, symbol, 3)}
          ，自營商自行買賣 {formatMarketVolume(last?.dealerOwn.net, symbol, 3)}
          、避險 {formatMarketVolume(last?.dealerHedge.net, symbol, 3)}。
        </p>
      </details>
      <h3>法人買超日價格區間（估計）</h3>
      <label>
        估計窗口{" "}
        <select
          aria-label="法人估計窗口"
          value={window}
          onChange={(e) => setWindow(e.target.value)}
        >
          {[5, 20, 60].map((w) => (
            <option key={w} value={w}>
              {w} 個交易日
            </option>
          ))}
        </select>
      </label>
      {cost && (
        <>
          <dl className="research-stats">
            <Stat label="估計買超日參考價（TWD）" value={num(cost.reference)} />
            <Stat
              label="買超日收盤價區間（TWD）"
              value={`${num(cost.closeLow)}～${num(cost.closeHigh)}`}
            />
            <Stat
              label="買超日市場均價區間（TWD）"
              value={`${num(cost.low)}～${num(cost.high)}`}
            />
            <Stat
              label="最新快取收盤距參考價"
              value={pct(cost.distancePct)}
              note={`價格日 ${cost.currentAsOf ?? "未提供"} · ${num(cost.currentClose)} TWD`}
            />
            <Stat
              label="有效／正買超日"
              value={`${cost.observations}／${cost.positiveDays} 日`}
              note={`覆蓋 ${num(cost.coveragePct, 1)}%（要求 ${window} 日）`}
            />
          </dl>
          <details>
            <summary>估計使用的實際觀測日</summary>
            <div className="research-table">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>正向淨買超（{label}）</th>
                    <th>市場日均價／收盤代理 TWD</th>
                    <th>收盤 TWD</th>
                  </tr>
                </thead>
                <tbody>
                  {cost.days.map((d) => (
                    <tr key={d.date}>
                      <td>{d.date}</td>
                      <td>{num(d.net / divisor, label === "張" ? 3 : 0)}</td>
                      <td>{num(d.marketAverage)}</td>
                      <td>{num(d.close)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <ResearchNote title="估計方式與資料限制">
            <p>
              Σ（每日市場均價 × max(淨買超股數, 0)）÷ Σ max(淨買超股數,
              0)；分母為零＝N/A。
              {cost.priceBasis === "official-market-VWAP"
                ? "使用官方市場成交值÷成交股數，仍非法人逐筆成交均價。"
                : "未配對官方成交值的日期採收盤價代理；股價還原口徑相差超過 0.5% 的日期排除，不混入估計。"}
              淨買賣超無法辨識原有庫存、個別帳戶或賣出歸屬，因此不是實際持倉成本或防守價。拆股後的價格／歷史股數可能不同口徑，區間只供公開資料研究；資料不足不推估補值。
            </p>
          </ResearchNote>
        </>
      )}
    </>
  );
}
