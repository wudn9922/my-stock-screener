import { useEffect, useMemo, useState } from 'react';
import { loadMarginTemperature, type MarginTemperature, type TemperatureReading, type TemperatureResult } from '../report/marginTemperature';
import './marginTemperature.css';

type RangeKey = '3y' | '10y' | 'all';
const percent = (value: number | null, digits = 1) => value === null ? '資料不足' : `${value.toFixed(digits)}%`;
const number = (value: number) => value.toLocaleString('zh-TW', { maximumFractionDigits: 2 });
const labelFor = (row: TemperatureReading) => [row.hot ? '過熱提醒' : '', row.cold ? '過冷提醒' : ''].filter(Boolean).join('＋') || '中性';
const persistence = (data: MarginTemperature): string => {
  const { hotWeeks } = data.latest;
  if (!data.latest.hot || !hotWeeks) return '';
  const year = data.firstDate?.slice(0, 4);
  const episode = year && data.hotEpisodes ? `（${year} 年以來第 ${data.hotEpisodes} 段）` : '';
  return `已連續 ${hotWeeks} 週${episode}`;
};
const relativeText = (ratioPct: number | null): string => ratioPct === null ? '資料不足'
  : `P${Math.floor(ratioPct)} · ${ratioPct >= 90 ? '融資增速高於大盤' : '融資增速未明顯高於大盤'}`;
const LEFT = 48, RIGHT = 888, SPAN = RIGHT - LEFT;
const xp = (index: number, length: number) => LEFT + index * SPAN / Math.max(length - 1, 1);
const clamp = (value: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, value));

function pathFor(rows: readonly TemperatureReading[], value: (row: TemperatureReading) => number | null,
  y: (v: number) => number): string {
  let inSegment = false;
  const parts: string[] = [];
  rows.forEach((row, i) => {
    const v = value(row);
    if (v === null || !Number.isFinite(v)) { inSegment = false; return; }
    parts.push(`${inSegment ? 'L' : 'M'}${xp(i, rows.length).toFixed(2)},${y(v).toFixed(2)}`);
    inSegment = true;
  });
  return parts.join(' ');
}

