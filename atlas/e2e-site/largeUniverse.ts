/**
 * Synthetic full-market universe.json (scope "full", profile columns) of realistic size: about 1,850 Taiwan
 * and 6,000 US rows. Deterministic (seeded); used by the screener performance test and e2e. Not real data.
 */
const FIELDS = [
  'symbol', 'name', 'close', 'changePct', 'volume', 'r5', 'r21', 'r63', 'ma20', 'ma50', 'ma200', 'ema20',
  'ma20Slope5', 'trend', 'hi52', 'fromHi52', 'hiBars', 'rs21', 'rs63', 'rsRank', 'volVsPrev', 'turnover20',
  'sector', 'industry', 'marketCap',
];
const TW_INDUSTRIES = ['半導體業', '電子零組件業', '電腦及週邊設備業', '光電業', '金融保險業', '航運業', '生技醫療業', '塑膠工業', '建材營造業', '其他業'];
const US_SECTORS: Record<string, string[]> = {
  Technology: ['Semiconductors', 'Computer Software: Prepackaged Software', 'EDP Services'],
  'Health Care': ['Biotechnology: Pharmaceutical Preparations', 'Medical/Dental Instruments'],
  Finance: ['Major Banks', 'Investment Managers'],
  Industrials: ['Industrial Machinery/Components', 'Aerospace'],
  'Consumer Discretionary': ['Restaurants', 'Auto Manufacturing'],
  Energy: ['Oil & Gas Production'],
};

function random(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const round = (value: number, digits = 2) => Math.round(value * 10 ** digits) / 10 ** digits;

function usTicker(index: number): string {
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  let text = '';
  let value = index + 26 * 27; // three letters and up
  while (value > 0) {
    text = letters[value % 26]! + text;
    value = Math.floor(value / 26);
  }
  return text;
}

function row(rand: () => number, symbol: string, name: string, profile: [string | null, string, number | null]) {
  const close = round(5 + rand() * 600);
  const r63 = round((rand() - 0.4) * 60);
  const missing = rand() < 0.04; // a few young listings without long windows
  return [
    symbol, name, close, round((rand() - 0.5) * 8), Math.round(rand() * 5e7),
    round((rand() - 0.5) * 12), round((rand() - 0.45) * 25), missing ? null : r63,
    round((rand() - 0.5) * 20), round((rand() - 0.5) * 30), missing ? null : round((rand() - 0.4) * 50),
    round((rand() - 0.5) * 18), round((rand() - 0.5) * 6), ['up', 'down', 'mixed'][Math.floor(rand() * 3)],
    round(close * (1 + rand() * 0.5)), round(-rand() * 60), missing ? 40 : 252,
    round((rand() - 0.5) * 20), missing ? null : round(r63 - 5), missing ? null : 1 + Math.floor(rand() * 99),
    round(0.2 + rand() * 3), Math.round(rand() * 5e9), ...profile,
  ];
}

export function buildLargeUniverse(twCount = 1850, usCount = 6000) {
  const rand = random(20261014);
  const sectors = Object.keys(US_SECTORS);
  const tw = Array.from({ length: twCount }, (_, index) => {
    const code = 1101 + index * 4;
    const symbol = `${code}.${index % 9 < 5 ? 'TW' : 'TWO'}`;
    return row(rand, symbol, `台股${code}`, [null, TW_INDUSTRIES[index % TW_INDUSTRIES.length]!, Math.round(rand() * 2e12)]);
  });
  const us = Array.from({ length: usCount }, (_, index) => {
    const sector = sectors[index % sectors.length]!;
    const industries = US_SECTORS[sector]!;
    return row(rand, usTicker(index), `Company ${index} Inc.`, [sector, industries[index % industries.length]!, Math.round(rand() * 5e11)]);
  });
  return {
    version: 1,
    generatedAt: '2026-10-14T09:30:00+00:00',
    reportDate: '2026-10-13',
    fields: FIELDS,
    markets: {
      TW: {
        benchmark: '^TWII', asOf: '2026-10-13', scope: 'full', rows: tw,
        universe: `台股上市＋上櫃普通股（上市 1,060、上櫃 ${(twCount - 1060 + 40).toLocaleString('en-US')} 檔）`,
        note: `名單 ${(twCount + 40).toLocaleString('en-US')} 檔、列出 ${twCount.toLocaleString('en-US')} 檔；排除近 5 個交易日無 K 線（停牌、暫停交易等）31 檔、近 20 日零成交 9 檔。不含 ETF、TDR、特別股、權證與興櫃。產業別：證交所／櫃買中心公司基本資料；市值＝已發行普通股數×收盤價。`,
      },
      US: {
        benchmark: 'SPY', asOf: '2026-10-13', scope: 'full', rows: us,
        universe: `美股 NYSE／NASDAQ／NYSE American 等交易所普通股與 ADR（${(usCount + 120).toLocaleString('en-US')} 檔）`,
        note: `名單 ${(usCount + 120).toLocaleString('en-US')} 檔、列出 ${usCount.toLocaleString('en-US')} 檔；排除近 5 個交易日無 K 線（停牌、暫停交易等）120 檔。不含 ETF、基金、權證、權利、單位、特別股、債券與 SPAC。產業分類與市值：Nasdaq 股票篩選器（英文分類），2026-10-13 更新。`,
      },
    },
  };
}
