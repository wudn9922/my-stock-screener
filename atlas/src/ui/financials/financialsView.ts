import {
  hasFinmindHistory,
  hasSecHistory,
  provenance,
  type FinancialHistory,
  type PeriodKind,
  type PeriodSource,
  type Revision,
} from '../../fundamentals/FinancialHistory';
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
  type Basis,
  type CagrResult,
  type ChangeResult,
  type MetricId,
  type MetricResult,
  type MetricUnit,
  type PeriodLabel,
} from '../../fundamentals/financialMetrics';
import { toneClass } from '../format';

/**
 * View model and text for the financials page: which periods each chart shows, the headline value,
 * YoY / CAGR chips, number formatting in 億 / 兆, and the honest notes about depth and provenance.
 * Pure (no React), so it is unit-tested with the synthetic fixtures.
 */

/** Default bars per chart: annual 10 years, quarterly 12 quarters (the table shows every period). */
export const CHART_PERIODS: Record<PeriodKind, number> = { annual: 10, quarterly: 12 };
/** Range choices offered on the charts: years for annual, quarters for quarterly. */
export const SPAN_OPTIONS: Record<PeriodKind, readonly number[]> = { annual: [5, 10, 15], quarterly: [12, 20, 40, 60] };

export interface SpanOption {
  span: number;
  /** A range needs at least that many periods in the file. */
  enabled: boolean;
}
export function spanOptions(kind: PeriodKind, count: number): SpanOption[] {
  return SPAN_OPTIONS[kind].map((span) => ({ span, enabled: count >= span }));
}
export const spanLabel = (kind: PeriodKind, span: number) => (kind === 'annual' ? `${span}年` : `${span}季`);

/** Indices (ascending) of the last `span` periods of a block of `count`; every period when there are fewer. */
export function windowIndices(count: number, span: number): number[] {
  const shown = Math.max(0, Math.min(count, span));
  return Array.from({ length: shown }, (_, i) => count - shown + i);
}

export interface MetricView {
  id: MetricId;
  label: string;
  unit: MetricUnit;
  results: MetricResult[];
  values: (number | null)[];
  /** YoY for every period (same alignment as the cards); index-aligned with `values`. */
  changes: ChangeResult[];
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

/** `span` is the number of periods drawn (defaults to CHART_PERIODS); the full series stays in every value array. */
export function buildView(history: FinancialHistory, kind: PeriodKind, span: number = CHART_PERIODS[kind]): FinancialsView {
  const ctx = contextOf(history, kind);
  const labels = periodLabels(history, kind);
  const ends = ctx.series.end;
  const count = ends.length;
  const window = windowIndices(count, span);
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
      const changes = values.map((_, index) => yoy(values, ends, index, def.unit));
      // CAGR only where growth means something; ratios and the sign-flipping non-operating line get an average.
      const ratio = def.unit === 'pct' || def.unit === 'times' || id === 'nonOperatingIncome';
      metrics[id] = {
        id,
        label: def.label,
        unit: def.unit,
        results,
        values,
        changes,
        window,
        latestIndex,
        yoy: latestIndex === null ? null : changes[latestIndex]!,
        cagr: ratio ? null : windowCagr(values, ends, window, kind === 'quarterly' ? quarters : undefined),
        average: ratio ? windowAverage(values, window) : null,
      };
    }
  return { kind, labels, metrics };
}

// ------------------------------------------------------------------ key metrics (first screen)

export const KEY_METRIC_IDS: readonly MetricId[] = [
  'revenue',
  'grossProfit',
  'netIncome',
  'eps',
  'grossMargin',
  'operatingMargin',
  'netMargin',
];
/** Mini bars per key metric: the latest eight periods. */
export const KEY_SPARK_PERIODS = 8;

export interface KeyTile {
  id: MetricId;
  label: string;
  unit: MetricUnit;
  /** Value at the shared latest period; null when this metric has no value there. */
  value: number | null;
  /** How the value was obtained at the latest period (derived shows 「推算」). */
  basis: Basis | null;
  /** Why it is missing (shown as the tooltip and under the dash). */
  reason: string | null;
  /** YoY at the shared latest period (growth for money and EPS, percentage points for ratios). */
  change: ChangeResult | null;
  /** Up to KEY_SPARK_PERIODS periods ending at the shared latest period, oldest first. */
  spark: { index: number; value: number | null }[];
}

