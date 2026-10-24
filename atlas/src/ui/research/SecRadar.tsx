import { useState } from "react";
import { secSchema } from "../../report/research";
import {
  Provenance,
  ResearchDisclosure,
  ResearchNote,
  Status,
  useResearch,
} from "./Common";
const labels: Record<string, string> = {
  agreement: "重大合約",
  merger: "收購／出售／併購",
  debt: "重大債務／違約",
  financial: "財報與財務狀況",
  governance: "治理／人事",
  accountant: "會計師",
  legal: "監管／訴訟",
  other: "其他重大事件",
  exhibits: "附件",
};
export function SecRadar({
  symbol,
  symbols,
}: {
  symbol?: string;
  symbols?: string[];
}) {
  return (
    <ResearchDisclosure
      key={symbol ?? symbols?.join(",") ?? "all"}
      title="SEC 8-K 重大事件雷達"
    >
      <SecData symbol={symbol} symbols={symbols} />
    </ResearchDisclosure>
  );
}
function SecData({ symbol, symbols }: { symbol?: string; symbols?: string[] }) {
  const { data, error } = useResearch("sec-radar", secSchema);
  const [days, setDays] = useState(30),
    [category, setCategory] = useState("all"),
    [amendment, setAmendment] = useState("both"),
    [search, setSearch] = useState(""),
    [shown, setShown] = useState(25);
  if (!data) return <Status error={error} />;
  const today = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "America/New_York",
  }).format(new Date());
  const cutoff = new Date(Date.parse(today) - (days - 1) * 86400000)
    .toISOString()
    .slice(0, 10);
  const events = data.events.filter(
    (e) =>
      e.date >= cutoff &&
      e.date <= today &&
      (!symbol || (e.symbols ?? [e.symbol]).includes(symbol)) &&
      (!symbols ||
        (e.symbols ?? [e.symbol]).some((s) => symbols.includes(s))) &&
      (category === "all" ||
        e.categories.some((c) => c.category === category)) &&
      (amendment === "both" || e.amendment === (amendment === "amend")) &&
      (!search ||
        `${(e.symbols ?? [e.symbol]).join(" ")} ${e.companyName}`
          .toUpperCase()
          .includes(search.toUpperCase())),
  );
  const coverage = symbol ? data.checkedSymbols[symbol] : null;
  return (
    <>
      <Provenance data={data} />
      <ResearchNote title="查詢覆蓋與申報類型">
        <p>
          全市場名單 {data.universe} 檔，CIK 對應 {data.mapped} 檔；已查詢{" "}
          {Object.keys(data.checkedSymbols).length} 檔、累積{" "}
          {data.indexedDates.length}{" "}
          個每日索引。覆蓋正在累積，未查詢不代表無事件。
          {coverage && ` 此股最近查詢 ${coverage.fetchedAt}。`}
          {coverage?.applicability.includes("6-K") &&
            " 外國發行人可能以 6-K 申報；此雷達只收錄 8-K，不將 6-K 混入。"}
        </p>
      </ResearchNote>
      <div className="research-controls">
        <label>
          最近
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {[1, 7, 30, 90].map((d) => (
              <option key={d} value={d}>
                {d} 日
              </option>
            ))}
          </select>
        </label>
        <label>
          事件分類
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="all">全部</option>
            {Object.entries(labels).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          申報版本
          <select
            value={amendment}
            onChange={(e) => setAmendment(e.target.value)}
          >
            <option value="both">原始＋修訂</option>
            <option value="original">僅原始 8-K</option>
            <option value="amend">僅修訂 8-K/A</option>
          </select>
        </label>
        {!symbol && (
          <label>
            股票搜尋
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="SEC股票搜尋"
            />
          </label>
        )}
      </div>
      {!events.length && (
        <p role="status" className="research-empty">
          {symbol && !coverage
            ? "此股尚未完成申報覆蓋；無法判定是否有事件。"
            : "已收錄範圍內沒有符合條件的事件。"}
        </p>
      )}
      <ul className="research-timeline">
        {events.slice(0, shown).map((e) => (
          <li key={`${e.symbol}:${e.id}`}>
            <strong>
              {symbol ?? (e.symbols ?? [e.symbol]).join(" / ")} · {e.title}
            </strong>
            <p>
              {e.companyName} · {e.form}
              {e.amendment ? "（修訂）" : ""}
              <br />
              申報 {e.date} {e.acceptedAt ?? "來源未提供時間"}
              <br />
              CIK {e.cik} · {e.accession}
              <br />
              SEC Item {e.items.join("、") || "尚未解析"}
            </p>
            <details>
              <summary>
                {e.summaryStatus === "parsed"
                  ? "內容節錄與來源"
                  : "內容尚未解析"}
              </summary>
              <p>
                {e.summary ??
                  "保留申報紀錄與官方連結；不由 Item 編號推測事件內容。"}
              </p>
              <p className="source-note">
                摘要為實際原文節錄，可能為英文，非投資建議。取得 {e.fetchedAt}
              </p>
            </details>
            <a href={e.sourceUrl} target="_blank" rel="noreferrer">
              開啟 SEC 官方申報
            </a>
          </li>
        ))}
      </ul>
      {events.length > shown && (
        <button className="ghost-button" onClick={() => setShown(shown + 25)}>
          再顯示 25 筆
        </button>
      )}
    </>
  );
}
