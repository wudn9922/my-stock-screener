import { useEffect, useState } from "react";
import {
  DATA_STATUS_LABELS,
  fetchDataStatus,
  groupDataStatus,
  statusSummary,
  workflowLabel,
  type DataStatusResult,
} from "../report/dataStatus";
import { PageState } from "./PageState";
import { HelpDisclosure } from "./HelpDisclosure";
import "./dataStatus.css";

const taipei = (value: string | null) =>
  value
    ? /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? `${value}（僅日期）`
      : new Intl.DateTimeFormat("zh-TW", {
          timeZone: "Asia/Taipei",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        }).format(new Date(value))
    : "未提供";

export function DataStatusPanel() {
  const [state, setState] = useState<DataStatusResult>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setState(undefined);
    void fetchDataStatus(abort.signal).then((result) => {
      if (!abort.signal.aborted) setState(result);
    });
    return () => abort.abort();
  }, [attempt]);
  if (!state) return <PageState kind="loading" title="載入資料狀態…" />;
  if (state.status !== "ok")
    return (
      <PageState
        kind="error"
        title="資料狀態暫時無法顯示"
        message={state.message}
        onRetry={() => setAttempt((n) => n + 1)}
      />
    );
  const { data } = state;
  const counts = statusSummary(data.sources);
  const allOk = counts.ok === data.sources.length;
  return (
    <section className="data-status-panel" aria-label="資料狀態">
      <div className="console-card data-status-summary" role="status">
        <h2>
          {allOk
            ? "全部正常"
            : `${counts.late} 項延遲・${counts.stale} 項過期・${counts.missing} 項缺少`}
        </h2>
        <p>
          正常 {counts.ok}／共 {data.sources.length} 項 · 檢查時間{" "}
          {taipei(data.generatedAt)}（台灣時間）
        </p>
        <button
          type="button"
          className="ghost-button"
          onClick={() => setAttempt((n) => n + 1)}
        >
          重新載入狀態
        </button>
      </div>
      {groupDataStatus(data.sources)
        .filter((group) => group.sources.length)
        .map((group) => (
          <section
            className="console-card"
            key={group.market}
            aria-label={`${group.label}資料狀態`}
          >
            <h3>{group.label}</h3>
            <ul className="data-status-list">
              {group.sources.map((source) => (
                <li key={source.id}>
                  <details className="data-status-source">
                    <summary>
                      <span className="data-status-name">{source.name}</span>
                      <span className={`data-status-badge is-${source.status}`}>
                        {DATA_STATUS_LABELS[source.status]}
                      </span>
                      <span className="data-status-dates">
                        <span>
                          資料日 <b>{source.dataDate ?? "未提供"}</b>
                        </span>
                        <span>更新 {taipei(source.generatedAt)}</span>
                      </span>
                    </summary>
                    <div className="data-status-reason">
                      <p>{source.reason}</p>
                      <p>
                        應更新至 {source.expectedDate ?? "無法判定"} · 落後{" "}
                        {source.lagTradingDays ?? "未知"} 個交易日
                      </p>
                    </div>
                  </details>
                </li>
              ))}
            </ul>
          </section>
        ))}
      <section className="console-card" aria-label="工作流程執行結果">
        <h3>工作流程最近一次執行</h3>
        {data.workflows.length === 0 ? (
          <p>此檔案未提供工作流程紀錄。</p>
        ) : (
          <ul className="data-status-list">
            {data.workflows.map((run) => (
              <li className="data-status-run" key={run.file}>
                <a href={run.url} target="_blank" rel="noopener noreferrer">
                  {run.name} ↗
                </a>
                <span
                  className={`data-status-badge is-${run.conclusion === "success" ? "ok" : run.conclusion === "failure" || run.conclusion === "timed_out" ? "stale" : run.status === "in_progress" || run.status === "queued" ? "late" : "missing"}`}
                >
                  {workflowLabel(run)}
                </span>
                <small>
                  開始 {taipei(run.startedAt)} · 更新 {taipei(run.updatedAt)}
                </small>
                {run.reason && <p>{run.reason}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
      <HelpDisclosure label="狀態判定說明">
        依交易所日曆、收盤後發布寬限及各來源正常延遲判定，週末與休市不計落後。季度財報顯示擷取日期，不代表財報季度。這是檢查時間當下的快照；重新載入不會觸發資料收集。
      </HelpDisclosure>
    </section>
  );
}