export interface KeySummary {
  /** The newest period any key metric has a value for (null when none does). */
  index: number | null;
  tiles: KeyTile[];
}

/** All tiles show the same period so the headline row never mixes quarters: the newest period with data. */
export function keySummary(view: FinancialsView): KeySummary {
  let index: number | null = null;
  for (const id of KEY_METRIC_IDS) {
    const latest = view.metrics[id].latestIndex;
    if (latest !== null && (index === null || latest > index)) index = latest;
  }
  const tiles = KEY_METRIC_IDS.map((id): KeyTile => {
    const data = view.metrics[id];
    if (index === null)
      return { id, label: data.label, unit: data.unit, value: null, basis: null, reason: '沒有可用的期別', change: null, spark: [] };
    const value = data.values[index] ?? null;
    const spark = windowIndices(index + 1, KEY_SPARK_PERIODS).map((i) => ({ index: i, value: data.values[i] ?? null }));
    return {
      id,
      label: data.label,
      unit: data.unit,
      value,
      basis: data.results[index]?.basis ?? null,
      reason: value === null ? (data.results[index]?.reason ?? '沒有資料') : null,
      change: data.changes[index] ?? null,
      spark,
    };
  });
  return { index, tiles };
}

/** Statement table cell in YoY mode: 「+12.3%」, or 轉盈 / 續虧 / 轉虧 when the base sign flips, or the dash. */
export function changeCellText(change: ChangeResult | null): ChipText {
  if (!change) return { text: '—', tone: 'flat' };
  if (change.label === '轉盈') return { text: '轉盈', tone: 'up' };
  if (change.label === '續虧') return { text: '續虧', tone: 'down' };
  if (change.label === '轉虧') return { text: '轉虧', tone: 'down' };
  if (change.value === null) return { text: '—', tone: 'flat' };
  return { text: signed(change.value, 1, change.mode === 'diff' ? ' 百分點' : '%'), tone: toneClass(change.value) };
}

/** 「年增 +12.3%」 for money and EPS, 「年增 +1.3 百分點」 for margins; 轉盈 / 續虧 / 轉虧 when the base sign flips. */
export function keyChangeText(change: ChangeResult | null): ChipText {
  if (!change) return { text: '年增 —', tone: 'flat' };
  if (change.label === '轉盈') return { text: '轉盈', tone: 'up' };
  if (change.label === '續虧') return { text: '續虧', tone: 'down' };
  if (change.value === null) return { text: '年增 —', tone: 'flat' };
  const tone = toneClass(change.value);
  if (change.mode === 'diff') return { text: `年增 ${signed(change.value, 1, ' 百分點')}`, tone };
  if (change.label === '轉虧') return { text: `轉虧 ${signed(change.value, 1, '%')}`, tone };
  return { text: `年增 ${signed(change.value, 1, '%')}`, tone };
}

// ------------------------------------------------------------------ latest quarter strip and fold summaries

/** Strip-sized 年增 text: 「年增 +12.3%」, margins 「年增 +1.3pp」 (short enough for a phone cell). */
export function stripChangeText(change: ChangeResult | null): ChipText {
  if (!change || change.value === null) return keyChangeText(change);
  if (change.mode === 'diff') return { text: `年增 ${signed(change.value, 1, 'pp')}`, tone: toneOf(change.value) };
  return keyChangeText(change);
}

/** The 「當季財報」 strip, in display order: EPS, revenue and the two margins. */
export const STRIP_METRIC_IDS: readonly MetricId[] = ['eps', 'revenue', 'grossMargin', 'netMargin'];

export interface StripCell {
  id: MetricId;
  label: string;
  /** 「6.11」「1,030億」「46.2」; 「—」 when the quarter has no value. */
  value: string;
  /** Unit (「美元」「億美元」「%」); empty when there is no value. Shown after the label, or after the value for %. */
  unit: string;
  /** 「年增 +12.3%」, 「年增 +1.3 百分點」, 「轉盈」… or 「年增 —」. Same-quarter comparison, actual values only. */
  change: ChipText;
  /** Why the value is missing (tooltip). */
  reason: string | null;
}

