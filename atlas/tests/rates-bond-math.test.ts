import { describe, expect, it } from 'vitest';
import {
  SCENARIO_SHOCKS_BP,
  formulaParts,
  fromPublishedConvexity,
  oneYearHold,
  pickDuration,
  scenarioPct,
  scenarioTable,
  toScenarioEtf,
  type ScenarioEtf,
} from '../src/rates/bondMath';

/** Semi-annual-compounding zero coupon: price per 1 of face, modified duration and convexity (years²). */
function zero(maturity: number, yieldDecimal: number) {
  const g = 1 + yieldDecimal / 2;
  return {
    price: (y: number) => Math.pow(1 + y / 2, -2 * maturity),
    duration: maturity / g,
    convexity: (maturity * (maturity + 0.5)) / (g * g),
  };
}

/** Equal-par zero ladder: PV-weighted modified duration and convexity, plus exact repricing. */
function zeroLadder(maturities: number[], yieldDecimal: number) {
  const legs = maturities.map((m) => zero(m, yieldDecimal));
  const value = (y: number) => legs.reduce((sum, leg) => sum + leg.price(y), 0);
  const base = value(yieldDecimal);
  const duration = legs.reduce((sum, leg) => sum + (leg.price(yieldDecimal) / base) * leg.duration, 0);
  const convexity = legs.reduce((sum, leg) => sum + (leg.price(yieldDecimal) / base) * leg.convexity, 0);
  return { value, base, duration, convexity };
}

const exactPct = (price: (y: number) => number, y: number, bp: number) => (price(y + bp / 10000) / price(y) - 1) * 100;

describe('scenarioPct', () => {
  it('is −D·Δy + ½·C·Δy² in percent, with Δy = bp / 10000', () => {
    // 1% rise, D = 16.5, C = 360 years²: −16.5% + 1.8% = −14.7%.
    expect(scenarioPct(16.5, 360, 100)).toBeCloseTo(-14.7, 10);
    expect(scenarioPct(16.5, 360, -100)).toBeCloseTo(18.3, 10);
    expect(scenarioPct(16.5, 360, 0)).toBe(0);
    // Without convexity the change is symmetric.
    expect(scenarioPct(10, 0, 50)).toBeCloseTo(-5, 10);
    expect(scenarioPct(10, 0, -50)).toBeCloseTo(5, 10);
  });

  it('matches exact repricing of a 30-year zero within 0.5pp at ±100bp', () => {
    for (const y of [0.02, 0.035, 0.045, 0.06]) {
      const bond = zero(30, y);
      for (const bp of [-100, -50, -25, 25, 50, 100]) {
        const error = Math.abs(scenarioPct(bond.duration, bond.convexity, bp)! - exactPct(bond.price, y, bp));
        expect(error, `y=${y} bp=${bp}`).toBeLessThan(0.5);
      }
      // Without the convexity term the same 100bp move is off by about 4pp: the term matters.
      const noConvexity = Math.abs(scenarioPct(bond.duration, 0, 100)! - exactPct(bond.price, y, 100));
      expect(noConvexity).toBeGreaterThan(3.5);
    }
  });

  it('documents the second-order error at ±200bp (a few pp for a 30-year zero)', () => {
    const bond = zero(30, 0.045);
    for (const bp of [-200, 200]) {
      const error = Math.abs(scenarioPct(bond.duration, bond.convexity, bp)! - exactPct(bond.price, 0.045, bp));
      expect(error).toBeGreaterThan(2);
      expect(error).toBeLessThan(5);
    }
  });

  it('matches exact repricing of an equal-par 25–30y zero ladder (the ZROZ/GOVZ model proxy)', () => {
    const ladder = zeroLadder([25, 25.5, 26, 26.5, 27, 27.5, 28, 28.5, 29, 29.5, 30], 0.047);
    expect(ladder.duration).toBeGreaterThan(25);
    expect(ladder.duration).toBeLessThan(28);
    for (const bp of [-100, 100]) {
      const error = Math.abs(scenarioPct(ladder.duration, ladder.convexity, bp)! - exactPct(ladder.value, 0.047, bp));
      expect(error).toBeLessThan(0.5);
    }
  });

  it('scales by leverage and never goes below −100%', () => {
    expect(scenarioPct(16.5, 360, 100, 3)).toBeCloseTo(-44.1, 10);
    expect(scenarioPct(16.5, 360, -100, 3)).toBeCloseTo(54.9, 10);
    // 3 × (−16.5 × 3% + ½ × 360 × (3%)²) = 3 × (−49.5% + 16.2%) = −99.9%: just above the floor.
    expect(scenarioPct(16.5, 360, 300, 3)).toBeCloseTo(-99.9, 10);
    // 3 × (−17 × 3% + ½ × 300 × (3%)²) = 3 × (−51% + 13.5%) = −112.5% → floored.
    expect(scenarioPct(17, 300, 300, 3)).toBe(-100);
    expect(scenarioPct(60, 0, 300)).toBe(-100);
    expect(scenarioPct(16.5, 360, 200, 3)).toBeGreaterThan(-100);
  });

  it('returns null for missing or non-finite inputs and treats a missing convexity as zero', () => {
    expect(scenarioPct(null, 360, 100)).toBeNull();
    expect(scenarioPct(Number.NaN, 360, 100)).toBeNull();
    expect(scenarioPct(16.5, 360, Number.POSITIVE_INFINITY)).toBeNull();
    expect(scenarioPct(16.5, null, 100)).toBeCloseTo(-16.5, 10);
    expect(scenarioPct(16.5, 360, 100, 0)).toBeNull();
  });
});

