import { UNIVERSE_FIELDS, UNIVERSE_PROFILE_FIELDS } from '../../../src/report/universe';

/**
 * Synthetic full-market universe.json bodies with the profile columns (sector / industry / marketCap),
 * for the industry heatmap tests (unit and e2e). Deterministic: a seeded generator, no clock, no network.
 */

const TW_INDUSTRIES = [
  '水泥工業', '食品工業', '塑膠工業', '紡織纖維', '電機機械', '電器電纜', '化學工業', '生技醫療業', '玻璃陶瓷', '造紙工業',
  '鋼鐵工業', '橡膠工業', '汽車工業', '半導體業', '電腦及週邊設備業', '光電業', '通信網路業', '電子零組件業', '電子通路業', '資訊服務業',
  '其他電子業', '建材營造業', '航運業', '觀光餐旅', '金融保險業', '貿易百貨業', '油電燃氣業', '綜合', '其他', '文化創意業',
];

export const US_SECTORS = [
  'Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical', 'Industrials', 'Communication Services',
  'Consumer Defensive', 'Energy', 'Basic Materials', 'Real Estate', 'Utilities',
];
const US_INDUSTRY_NAMES = [
  'Semiconductors', 'Semiconductor Equipment & Materials', 'Software - Infrastructure', 'Software - Application', 'Biotechnology',
  'Drug Manufacturers - General', 'Banks - Regional', 'Asset Management', 'Insurance - Property & Casualty', 'Specialty Retail',
  'Auto Manufacturers', 'Aerospace & Defense', 'Oil & Gas E&P', 'REIT - Industrial', 'Utilities - Regulated Electric', 'Gold', 'Packaged Foods',
];

export function rng(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Spec {
  count: number;
  /** [sector or null, industry or null] per industry; stocks are spread Zipf-like over them. */
  industries: [string | null, string | null][];
  symbol: (index: number) => string;
  nameOf: (index: number) => string;
  capScale: number;
  capMissing: number;
  seed: number;
}

function rows(spec: Spec): (string | number | null)[][] {
  const random = rng(spec.seed);
  const fields: string[] = [...UNIVERSE_FIELDS, ...UNIVERSE_PROFILE_FIELDS];
  const weights = spec.industries.map((_, i) => 1 / (i + 1) ** 0.8);
  const total = weights.reduce((a, b) => a + b, 0);
  // Every industry gets at least 3 stocks, the rest follow the Zipf weights.
  const counts = weights.map(() => 3);
  let left = spec.count - 3 * counts.length;
  for (let i = 0; i < counts.length; i++) {
    const extra = Math.min(left, Math.floor(((spec.count - 3 * counts.length) * weights[i]!) / total));
    counts[i]! += extra;
    left -= extra;
  }
  for (let i = 0; left > 0; i = (i + 1) % counts.length, left--) counts[i]!++;
  const out: (string | number | null)[][] = [];
  let index = 0;
  spec.industries.forEach(([sector, industry], k) => {
    // Industry-level drift so that colours differ between industries.
    const drift = (random() - 0.5) * 6;
    for (let n = 0; n < counts[k]!; n++, index++) {
      const row: (string | number | null)[] = Array(fields.length).fill(null);
      const set = (field: string, value: string | number | null) => (row[fields.indexOf(field)] = value);
      const cap = spec.capScale * Math.exp((random() - 0.35) * 4.5);
      set('symbol', spec.symbol(index));
      set('name', spec.nameOf(index));
      set('close', 10 + random() * 200);
      set('changePct', +(drift * 0.3 + (random() - 0.5) * 5).toFixed(2));
      set('volume', Math.round(1e5 + random() * 1e7));
      set('r5', +(drift * 0.6 + (random() - 0.5) * 9).toFixed(2));
      set('r21', +(drift + (random() - 0.5) * 20).toFixed(2));
      set('r63', +(drift * 1.5 + (random() - 0.5) * 36).toFixed(2));
      set('turnover20', Math.round(cap * (0.001 + random() * 0.004)));
      set('sector', sector);
      set('industry', industry);
      set('marketCap', random() < spec.capMissing ? null : Math.round(cap));
      out.push(row);
    }
  });
  return out;
}

export interface UniverseOptions {
  twCount?: number;
  twIndustries?: number;
  usCount?: number;
  usIndustries?: number;
  /** Share of stocks without a market cap. */
  capMissing?: number;
  /** Leave the profile columns out entirely (today's online file). */
  withoutProfile?: boolean;
  scope?: 'full' | 'liquid';
}

/** A parseUniverse-ready body: both markets, profile columns appended after the metric columns. */
export function universeBody(options: UniverseOptions = {}) {
  const { twCount = 1800, twIndustries = 30, usCount = 5000, usIndustries = 120, capMissing = 0.03, withoutProfile = false, scope = 'full' } = options;
  const twIndustryList: [string | null, string | null][] = Array.from({ length: twIndustries }, (_, i) => [null, TW_INDUSTRIES[i] ?? `產業${i + 1}`]);
  const usIndustryList: [string | null, string | null][] = Array.from({ length: usIndustries }, (_, i) => {
    const sector = US_SECTORS[i % US_SECTORS.length]!;
    const base = US_INDUSTRY_NAMES[i];
    return [sector, base ?? `${sector.split(' ')[0]} Group ${Math.floor(i / US_SECTORS.length) + 1}`];
  });
  const common = { capMissing };
  const tw = rows({
    ...common,
    count: twCount,
    industries: twIndustryList,
    symbol: (i) => `${1101 + i}.${i % 5 === 0 ? 'TWO' : 'TW'}`,
    nameOf: (i) => `台股${i + 1}`,
    capScale: 5e9,
    seed: 7,
  });
  const us = rows({
    ...common,
    count: usCount,
    industries: usIndustryList,
    symbol: (i) => `U${i.toString(36).toUpperCase().padStart(4, '0')}`,
    nameOf: (i) => `US Company ${i + 1}`,
    capScale: 2e9,
    seed: 11,
  });
  const fieldNames: string[] = withoutProfile ? [...UNIVERSE_FIELDS] : [...UNIVERSE_FIELDS, ...UNIVERSE_PROFILE_FIELDS];
  const cut = (list: (string | number | null)[][]) => (withoutProfile ? list.map((row) => row.slice(0, UNIVERSE_FIELDS.length)) : list);
  return {
    version: 1,
    generatedAt: '2026-10-13T00:00:00Z',
    reportDate: '2026-10-13',
    fields: fieldNames,
    markets: {
      TW: { benchmark: '^TWII', asOf: '2026-10-13', universe: '台股上市櫃', scope, rows: cut(tw) },
      US: { benchmark: 'SPY', asOf: '2026-10-12', universe: '美股上市股票', scope, rows: cut(us) },
    },
  };
}