export interface QuarterStrip {
  /** Newest quarter with a value for any strip metric; null when the file has none. */
  index: number | null;
  period: string | null;
  cells: StripCell[];
}

/** The newest quarter the strip shows, with EPS, revenue and both margins at that quarter (or a dash each). */
export function quarterStrip(view: FinancialsView, currency: string): QuarterStrip {
  let index: number | null = null;
  for (const id of STRIP_METRIC_IDS) {
    const latest = view.metrics[id].latestIndex;
    if (latest !== null && (index === null || latest > index)) index = latest;
  }
  const cells = STRIP_METRIC_IDS.map((id): StripCell => {
    const data = view.metrics[id];
    const value = index === null ? null : (data.values[index] ?? null);
    const scale = moneyScale(value === null ? [] : [value]);
    return {
      id,
      label: data.label,
      value: value === null ? '—' : formatValue(value, data.unit, scale),
      unit: value === null ? '' : unitText(data.unit, currency, scale),
      change: index === null ? { text: '年增 —', tone: 'flat' } : stripChangeText(data.changes[index] ?? null),
      reason: value === null ? (index === null ? '這檔股票沒有季度資料' : (data.results[index]?.reason ?? '沒有資料')) : null,
    };
  });
  return { index, period: index === null ? null : (view.labels[index]?.long ?? null), cells };
}

/** Folded-card line for the key metrics: 「FY2025 Q4：營收 1,205億美元、淨利 …、EPS 2.53美元」. */
export function keyMetricsSummary(view: FinancialsView, currency: string): string {
  const summary = keySummary(view);
  if (summary.index === null) return '沒有可用的期別';
  const text = (id: MetricId) => {
    const tile = summary.tiles.find((item) => item.id === id);
    if (!tile || tile.value === null) return '—';
    const scale = moneyScale([tile.value]);
    return `${formatValue(tile.value, tile.unit, scale)}${unitText(tile.unit, currency, scale)}`;
  };
  return `${view.labels[summary.index]?.long ?? ''}：EPS ${text('eps')}、營收 ${text('revenue')}`;
}

/** Folded-card line for the summary chips: 「營收 CAGR +6.1% · EPS CAGR +7.0% · 平均 ROIC 22.4%」 (annual window). */
export function summaryChipsLine(annual: FinancialsView): string {
  const growth = (id: MetricId) => {
    const result = annual.metrics[id].cagr;
    return result?.value === null || !result ? '—' : signed(result.value, 1, '%');
  };
  const roic = annual.metrics.roic.average?.value;
  return `營收 CAGR ${growth('revenue')} · EPS CAGR ${growth('eps')} · 平均 ROIC ${roic === null || roic === undefined ? '—' : `${formatNumber(roic, 1)}%`}`;
}

/**
 * Folded-card line for a metric card: the first two metrics that have a value, at the period of the first
 * of them. 「營業現金流 1,020億美元、自由現金流 830億美元（FY2025）」; 「沒有可用的數字」 when none does.
 */
