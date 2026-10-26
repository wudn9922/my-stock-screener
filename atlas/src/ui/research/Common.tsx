import { useEffect, useState, type ReactNode } from "react";
import type { z } from "zod";
import { fetchResearch, type ResearchMeta } from "../../report/research";
import "../dataResearch.css";
export const num = (v: number | null | undefined, digits = 2) =>
  v == null
    ? "N/A"
    : v.toLocaleString("zh-TW", { maximumFractionDigits: digits });
export const pct = (v: number | null | undefined) =>
  v == null ? "N/A" : `${v > 0 ? "+" : ""}${num(v, 2)}%`;
export function useResearch<T>(
  path: string,
  schema: z.ZodType<T>,
  symbol?: string,
): { data?: T; error?: string } {
  const [loaded, setLoaded] = useState<{
    path: string;
    data?: T;
    error?: string;
  }>();
  useEffect(() => {
    const abort = new AbortController();
    void fetchResearch(path, schema, abort.signal, symbol).then(
      (data) => {
        if (!abort.signal.aborted) setLoaded({ path, data });
      },
      (e) => {
        if (!abort.signal.aborted)
          setLoaded({
            path,
            error: e instanceof Error ? e.message : "資料取得失敗",
          });
      },
    );
    return () => abort.abort();
  }, [path, schema, symbol]);
  return loaded?.path === path ? loaded : {};
}
export function Status({ error }: { error?: string }) {
  return (
    <p className="source-note" role="status">
      {error ?? "載入研究資料中…"}
    </p>
  );
}
export function Provenance({
  data,
  maxAgeDays = 10,
}: {
  data: ResearchMeta;
  maxAgeDays?: number;
}) {
  const age = data.asOf
    ? (Date.now() - Date.parse(data.asOf)) / 86400000
    : null;
  return (
    <details className="source-note research-provenance research-note">
      <summary>
        資料來源 · {data.asOf ?? "資料日未確認"}
        {(data.status !== "ok" || (age !== null && age > maxAgeDays)) && (
          <span>
            {" "}
            ·{" "}
            {data.status === "unavailable"
              ? "來源不可用"
              : "資料較舊／沿用快照"}
          </span>
        )}
      </summary>
      <p>
        <a href={data.sourceUrl} target="_blank" rel="noreferrer">
          {data.source}
        </a>{" "}
        · 資料日 {data.asOf ?? "未確認"}
        <br />
        擷取 {data.fetchedAt ?? "原快取未提供"} · 發布{" "}
        {data.publishedAt ?? "來源未提供"}
        {data.missingReason && (
          <>
            <br />
            {data.missingReason}
          </>
        )}
      </p>
    </details>
  );
}
export function ResearchNote({
  children,
  title = "說明",
  compact = false,
}: {
  children: ReactNode;
  title?: string;
  compact?: boolean;
}) {
  return (
    <details className={`research-note source-note${compact ? " research-note-compact" : ""}`}>
      <summary aria-label={compact ? title : undefined} title={compact ? title : undefined}>
        {compact ? <span aria-hidden="true">?</span> : title}
      </summary>
      <div className="research-note-body">{children}</div>
    </details>
  );
}
export function ResearchDisclosure({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const [opened, setOpened] = useState(false);
  return (
    <details
      className="card data-research"
      onToggle={(e) => setOpened(e.currentTarget.open)}
    >
      <summary>{title}</summary>
      {opened && <div className="research-content">{children}</div>}
    </details>
  );
}
export function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: string;
}) {
  return (
    <div className="research-stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {note && (
        <ResearchNote title={`${label}說明`} compact>
          <small>{note}</small>
        </ResearchNote>
      )}
    </div>
  );
}
/** Independent SVG summaries like the financial cards; no gestures/overlays on the stock chart. */
export function HistoryPlot({
  points,
  label,
  secondary,
  unit = "",
  bars = false,
}: {
  points: [string, number | null][];
  label: string;
  secondary?: [string, number | null][];
  unit?: string;
  bars?: boolean;
}) {
  const [position, setPosition] = useState<number | null>(null);
  const selected =
    points[Math.min(position ?? points.length - 1, points.length - 1)];
  const good = points.filter((p): p is [string, number] => p[1] !== null);
  if (good.length < 2)
    return <p className="source-note">歷史資料不足兩筆；持續累積中。</p>;
  const lo = Math.min(bars ? 0 : Infinity, ...good.map((p) => p[1])),
    hi = Math.max(bars ? 0 : -Infinity, ...good.map((p) => p[1]));
  const x = (i: number) => 10 + (i * 580) / Math.max(1, points.length - 1),
    y = (v: number) => 100 - ((v - lo) / (hi - lo || 1)) * 88;
  const line = (values: [string, number | null][], secondary = false) => {
    const nums = values.flatMap((p) => (p[1] === null ? [] : [p[1]]));
    const min = Math.min(...nums),
      max = Math.max(...nums);
    let pen = "M";
    return values
      .map(([_, v], i) => {
        if (v === null) {
          pen = "M";
          return "";
        }
        const d = `${pen}${x(i)},${secondary ? 100 - ((v - min) / (max - min || 1)) * 88 : y(v)}`;
        pen = "L";
        return d;
      })
      .join(" ");
  };
  return (
    <figure className="research-plot">
      <figcaption>
        {label} · {selected?.[0]} · {num(selected?.[1])}
        {unit}
        {secondary &&
          ` · 對照 ${num(secondary.find((p) => p[0] === selected?.[0])?.[1])}`}
      </figcaption>
      <svg viewBox="0 0 600 112" role="img" aria-label={label}>
        {bars ? (
          points.map(([d, v], i) =>
            v === null ? null : (
              <rect
                key={d}
                x={x(i) - Math.min(5, 260 / points.length)}
                y={Math.min(y(v), y(0))}
                width={Math.min(10, 520 / points.length)}
                height={Math.max(1, Math.abs(y(v) - y(0)))}
                fill={v >= 0 ? "#e67c73" : "#45b69c"}
              >
                <title>
                  {d} {num(v)}
                  {unit}
                </title>
              </rect>
            ),
          )
        ) : (
          <path d={line(points)} fill="none" stroke="#3987e5" strokeWidth="2" />
        )}
        {secondary && (
          <path
            d={line(secondary, true)}
            fill="none"
            stroke="#d5a02e"
            strokeWidth="2"
          />
        )}
      </svg>
      <input
        type="range"
        min="0"
        max={points.length - 1}
        value={position ?? points.length - 1}
        onChange={(e) => setPosition(Number(e.target.value))}
        aria-label={`${label}查閱日期`}
      />
      <small>
        {points[0]?.[0]} — {points.at(-1)?.[0]}
      </small>
      {secondary && (
        <ResearchNote>
          <small>藍／柱：主指標；金：對照指標，各自刻度。</small>
        </ResearchNote>
      )}
    </figure>
  );
}