describe('convexity units', () => {
  it('converts the iShares published figure to years² (×100)', () => {
    expect(fromPublishedConvexity(3.6)).toBe(360);
    expect(fromPublishedConvexity(null)).toBeNull();
  });

  it('a 30-year zero has convexity near 875 years², i.e. 8.75 in the published convention', () => {
    const bond = zero(30, 0.045);
    expect(bond.convexity).toBeGreaterThan(860);
    expect(bond.convexity).toBeLessThan(890);
    // Feeding the published (÷100) figure by mistake would make the convexity term vanish.
    const wrong = scenarioPct(bond.duration, bond.convexity / 100, -100)!;
    const right = scenarioPct(bond.duration, bond.convexity, -100)!;
    expect(right - wrong).toBeGreaterThan(4);
  });
});

const tlt: ScenarioEtf = {
  symbol: 'TLT',
  leverage: 1,
  expenseRatio: 0.15,
  effectiveDuration: 16.5,
  model: { duration: 16.9, convexity: 362 },
};
const tmf: ScenarioEtf = {
  symbol: 'TMF',
  leverage: 3,
  expenseRatio: 1.06,
  effectiveDuration: null,
  model: { duration: 16.9, convexity: 362 },
};
const edv: ScenarioEtf = {
  symbol: 'EDV',
  leverage: 1,
  expenseRatio: 0.05,
  effectiveDuration: null,
  model: { duration: 24.3, convexity: 610 },
};

describe('pickDuration', () => {
  it('prefers the published duration, else the model', () => {
    expect(pickDuration(tlt)).toEqual({ value: 16.5, basis: 'published' });
    expect(pickDuration(edv)).toEqual({ value: 24.3, basis: 'model' });
    expect(pickDuration(tlt, 'model')).toEqual({ value: 16.9, basis: 'model' });
    expect(pickDuration({ symbol: 'X', leverage: 1, expenseRatio: null, effectiveDuration: null, model: null })).toEqual({
      value: null,
      basis: null,
    });
  });
});