export function cardSummaryLine(view: FinancialsView, ids: readonly MetricId[], currency: string): string {
  const parts: string[] = [];
  let period: string | null = null;
  for (const id of ids) {
    const data = view.metrics[id];
    if (data.latestIndex === null) continue;
    const value = data.values[data.latestIndex];
    if (value === null || value === undefined) continue;
    const scale = moneyScale([value]);
    parts.push(`${data.label} ${formatValue(value, data.unit, scale)}${unitText(data.unit, currency, scale)}`);
    period ??= view.labels[data.latestIndex]?.long ?? null;
    if (parts.length === 2) break;
  }
  if (!parts.length) return '沒有可用的數字';
  return `${parts.join('、')}${period ? `（${period}）` : ''}`;
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
  'yahoo-differs': '部分期別 Yahoo 與官方申報（SEC 或 FinMind）的數字差異超過 2%，本頁保留官方申報值。',
  'q4-derived': '第四季數字由「全年減前三季」推算，不是公司單獨公布的單季值。',
  'q4-eps-from-shares': '部分第四季 EPS 以第四季淨利 ÷ 推算的第四季加權股數估算（年度股數 × 4 − 前三季股數），誤差多在 5% 內',
  'eps-inconsistent': 'EPS 與「淨利 ÷ 股數」對不起來，可能有特別股、分割或口徑差異。',
  'debt-approx': '有息負債為近似值：Yahoo 的 TotalDebt，或 FinMind 的短期借款、長期借款與應付公司債（含一年內到期）加總，不含租賃負債，負債比與 ROIC 為近似值。',
  'debt-missing': '有息負債科目在官方資料中找不到，負債比與 ROIC 不計算。',
  'eps-annual-summed': '年度 EPS 為四季 EPS 加總（與公司年報的全年 EPS 可能有一分上下的差異），年度 EPS 的年增率請小心解讀。',
  'foreign-filer': '外國發行人（如 ADR）：EPS 為每普通股，不是每 ADR。',
  'currency-changed': '財報幣別曾經變更，較早期別與最新期別的幣別可能不同，跨期比較請小心。',
  ifrs: '外國發行人以 IFRS 申報（SEC 20-F／40-F），科目由 SEC 事實對照而來；淨利與權益以歸屬母公司為準，缺欄位留空、不推算。',
  'ifrs-fallback': '部分期別缺歸屬母公司的淨利或權益，改用含非控制權益的合併數，跨期比較請小心。',
  'foreign-gaap': '外國發行人以美國一般公認會計原則（US GAAP）申報（SEC 20-F／40-F 年報、6-K 或 10-Q 季報），科目由 SEC 事實對照而來；淨利與權益以歸屬母公司為準，缺欄位留空、不推算。',
  'foreign-gaap-fallback': '部分期別缺歸屬母公司的淨利或權益，改用含非控制權益的合併數，跨期比較請小心。',
  'adr-eps-rescaled': 'SEC 的每股 EPS 與股數已依 ADR 換算（與 Yahoo 同為每 ADR 基準），口徑見財報頁說明。',
  'eps-yahoo-only': 'SEC 的每股 EPS 與 Yahoo 的每 ADR EPS 無法確認換算比例，EPS 與股數只採 Yahoo（每 ADR），較早年度沒有 EPS。',
};
export const issueText = (code: string) => ISSUE_TEXT[code] ?? `其他資料提示（${code}）。`;

const SOURCE_NAMES = { sec: 'SEC EDGAR', finmind: '公開資訊觀測站（FinMind）', yahoo: 'Yahoo Finance' } as const;

/** Short source name of one period (the statement table's 來源 row and the detail card). */
export const SOURCE_LABEL: Record<PeriodSource, string> = { sec: 'SEC', finmind: 'FinMind', yahoo: 'Yahoo' };

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

/** A whole number as is; any other factor to two decimals (「2.50」). */
const factorText = (factor: number) => (Number.isInteger(factor) ? String(factor) : factor.toFixed(2));

/**
 * Basis note for a foreign reporter whose SEC EPS were put on the ADR basis (adrRatio epsFactor k ≠ 1).
 * Null without an adrRatio, and for k = 1 (one ADR is one ordinary share: nothing to explain).
 */
export function adrEpsNote(history: FinancialHistory): string | null {
  if (!history.adrRatio || history.adrRatio.epsFactor === 1) return null;
  return `EPS 以每單位 ADR 計（1 ADR ＝ ${factorText(history.adrRatio.epsFactor)} 股普通股，由 SEC 每股數字換算）`;
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
  if (hasFinmindHistory(history)) return `含 FinMind（公開資訊觀測站）申報歷史，共約 ${years} 年（${first} 起）；之後每季自動累積。`;
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
  const official = hasFinmindHistory(history) && !hasSecHistory(history) ? 'FinMind' : 'SEC';
  const kind = revision.kind === 'restated' ? '重編' : `保留 ${official} 值，Yahoo 不同`;
  return `${label} ${FIELD_LABELS[revision.field]}：${amount(revision.old)} → ${amount(revision.new)}（${kind}）`;
}