function Regions({ rows }: { rows: TemperatureReading[] }) {
  const widths = SPAN / Math.max(1, rows.length - 1);
  return <>
    {rows.map((row, i) => (row.hot || row.cold) && <g key={row.date}>
      {row.hot && <rect data-state="hot" x={xp(i, rows.length) - widths / 2} y="12" width={widths + .8} height="188" fill="#df923b" fillOpacity=".20" />}
      {row.cold && <rect data-state="cold" x={xp(i, rows.length) - widths / 2} y="12" width={widths + .8} height="188" fill="#398fe4" fillOpacity=".25" />}
    </g>)}
  </>;
}
function SparkPanel({ rows, kind }: { rows: TemperatureReading[]; kind: 'pct' | 'mdd' }) {
  const heat = kind === 'pct';
  const lowest = heat ? 0 : Math.min(-60, Math.floor(Math.min(-30, ...rows.flatMap(r => r.mdd === null ? [] : [r.mdd])) / 10) * 10);
  const highest = heat ? 100 : 0;
  const y = (v: number) => 112 - (clamp(v, lowest, highest) - lowest) * 88 / (highest - lowest);
  const threshold = heat ? 90 : -30;
  const latest = rows.at(-1)!;
  const value = heat ? latest.pct : latest.mdd;
  return <svg viewBox="0 0 900 124" preserveAspectRatio="none" role="img"
    aria-label={heat ? '融資熱度百分位週線，虛線為過熱門檻 90' : '融資冷度高點降幅週線，虛線為過冷門檻負 30%'}>
    <text x="1" y="19" fill="#a9b1bf" fontSize="13">{heat ? '熱度' : '冷度'}</text>
    <text x="1" y="45" fill="#8491a2" fontSize="11">{highest}</text>
    <text x="1" y="112" fill="#8491a2" fontSize="11">{lowest}</text>
    <line x1={LEFT} x2={RIGHT} y1={y(threshold)} y2={y(threshold)} stroke={heat ? '#df923b' : '#398fe4'} strokeDasharray="7 5" strokeWidth="1.4" />
    <text x={RIGHT - 4} y={y(threshold) - 5} textAnchor="end" fill={heat ? '#dfab6c' : '#91c1fa'} fontSize="12">{heat ? '90' : '−30%'}</text>
    <path d={pathFor(rows, r => r[kind], y)} stroke={heat ? '#edb06e' : '#86b7eb'} strokeWidth="2.2" vectorEffect="non-scaling-stroke" fill="none" />
    {value !== null && <circle cx={xp(rows.length - 1, rows.length)} cy={y(value)} r="4.5" fill={heat ? '#edb06e' : '#86b7eb'} stroke="#101720" strokeWidth="1.5" />}
  </svg>;
}
function HistoricalChart({ rows }: { rows: TemperatureReading[] }) {
  const closingLogs = rows.map(row => Math.log(row.close));
  const min = Math.min(...closingLogs), max = Math.max(...closingLogs);
  const plotY = (value: number) => 198 - 175 * (Math.log(value) - min) / (max - min || 1);
  const latest = rows.at(-1)!;
  const [focus, setFocus] = useState<number | null>(null);
  const selected = rows[focus ?? rows.length - 1] ?? latest;
  const markerX = xp(focus ?? rows.length - 1, rows.length);
  return <figure className="mt-history" aria-label="台股融資溫度歷史走勢">
    <div className="mt-chart-legend">
      <span><i className="mt-legend-index" />加權指數（對數刻度）</span>
      <span><i className="mt-legend-hot" />橘色底：過熱週</span>
      <span><i className="mt-legend-cold" />藍色底：過冷週</span>
    </div>
    <svg className="mt-price-chart" viewBox="0 0 900 215" preserveAspectRatio="none" role="img"
      aria-label="加權指數對數刻度週線；橘色背景表示過熱提醒，藍色背景表示過冷提醒">
      <Regions rows={rows} />
      <line x1={LEFT} x2={RIGHT} y1="200" y2="200" stroke="#364152" />
      <text x="3" y="24" fill="#9ba7b7" fontSize="13">指數</text>
      <text x="3" y="43" fill="#9ba7b7" fontSize="11">{number(Math.exp(max))}</text>
      <text x="3" y="198" fill="#9ba7b7" fontSize="11">{number(Math.exp(min))}</text>
      <path d={pathFor(rows, r => r.close, plotY)} fill="none" stroke="#d3ddec" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <circle cx={xp(rows.length - 1, rows.length)} cy={plotY(latest.close)} r="4" fill="#d3ddec" />
      {focus !== null && <line x1={markerX} x2={markerX} y1="12" y2="201" stroke="#a9b1bf" strokeDasharray="3 5" />}
    </svg>
    <div className="mt-plot-foot"><span>{rows[0]?.date}</span><span>加權指數（對數軸）</span><span>{latest.date}</span></div>
    <SparkPanel rows={rows} kind="pct" />
    <SparkPanel rows={rows} kind="mdd" />
    <div className="mt-chart-legend"><span><i className="mt-legend-hot-line" />熱度分位線／90 虛線</span><span><i className="mt-legend-cold-line" />冷度降幅線／−30% 虛線</span><span>實心圓點：最新資料</span></div>
    <label className="mt-inspector">查閱歷史週
      <input type="range" min="0" max={Math.max(rows.length - 1, 0)} value={focus ?? rows.length - 1} onChange={event => setFocus(Number(event.target.value))} />
    </label>
    <figcaption className="mt-inspector-detail" aria-live="polite">{selected.date} · 加權指數 {number(selected.close)} · 熱度 {percent(selected.pct)} · 冷度 {percent(selected.mdd)} · {labelFor(selected)}</figcaption>
  </figure>;
}

function Gauge({ title, value, range, threshold, cold }: {
  title: string; value: number | null; range: [number, number]; threshold: number; cold?: boolean;
}) {
  const [lo, hi] = range;
  const pct = (n: number) => 100 * clamp((n - lo) / (hi - lo), 0, 1);
  return <div className="mt-gauge">
    <div className="mt-gauge-head"><span>{title}</span><b>{percent(value)}</b></div>
    <div className="mt-gauge-track" role="meter" aria-label={title} aria-valuemin={lo} aria-valuemax={hi} aria-valuenow={value ?? undefined}>
      <span className="mt-gauge-fill" style={{ width: `${value === null ? 0 : pct(value)}%`, background: cold ? '#398fe4' : '#df923b' }} />
      <i className="mt-gauge-threshold" style={{ left: `${pct(threshold)}%` }} />
    </div>
    <div className="mt-gauge-ticks"><span>{lo}%</span><span>提醒門檻 {threshold}%</span><span>{hi}%</span></div>
  </div>;
}

