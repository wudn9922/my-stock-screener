import { hasSecHistory, provenance, type FinancialHistory, type PeriodKind, type Revision } from '../../fundamentals/FinancialHistory';
import {
  FIELD_LABELS,
  METRICS,
  METRIC_GROUPS,
  contextOf,
  metricSeries,
  periodLabels,
  windowAverage,
  windowCagr,
  yoy,
  type CagrResult,
  type ChangeResult,
  type MetricId,
  type MetricResult,
  type MetricUnit,
  type PeriodLabel,
} from '../../fundamentals/financialMetrics';

/**
 * View model and text for the financials page: which periods each chart shows, the headline value,
 * YoY / CAGR chips, number formatting in 億 / 兆, and the honest notes about depth and provenance.
 * Pure (no React), so it is unit-tested with the synthetic fixtures.
 */

/** Bars per chart: annual up to 10 years, quarterly up to 12 quarters (the table shows every period). */
export const CHART_PERIODS: Record<PeriodKind, number> = { annual: 10, quarterly: 12 };

export interface MetricView {
  id: MetricId;
  label: string;
  unit: MetricUnit;
  results: MetricResult[];
  values: (number | null)[];
  /** Period indices drawn in the chart (ascending). */
  window: number[];
  /** Latest period with a value (null when none in the whole block). */
  latestIndex: number | null;
  yoy: ChangeResult | null;
  /** Money / EPS: CAGR over the chart window (quarterly: same fiscal quarter). */
  cagr: CagrResult | null;
  /** Percentages / multiples: mean over the chart window. */
  average: { value: number | null; count: number } | null;
}

export interface FinancialsView {
  kind: PeriodKind;
  labels: PeriodLabel[];
  metrics: Record<MetricId, MetricView>;
}

export function buildView(history: FinancialHistory, kind: PeriodKind): FinancialsView {
  const ctx = contextOf(history, kind);
  const labels = periodLabels(history, kind);
  const ends = ctx.series.end;
  const count = ends.length;
  const window = Array.from({ length: Math.min(count, CHART_PERIODS[kind]) }, (_, i) => count - Math.min(count, CHART_PERIODS[kind]) + i);
  const quarters = labels.map((label) => label.fq);
  const metrics = {} as Record<MetricId, MetricView>;
  for (const group of METRIC_GROUPS)
    for (const id of group.metrics) {
      const def = METRICS[id];
      const results = metricSeries(ctx, id);
      const values = results.map((result) => result.value);
      let latestIndex: number | null = null;
      for (let i = count - 1; i >= 0; i--)
        if (values[i] !== null) {
          latestIndex = i;
          break;
        }
      // CAGR only where growth means something; ratios and the sign-flipping non-operating line get an average.
      const ratio = def.unit === 'pct' || def.unit === 'times' || id === 'nonOperatingIncome';
      metrics[id] = {
        id,
        label: def.label,
        unit: def.unit,
        results,
        values,
        window,
        latestIndex,
        yoy: latestIndex === null ? null : yoy(values, ends, latestIndex, def.unit),
        cagr: ratio ? null : windowCagr(values, ends, window, kind === 'quarterly' ? quarters : undefined),
        average: ratio ? windowAverage(values, window) : null,
      };
    }
  return { kind, labels, metrics };
}

// ------------------------------------------------------------------ numbers

export interface MoneyScale {
  divisor: number;
  /** 億 or 兆 (1 兆 = 10,000 億). */
  unit: '億' | '兆';
}
/** 億 by default; 兆 once the largest magnitude reaches 1 兆, so labels stay short (TSMC revenue 2.89 兆). */
export function moneyScale(values: readonly (number | null)[]): MoneyScale {
  const max = Math.max(0, ...values.map((value) => (value === null ? 0 : Math.abs(value))));
  return max >= 1e12 ? { divisor: 1e12, unit: '兆' } : { divisor: 1e8, unit: '億' };
}

/** Decimals for a set of numbers already in display units: same precision for every bar of a chart. */
export function digitsFor(values: readonly (number | null)[], unit: MetricUnit): number {
  const max = Math.max(0, ...values.map((value) => (value === null ? 0 : Math.abs(value))));
  if (unit === 'pct') return max >= 100 ? 0 : 1;
  if (unit === 'times') return max >= 100 ? 0 : max >= 10 ? 1 : 2;
  return max >= 100 ? 0 : max >= 10 ? 1 : 2;
}

const numberFormats = new Map<number, Intl.NumberFormat>();
export function formatNumber(value: number, digits: number): string {
  let format = numberFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    numberFormats.set(digits, format);
  }
  // Avoid "-0.0".
  const rounded = Number(value.toFixed(digits));
  return format.format(rounded === 0 ? 0 : rounded);
}