describe('scenarioTable', () => {
  it('builds one row per ETF with the standard shocks and flags leveraged rows as single-day only', () => {
    const rows = scenarioTable([tlt, tmf, edv]);
    expect(SCENARIO_SHOCKS_BP).toEqual([-200, -100, -50, -25, 25, 50, 100, 200]);
    expect(rows.map((row) => row.symbol)).toEqual(['TLT', 'TMF', 'EDV']);
    const [tltRow, tmfRow, edvRow] = rows;
    expect(tltRow!.duration).toBe(16.5);
    expect(tltRow!.durationBasis).toBe('published');
    expect(tltRow!.convexity).toBe(362);
    expect(tltRow!.singleDayOnly).toBe(false);
    expect(tltRow!.cells.find((cell) => cell.bp === 100)!.pct).toBeCloseTo(scenarioPct(16.5, 362, 100)!, 10);
    expect(tmfRow!.singleDayOnly).toBe(true);
    expect(tmfRow!.cells.find((cell) => cell.bp === -100)!.pct).toBeCloseTo(3 * scenarioPct(16.9, 362, -100)!, 10);
    expect(edvRow!.durationBasis).toBe('model');
    // Custom shocks and floor flag.
    const steep: ScenarioEtf = { ...tmf, model: { duration: 17, convexity: 300 } };
    const custom = scenarioTable([steep], [300]);
    expect(custom[0]!.cells).toEqual([{ bp: 300, pct: -100, floored: true }]);
    // 3 × (−16.9 × 2.5% + ½ × 362 × (2.5%)²) ≈ −92.8%: not floored.
    expect(scenarioTable([tmf], [250])[0]!.cells[0]!.floored).toBe(false);
  });
});

describe('oneYearHold', () => {
  it('adds carry, price change and cost for an unlevered fund', () => {
    const result = oneYearHold(tlt, { carryYield: 4.8, shortRate: 4.0, dyBp: 100 });
    expect(result.carry).toBeCloseTo(4.8, 10);
    expect(result.cost).toBeCloseTo(0.15, 10);
    expect(result.total).toBeCloseTo(4.8 + scenarioPct(16.5, 362, 100)! - 0.15, 10);
    expect(result.approximate).toBe(false);
  });

  it('uses L×yield − (L−1)×short rate − fee for a leveraged fund and marks it approximate', () => {
    const result = oneYearHold(tmf, { carryYield: 4.8, shortRate: 4.0, dyBp: 0 });
    expect(result.carry).toBeCloseTo(3 * 4.8 - 2 * 4.0, 10);
    expect(result.total).toBeCloseTo(3 * 4.8 - 2 * 4.0 - 1.06, 10);
    expect(result.approximate).toBe(true);
  });

  it('returns null pieces when inputs are missing', () => {
    expect(oneYearHold(tlt, { carryYield: null, shortRate: 4, dyBp: 0 }).total).toBeNull();
    expect(oneYearHold(tmf, { carryYield: 4.8, shortRate: null, dyBp: 0 }).total).toBeNull();
  });
});

describe('toScenarioEtf', () => {
  it('keeps unlevered durations and undoes an already-levered TMF duration', () => {
    const plain = toScenarioEtf({ symbol: 'TMF', leverage: 3, expenseRatio: 1.06, effectiveDuration: 16.8, model: { duration: 16.9, convexity: 362 } });
    expect(plain.effectiveDuration).toBe(16.8);
    expect(plain.model).toEqual({ duration: 16.9, convexity: 362 });
    const levered = toScenarioEtf({ symbol: 'TMF', leverage: 3, expenseRatio: 1.06, effectiveDuration: 50.4, model: { duration: 50.7, convexity: 1086 } });
    expect(levered.effectiveDuration).toBeCloseTo(16.8, 10);
    expect(levered.model!.duration).toBeCloseTo(16.9, 10);
    expect(levered.model!.convexity).toBeCloseTo(362, 10);
    // A bad leverage falls back to 1.
    expect(toScenarioEtf({ symbol: 'X', leverage: Number.NaN, expenseRatio: null, effectiveDuration: 10, model: null }).leverage).toBe(1);
  });
});

describe('formulaParts', () => {
  it('spells out the second-order formula for the UI example', () => {
    const parts = formulaParts(16.5, 360, 100, 1);
    expect(parts.dyPct).toBeCloseTo(1, 10);
    expect(parts.durationTerm).toBeCloseTo(-16.5, 10);
    expect(parts.convexityTerm).toBeCloseTo(1.8, 10);
    expect(parts.total).toBeCloseTo(-14.7, 10);
    expect(parts.publishedConvexity).toBeCloseTo(3.6, 10);
    const levered = formulaParts(16.5, 360, 300, 3);
    expect(levered.total).toBeCloseTo(-99.9, 10);
    expect(formulaParts(17, 300, 300, 3).total).toBe(-100);
    expect(formulaParts(17, 300, 300, 3).floored).toBe(true);
  });
});
