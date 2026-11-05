// Public, synthetic #224 wire-shape fixture; no server function is imported.
export const D3 = 1793667600, D4 = 1793754000, D5 = 1793840400;
export function intradayFilledPayload(filled: boolean) {
  return { chart: { result: [{
    meta: { symbol:'^TWII', currency:'TWD', exchangeTimezoneName:'Asia/Taipei', gmtoffset:28800,
      regularMarketTime:D5+1800, regularMarketPrice:43250,
      ...(filled ? {atlasIntradayFill:['2026-11-04','2026-11-05']} : {}) },
    timestamp:[D3,D4,D5], indicators:{quote:[{
      open:[44481.59,filled?43000:null,filled?43200:null], high:[44614.64,filled?43102:null,filled?43300:null],
      low:[43191.58,filled?42898:null,filled?43100:null], close:[43307.54,filled?42948.91:null,filled?43250:null],
      volume:[4346200,filled?200:null,filled?100:null],
    }]},
  }], error:null } };
}
