import type { GroupItem } from '../report/schema';
import type { SavedWatchlist, WatchlistVisit } from './model';
import { activeChips, applyFilters, epsAccelStatus, epsStreak, filterAvailability, gateTally, growthRecordFor, maDeviation, parseFilters, removeFilter, screenerMa, type FilterChip, type FilterContext, type FilterState } from '../pages/screenerFilters';
import { screenerPeValue } from '../fundamentals/peFigures';
import { formatMarketVolume, formatPercent } from '../ui/format';

export interface ChangeReason {
  title: string;
  conditions: { label: string; value: string; passed: boolean | null }[];
}
export interface WatchlistChangeReasons {
  entered: Record<string, ChangeReason>;
  exited: Record<string, ChangeReason>;
}
const number = (value: number | null | undefined, unit = '') => value == null || !Number.isFinite(value) ? '未提供' : `${value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })}${unit}`;

/** Describe current evidence only. Prior snapshots contain membership, not prior indicator values. */
function actualValue(chip: FilterChip, item: GroupItem, state: FilterState, context: FilterContext): string {
  const metrics = item.metrics;
  if (chip.id === 'ema20') {
    const reference = screenerMa(item, context.fixedMAs);
    return `${reference.label} 乖離 ${reference.distance === null ? '未提供' : formatPercent(reference.distance)}`;
  }
  if (chip.id.startsWith('tr:')) {
    const flag = chip.id.slice(3);
    if (flag === 'stack') return [20, 50, 200].map(period => `MA${period} 乖離 ${formatPercent(maDeviation(item, period as 20 | 50 | 200))}`).join('、');
    if (flag === 'slope') return `MA20 斜率 ${number(metrics?.ma20Slope5, '%')}`;
    if (flag === 'nobear') return `趨勢：${metrics?.trend === 'down' ? '空頭' : metrics?.trend === 'up' ? '多頭' : metrics?.trend ? '盤整' : '未提供；此條件僅排除已知空頭'}`;
    const period = flag === 'a20' ? 20 : flag === 'a50' ? 50 : 200;
    return `MA${period} 乖離 ${formatPercent(maDeviation(item, period))}`;
  }
  if (chip.id === 'hi') return `距 52 週高 ${number(metrics?.fromHi52, '%')}`;
  if (chip.id === 'rs') return `RS 百分位 ${number(metrics?.rsRank)}`;
  if (chip.id === 'rs3') return `3 月相對大盤 ${number(metrics?.rs63, '%')}`;
  if (chip.id === 'rs1') return `1 月相對大盤 ${number(metrics?.rs21, '%')}`;
  if (chip.id === 'vr') return `較前一交易日成交量 ${number(metrics?.volVsPrev, ' 倍')}`;
  if (chip.id === 'vol') return `20 日平均成交量 ${formatMarketVolume(metrics?.volumeAvg20, context.market)}`;
  if (chip.id === 'pe') {
    const figures = context.pe?.[item.symbol];
    return figures?.peTtmLoss ? '近四季虧損，本益比不適用' : `本益比 ${number(screenerPeValue(figures), ' 倍')}`;
  }
  const record = growthRecordFor(item, context.growth);
  if (chip.id === 'epss') {
    const streak = epsStreak(record, state.turn);
    return `EPS 已連續成長 ${streak.years} 年${streak.stop === 'missing' ? state.keepMissing ? '；後續缺值，依設定保留' : '；後續資料不足' : ''}`;
  }
  if (chip.id === 'accel' || chip.id === 'accelY') {
    const annual = chip.id === 'accelY';
    const status = epsAccelStatus(record, state.turn, annual ? 'annual' : 'quarterly');
    const values = annual ? record?.epsYoYAnnual : record?.epsYoY;
    return status === 'unknown' ? `資料不足${state.keepMissing ? '，依保留缺值設定處理' : ''}` : `本期／前期 EPS 年增 ${number(values?.[0], '%')}／${number(values?.[1], '%')}${(annual ? record?.epsTurnAnnual : record?.epsTurnQ)?.[0] ? '；本期虧轉盈' : ''}`;
  }
  const extra = chip.id.startsWith('growth:') ? state.extraGates?.[Number(chip.id.slice(7))] : undefined;
  const kind = extra?.kind ?? chip.key;
  const gate = extra?.gate ?? (kind === 'eps' ? state.eps : kind === 'epsY' ? state.epsY : kind === 'rev' ? state.rev : kind === 'revY' ? state.revY : null);
  if (gate) {
    const periods = 'quarters' in gate ? gate.quarters : gate.years;
    const values = kind === 'eps' ? record?.epsYoY : kind === 'epsY' ? record?.epsYoYAnnual : kind === 'rev' ? record?.revYoY : record?.revYoYAnnual;
    const turns = kind === 'eps' ? record?.epsTurnQ : kind === 'epsY' ? record?.epsTurnAnnual : [];
    const tally = gateTally(values, periods, gate.min, turns, state.turn);
    return `達標 ${tally.growth}／${tally.measured} 個有資料期間${tally.missing ? `，另缺 ${tally.missing} 期` : ''}${tally.measured < Math.ceil(periods / 2) && state.keepMissing ? '；資料不足，依設定保留' : ''}`;
  }
  return '依本次資料及目前條件判定';
}