export function TemperatureBody({ data }: { data: MarginTemperature }) {
  const [range, setRange] = useState<RangeKey>('all');
  const rows = useMemo(() => {
    if (range === 'all') return data.rows;
    const year = Number(data.asOf.slice(0, 4)) - (range === '3y' ? 3 : 10);
    const cutoff = `${year}${data.asOf.slice(4)}`;
    return data.rows.filter(row => row.date >= cutoff);
  }, [data, range]);
  const current = data.latest;
  return <>
    <div className="mt-status-line"><div className="mt-status-tags" aria-label={`目前狀態：${labelFor(current)}`}>
      {current.hot && <span className="mt-label mt-hot">▲ 過熱提醒</span>}
      {current.cold && <span className="mt-label mt-cold">▼ 過冷提醒</span>}
      {!current.hot && !current.cold && <span className="mt-label mt-neutral">● 中性</span>}
    </div>{persistence(data) && <span className="mt-persist">{persistence(data)}</span>}<time dateTime={data.asOf}>資料日期 {data.asOf}</time>{data.provisional && <span className="mt-provisional">本週暫計</span>}</div>
    <div className="mt-gauge-grid">
      <Gauge title="熱度 · 近 3 年分位" value={current.pct} range={[0, 100]} threshold={90} />
      <Gauge title="冷度 · 自 52 週高點降幅" value={current.mdd} range={[-100, 0]} threshold={-30} cold />
    </div>
    {current.ratioPct !== undefined && <p className="mt-relative" data-state={current.ratioPct !== null && current.ratioPct >= 90 ? 'hot' : 'neutral'}>
      <span>相對大盤分位</span> <b>{relativeText(current.ratioPct)}</b></p>}
    <p className="mt-fixed"><b>僅描述融資水位偏熱／偏冷，非買賣訊號。</b></p>
    <div className="mt-chart-tools"><h3>加權指數與融資溫度歷史圖</h3>
      <label>顯示區間<select aria-label="融資溫度歷史區間" value={range} onChange={event => setRange(event.target.value as RangeKey)}>
        <option value="3y">近 3 年</option><option value="10y">近 10 年</option><option value="all">全部（2007 年起）</option>
      </select></label></div>
    {rows.length > 1 && <HistoricalChart key={range} rows={rows} />}
    <details className="mt-data-table"><summary>表格檢視 · 全部 {data.rows.length} 週資料</summary>
      <div className="mt-table-scroll"><table><thead><tr><th>日期</th><th>加權指數</th><th>熱度</th><th>冷度</th><th>狀態</th></tr></thead><tbody>
        {[...data.rows].reverse().map(row => <tr key={row.date}><td>{row.date}</td><td>{number(row.close)}</td><td>{percent(row.pct)}</td><td>{percent(row.mdd)}</td><td>{labelFor(row)}</td></tr>)}
      </tbody></table></div>
    </details>
    <div className="mt-notes">
      <p>歷史（2007–2026）：3 個大頭附近皆曾亮燈過熱、4 個大底附近皆曾亮燈過冷；但提醒常提早數週到數月，過熱亮燈後指數仍可能續漲（例如 2020-12 亮燈後約 55 週才見大頭、期間指數再漲約 29%）。</p>
      <p>資料僅含上市融資；融資金額為借款成本餘額，非市值。</p>
      <p>熱度：156 週視窗（至少 104 週），與視窗內前面週數相比較低者的占比；冷度：融資金額與 52 週最高額的差距（至少 26 週）。來源：證交所。歷史統計是回顧性描述，不代表未來表現。</p>
      <p>融資餘額會隨大盤規模一起成長，所以金額分位在長期多頭中常會長時間維持高檔，「過熱」可能持續很多週。{current.ratioPct !== undefined &&
        '相對大盤分位則把融資金額除以加權指數收盤價，當作市場規模的粗略代理（只是代理，不是市場總市值），用來看融資有沒有比指數成長得更快。'}</p>
    </div>
  </>;
}

export function MarginTemperatureCard() {
  const [result, setResult] = useState<TemperatureResult>();
  useEffect(() => {
    const controller = new AbortController();
    void loadMarginTemperature(controller.signal).then(value => { if (!controller.signal.aborted) setResult(value); });
    return () => controller.abort();
  }, []);
  return <section className="card margin-temperature-card" aria-label="融資餘額溫度" data-testid="margin-temperature-card">
    <h2>融資餘額溫度 <small>台股上市 · 週資料</small></h2>
    {!result && <p className="muted-text" role="status">正在載入融資溫度…</p>}
    {result?.error && <p className="muted-text" role="status">{result.error}</p>}
    {result?.data && <TemperatureBody data={result.data} />}
  </section>;
}