const CURRENCY_NAMES: Record<string, string> = { USD: '美元', TWD: '元', JPY: '日圓', EUR: '歐元', CNY: '人民幣', HKD: '港元' };
/** 「億美元」「億元」「億 DKK」 for amounts; 「美元」「元」「DKK」 for EPS. */
export function unitText(unit: MetricUnit, currency: string, scale?: MoneyScale): string {
  const name = CURRENCY_NAMES[currency];
  if (unit === 'money') return name ? `${scale?.unit ?? '億'}${name}` : `${scale?.unit ?? '億'} ${currency}`;
  if (unit === 'eps') return name ?? currency;
  return unit === 'pct' ? '%' : '倍';
}

/** Values of one chart / table row converted to display units. */
export function displayValues(values: readonly (number | null)[], unit: MetricUnit, scale: MoneyScale): (number | null)[] {
  return values.map((value) => (value === null ? null : unit === 'money' ? value / scale.divisor : value));
}

/** Headline text, e.g. `3,930` (億美元), `6.11` (美元), `46.2` (%), `0.53` (倍). */
export function formatValue(value: number | null, unit: MetricUnit, scale: MoneyScale): string {
  if (value === null) return '—';
  const shown = unit === 'money' ? value / scale.divisor : value;
  return formatNumber(shown, digitsFor([shown], unit));
}

/** Exact amount with thousands separators (detail panel), e.g. `393,000,000,000`. */
export function formatExact(value: number | null, unit: MetricUnit): string {
  if (value === null) return '—';
  if (unit === 'money') return formatNumber(value, 0);
  if (unit === 'eps') return formatNumber(value, 2);
  return formatNumber(value, unit === 'pct' ? 2 : 2);
}

export interface ChipText {
  text: string;
  tone: 'up' | 'down' | 'flat';
}
const signed = (value: number, digits: number, suffix: string) => `${value > 0 ? '+' : ''}${formatNumber(value, digits)}${suffix}`;
const toneOf = (value: number | null): ChipText['tone'] =>
  value === null || Math.abs(value) < 1e-9 ? 'flat' : value > 0 ? 'up' : 'down';

/** 「YoY +2.1%」, 「YoY +1.3pp」, 「轉盈」… */
export function yoyText(change: ChangeResult | null, unit: MetricUnit): ChipText {
  if (!change) return { text: 'YoY —', tone: 'flat' };
  if (change.label === '轉盈') return { text: '轉盈', tone: 'up' };
  if (change.label === '續虧') return { text: '續虧', tone: 'down' };
  if (change.value === null) return { text: 'YoY —', tone: 'flat' };
  const suffix = change.mode === 'growth' ? '%' : unit === 'pct' ? 'pp' : unit === 'times' ? '倍' : '';
  const text = `YoY ${signed(change.value, unit === 'times' ? 2 : 1, suffix)}`;
  return { text: change.label === '轉虧' ? `轉虧 ${text.slice(4)}` : text, tone: toneOf(change.value) };
}

/** 「CAGR +6.1%（9.0年）」 or the reason it is not computed. */
export function cagrText(result: CagrResult | null): ChipText {
  if (!result) return { text: 'CAGR —', tone: 'flat' };
  if (result.value === null) return { text: result.reason === '期間有虧損，無法計算' ? '期間有虧損，無法計算 CAGR' : 'CAGR —', tone: 'flat' };
  return { text: `CAGR ${signed(result.value, 1, '%')}（${formatNumber(result.years ?? 0, 1)}年）`, tone: toneOf(result.value) };
}

export function averageText(average: { value: number | null; count: number } | null, unit: MetricUnit, currency = 'USD'): ChipText {
  if (!average || average.value === null) return { text: '平均 —', tone: 'flat' };
  if (unit === 'money' || unit === 'eps') {
    const scale = moneyScale([average.value]);
    return { text: `${average.count}期平均 ${formatValue(average.value, unit, scale)}${unitText(unit, currency, scale)}`, tone: 'flat' };
  }
  return { text: `${average.count}期平均 ${formatNumber(average.value, unit === 'times' ? 2 : 1)}${unit === 'pct' ? '%' : '倍'}`, tone: 'flat' };
}

// ------------------------------------------------------------------ notes

export const LEVEL_TEXT: Record<'good' | 'partial' | 'limited', string> = {
  good: '資料完整',
  partial: '部分缺漏',
  limited: '資料有限',
};

