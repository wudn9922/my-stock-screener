import { useState } from "react";
import { alertsSchema, type OfficialAlert } from "../../report/research";
import {
  Provenance,
  ResearchDisclosure,
  ResearchNote,
  Stat,
  Status,
  useResearch,
} from "./Common";
export function dispositionStatus(event: OfficialAlert, today: string) {
  return event.kind === "notice"
    ? "注意公告"
    : !event.start || !event.end
      ? "處置期間未解析"
      : today < event.start
        ? "已公告將處置"
        : today <= event.end
          ? "公告處置期間內"
          : "公告期間已過 · 展延／解除待確認";
}
export function OfficialAlerts({ symbol }: { symbol?: string }) {
  return (
    <ResearchDisclosure key={symbol ?? "all"} title="台股官方注意／處置追蹤">
      <AlertData symbol={symbol} />
    </ResearchDisclosure>
  );
}
function AlertData({ symbol }: { symbol?: string }) {
  const { data, error } = useResearch("alerts", alertsSchema);
  const [search, setSearch] = useState(""),
    [market, setMarket] = useState("all"),
    [kind, setKind] = useState("all"),
    [shown, setShown] = useState(30),
    [status, setStatus] = useState("recent");
  if (!data) return <Status error={error} />;
  const today = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Taipei",
  }).format(new Date());
  const relevant = data.events.filter((e) => !symbol || e.symbol === symbol);
  const filtered = relevant.filter(
    (e) =>
      (market === "all" || e.market === market) &&
      (kind === "all" || e.kind === kind) &&
      (!search ||
        `${e.symbol} ${e.name}`.includes(search.trim().toUpperCase())) &&
      (status !== "current" ||
        (e.kind === "disposal" &&
          e.start &&
          e.end &&
          e.start <= today &&
          e.end >= today)),
  );
  const sources = Object.entries(data.sources).filter(
    ([k]) => !symbol || k.startsWith(symbol.endsWith(".TWO") ? "TWO:" : "TW:"),
  );
  const missing = sources.some(([, s]) => s.status !== "ok");
  return (
    <>
      <ResearchNote title="注意與處置說明">
        <p>
          注意公告與處置不同。處置限制、起訖與順延條件以原文為準；超過公告結束日不代表已確認解除。
        </p>
      </ResearchNote>
      {sources.map(([key, s]) => (
        <Provenance key={key} data={s} />
      ))}
      {symbol && (
        <>
          <dl className="research-stats">
            {[30, 60, 90].map((days) => {
              const cutoff = new Date(Date.parse(today) - days * 86400000)
                .toISOString()
                .slice(0, 10);
              const source =
                data.sources[
                  `${symbol.endsWith(".TWO") ? "TWO" : "TW"}:notice`
                ];
              const complete =
                !!source?.historyFrom &&
                source.historyFrom <= cutoff &&
                source.status === "ok";
              return (
                <Stat
                  key={days}
                  label={`近 ${days} 日注意次數`}
                  value={
                    relevant.filter(
                      (e) =>
                        e.kind === "notice" &&
                        e.date > cutoff &&
                        e.date <= today,
                    ).length
                  }
                  note={
                    complete
                      ? "公告日去重；完整查詢期間"
                      : "觀測次數；歷史或來源不完整"
                  }
                />
              );
            })}
            <Stat
              label="最近注意日期"
              value={
                relevant.find((e) => e.kind === "notice")?.date ?? "尚無觀測"
              }
            />
            <Stat
              label="最近處置公告"
              value={
                relevant.find((e) => e.kind === "disposal")?.date ?? "尚無觀測"
              }
            />
          </dl>
        </>
      )}
      <div className="research-controls">
        {!symbol && (
          <label>
            個股搜尋
            <input
              aria-label="注意處置搜尋"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="代號或名稱"
            />
          </label>
        )}
        <label>
          市場
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="all">上市＋上櫃</option>
            <option value="TW">上市</option>
            <option value="TWO">上櫃</option>
          </select>
        </label>
        <label>
          公告
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">注意＋處置</option>
            <option value="notice">注意股</option>
            <option value="disposal">處置股</option>
          </select>
        </label>
        <label>
          狀態
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="recent">最近與歷史公告</option>
            <option value="current">公告處置期間內</option>
          </select>
        </label>
      </div>
      {!filtered.length && (
        <p role="status" className="research-empty">
          {missing
            ? "來源不可用或較舊，無法判定是否有新公告。"
            : "已收錄的官方查詢期間內，沒有符合條件的公告。"}
        </p>
      )}
      <ul className="research-timeline">
        {filtered.slice(0, shown).map((e) => (
          <li key={e.id}>
            <strong>
              {e.symbol} {e.name}
            </strong>{" "}
            <span className="research-tag">{dispositionStatus(e, today)}</span>
            <p>
              公告 {e.date}
              {e.period && ` · 處置 ${e.period}`}
              <br />
              {e.reason}
            </p>
            {e.kind === "disposal" && (
              <details>
                <summary>官方限制與處置原文</summary>
                <p>
                  {e.measures}
                  <br />
                  {e.content}
                </p>
              </details>
            )}
            <a href={e.sourceUrl} target="_blank" rel="noreferrer">
              查看官方公告原文
            </a>
          </li>
        ))}
      </ul>
      {filtered.length > shown && (
        <button className="ghost-button" onClick={() => setShown(shown + 30)}>
          再顯示 30 筆（共 {filtered.length} 筆）
        </button>
      )}
    </>
  );
}