function isolate(state: FilterState, chips: FilterChip[], target: FilterChip): FilterState {
  // Remove repeated gates from the end so their original indices remain valid.
  const other = chips.filter(chip => chip.id !== target.id).sort((a, b) =>
    (b.id.startsWith('growth:') ? Number(b.id.slice(7)) + 1 : 0) - (a.id.startsWith('growth:') ? Number(a.id.slice(7)) + 1 : 0));
  const single = other.reduce((next, chip) => removeFilter(next, chip.id), state);
  return { ...single, turn: state.turn, keepMissing: state.keepMissing };
}

/** Reuse the real filter evaluator over the same complete source; do not invent a second predicate set. */
export function explainWatchlistChanges(visit: WatchlistVisit, list: SavedWatchlist, items: readonly GroupItem[], context: FilterContext): WatchlistChangeReasons {
  const state = parseFilters(list.filters);
  const available = filterAvailability(items, context);
  const chips = activeChips(state, available, list.market, !!context.fixedMAs && Object.keys(context.fixedMAs).length > 0).filter(chip => chip.id !== 'turn');
  const passing = new Map(chips.map(chip => [chip.id, chip.unavailable ? null : new Set(applyFilters(items, isolate(state, chips, chip), context).map(item => item.symbol))]));
  const bySymbol = new Map(items.map(item => [item.symbol, item]));
  const explain = (symbol: string, entered: boolean): ChangeReason => {
    if (list.kind === 'manual' && !list.symbols.includes(symbol)) return { title: '目前未列入手動清單', conditions: [] };
    const item = bySymbol.get(symbol);
    if (!item) return { title: '已不在目前來源名單，無法判定原始指標原因', conditions: [] };
    const conditions = chips.map(chip => ({ label: chip.label, value: chip.unavailable ?? actualValue(chip, item, state, context), passed: passing.get(chip.id)?.has(symbol) ?? null }));
    const failed = conditions.filter(condition => condition.passed === false);
    const unknown = conditions.some(condition => condition.passed === null);
    return {
      title: failed.length ? `未通過：${failed.map(condition => condition.label).join('、')}`
        : unknown ? '部分條件資料不足，無法完整說明'
          : entered ? chips.length ? `本次符合 ${chips.length} 項條件` : list.kind === 'manual' ? '本次在手動清單內' : '本次在來源名單內，未設定額外條件'
            : '目前條件通過；名單差異需確認來源或設定變動',
      conditions,
    };
  };
  return { entered: Object.fromEntries(visit.entered.map(item => [item.symbol, explain(item.symbol, true)])), exited: Object.fromEntries(visit.exited.map(item => [item.symbol, explain(item.symbol, false)])) };
}