/** Plain-Chinese explanations of quality.issues codes. */
export const ISSUE_TEXT: Record<string, string> = {
  'short-history': '年度資料少於 4 年，成長率與平均值僅供參考。',
  'financial-sector-format': '金融業財報格式：沒有毛利與營業利益，毛利率、營業利益率、業外損益、ROIC、利息保障倍數不適用。',
  'missing-cashflow': '缺少部分現金流量表資料（營業現金流或資本支出），自由現金流可能無法計算。',
  'missing-balance': '缺少部分資產負債表資料（權益、負債或現金），ROE、ROIC、負債比可能無法計算。',
  'split-adjusted': '股票分割前的期別已把 EPS 與股數還原成分割後的基準。',
  'foreign-currency': '財報幣別與股價交易幣別不同，比較本益比時要換算匯率。',
  restated: '部分期別的營收或淨利曾被公司重編（新舊差異超過 2%），本頁採用最新數字。',
  'quarter-annual-mismatch': '同一會計年度四季加總與年度數字差異超過 5%，季度數字請小心解讀。',
  'quarterly-ytd-suspected': '季度數字疑似為「年初至今累計」而非單季，未自動拆解，季度圖可能失真。',
  'negative-equity': '有期別股東權益為負（常見於大量買回庫藏股或累積虧損），該期 ROE 與負債權益比不計算。',
  'capex-sign': '資本支出的正負號已校正為「流出為正」。',
  'yahoo-differs': '部分期別 Yahoo 與 SEC 的數字差異超過 2%，本頁保留 SEC 原始申報值。',
  'q4-derived': '第四季數字由「全年減前三季」推算，不是公司單獨公布的單季值。',
  'eps-inconsistent': 'EPS 與「淨利 ÷ 股數」對不起來，可能有特別股、分割或口徑差異。',
  'debt-approx': '有息負債為 Yahoo 的 TotalDebt，可能包含租賃負債，負債比與 ROIC 為近似值。',
  'foreign-filer': '外國發行人（如 ADR）：EPS 為每普通股，不是每 ADR。',
  'currency-changed': '財報幣別曾經變更，較早期別與最新期別的幣別可能不同，跨期比較請小心。',
  ifrs: '外國發行人以 IFRS 申報（SEC 20-F／40-F），科目由 SEC 事實對照而來；淨利與權益以歸屬母公司為準，缺欄位留空、不推算。',
  'ifrs-fallback': '部分期別缺歸屬母公司的淨利或權益，改用含非控制權益的合併數，跨期比較請小心。',
};
export const issueText = (code: string) => ISSUE_TEXT[code] ?? `其他資料提示（${code}）。`;

const SOURCE_NAMES = { sec: 'SEC EDGAR', yahoo: 'Yahoo Finance' } as const;

/** 「資料來源：SEC EDGAR（FY2015–FY2020）＋ Yahoo Finance（FY2021–FY2024）」 */
export function provenanceText(history: FinancialHistory, kind: PeriodKind): string {
  const series = history[kind];
  if (!series.end.length) return '資料來源：—';
  const labels = periodLabels(history, kind);
  const labelOf = new Map(labels.map((label) => [label.end, label.long]));
  const spans = provenance(series);
  // Merge back-to-back spans of the same source by source name for a short sentence.
  const bySource = new Map<string, { from: string; to: string }>();
  for (const span of spans) {
    const known = bySource.get(span.source);
    if (known) known.to = span.to;
    else bySource.set(span.source, { from: span.from, to: span.to });
  }
  const parts = [...bySource].map(([source, span]) => {
    const name = SOURCE_NAMES[source as keyof typeof SOURCE_NAMES];
    if (bySource.size === 1) return name;
    const from = labelOf.get(span.from) ?? span.from;
    const to = labelOf.get(span.to) ?? span.to;
    return `${name}（${from === to ? from : `${from}–${to}`}）`;
  });
  return `資料來源：${parts.join(' ＋ ')}`;
}

/** Honest depth line for the page head: how much history there is now and that it grows each quarter. */
export function depthText(history: FinancialHistory, kind: PeriodKind): string {
  const series = history[kind];
  const count = series.end.length;
  const labels = periodLabels(history, kind);
  const first = labels[0]?.long ?? '';
  if (kind === 'quarterly') return `目前 ${count} 季（${first} 起）；之後每季自動累積。`;
  const years = count;
  if (hasSecHistory(history)) return `含 SEC 申報歷史，共約 ${years} 年（${first} 起）；之後每季自動累積。`;
  return `目前約 ${years} 年（${first} 起）；Yahoo 只回溯約 4 年，之後每季自動累積。`;
}

/** Card subtitle saying which operating income line the latest value used. */
export function oiFieldText(result: MetricResult | undefined): string | null {
  if (!result?.oiField) return null;
  return '營業利益優先採列示欄，缺漏時採一般欄';
}

/** 「FY2024 營收：1,000億 → 1,030億（重編）」 for the quality panel. */
export function revisionText(history: FinancialHistory, revision: Revision): string {
  const label = periodLabels(history, revision.period).find((item) => item.end === revision.end)?.long ?? revision.end;
  const per = revision.field === 'epsDiluted' || revision.field === 'epsBasic';
  const shares = revision.field === 'dilutedShares';
  const amount = (value: number | null) => {
    if (value === null) return '—';
    if (per) return formatNumber(value, 2);
    if (shares) return `${formatNumber(value / 1e8, 2)}億股`;
    const scale = moneyScale([value]);
    return `${formatNumber(value / scale.divisor, 2)}${scale.unit}`;
  };
  const kind = revision.kind === 'restated' ? '重編' : '保留 SEC 值，Yahoo 不同';
  return `${label} ${FIELD_LABELS[revision.field]}：${amount(revision.old)} → ${amount(revision.new)}（${kind}）`;
}
