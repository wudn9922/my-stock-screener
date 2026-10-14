import { useMemo, useState } from 'react';
import { PERIOD_LABELS, type PeriodKey, type Theme, type ThemesData } from '../report/themes';
import { storageName } from '../app/HostingMode';
import type { OpenChartOptions } from '../app/Site';
import { formatDateTime, formatPercent, toneClass } from '../ui/format';
import HeatCanvas, { type HeatGroupView } from './HeatCanvas';
import { IndustryPanel } from './IndustryPanel';
import {
  FALLBACK_NOTICE,
  MIN_INDUSTRY_STOCKS,
  clampIndustryPeriod,
  colorValue,
  industryGroups,
  summarize,
  type IndustryModel,
  type IndustrySummary,
  type MapSource,
} from './industryModel';
import {
  BALANCED_FLOOR,
  DEFAULT_AGG_MODE,
  DEFAULT_WEIGHT_MODE,
  WEIGHT_MODES,
  buildMapModel,
  formatMoney,
  parentName,
  parseAggMode,
  parseWeightMode,
  themeGroups,
  themeTurnover,
  themesHaveTurnover,
  universeKey,
  type AggMode,
  type WeightMode,
} from './themesMapModel';
import { HEAT_CAP } from './themesView';

/**
 * Heatmap view of the 題材 page. Every cell is an industry: Taiwan one level (官方產業別), the US sector →
 * industry. Colour = the industry's return in the chosen period (market-cap weighted or equal-weight
 * median), size = the industry's total market cap. Until the full-market profile data exists the cells are
 * themes instead (colour = theme average, size = turnover of the members). Single stocks are never cells;
 * a click opens the industry's constituents (or the theme detail in the fallback).
 */

const PREFS_KEY = storageName('atlas-themes-map-v1');

interface MapPrefs {
  weight: WeightMode;
  agg: AggMode;
}

function readPrefs(): MapPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? 'null') as { weight?: unknown; agg?: unknown } | null;
    return { weight: parseWeightMode(raw?.weight), agg: parseAggMode(raw?.agg) };
  } catch {
    return { weight: DEFAULT_WEIGHT_MODE, agg: DEFAULT_AGG_MODE };
  }
}

function writePrefs(prefs: MapPrefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* A remembered mode is only a convenience. */
  }
}

const WEIGHT_LABELS = { balanced: '均衡', equal: '等大小' } as const;

function Legend({ period, cap }: { period: PeriodKey; cap: number }) {
  return (
    <div className="map-legend" aria-label={`顏色圖例：${PERIOD_LABELS[period]}報酬`} role="img">
      <span className="num">≤ -{cap}%</span>
      <span className="map-scale" aria-hidden="true" />
      <span className="num">≥ +{cap}%</span>
      <span className="legend-note">顏色＝{PERIOD_LABELS[period]}報酬，超過 ±{cap}% 以同一深度顯示</span>
    </div>
  );
}

function Toolbar({
  sizeLabel,
  sizeUsed,
  sizeEnabled,
  onSize,
  agg,
}: {
  sizeLabel: string;
  sizeUsed: WeightMode;
  sizeEnabled: boolean;
  onSize: (mode: WeightMode) => void;
  agg?: { value: AggMode; weightedLabel: string; weightedEnabled: boolean; onChange: (mode: AggMode) => void };
}) {
  const hints: Record<WeightMode, string> = {
    balanced: '大小取平方根壓縮並設最小面積，小產業也看得到、點得到',
    equal: '每個方塊一樣大',
    size: `大小與${sizeLabel}成正比，小的會很小`,
  };
  const labels: Record<WeightMode, string> = { ...WEIGHT_LABELS, size: sizeLabel };
  return (
    <>
      <div className="segmented compact" role="group" aria-label="大小">
        {WEIGHT_MODES.map((key) => (
          <button
            key={key}
            type="button"
            className={sizeUsed === key ? 'active' : ''}
            aria-pressed={sizeUsed === key}
            disabled={key !== 'equal' && !sizeEnabled}
            title={hints[key]}
            onClick={() => onSize(key)}
          >
            {labels[key]}
          </button>
        ))}
      </div>
      {agg && (
        <div className="segmented compact" role="group" aria-label="加權方式">
          <button
            type="button"
            className={agg.value === 'weighted' && agg.weightedEnabled ? 'active' : ''}
            aria-pressed={agg.value === 'weighted' && agg.weightedEnabled}
            disabled={!agg.weightedEnabled}
            title="個股報酬依權重平均，大公司影響較大"
            onClick={() => agg.onChange('weighted')}
          >
            {agg.weightedLabel}
          </button>
          <button
            type="button"
            className={agg.value === 'median' || !agg.weightedEnabled ? 'active' : ''}
            aria-pressed={agg.value === 'median' || !agg.weightedEnabled}
            title="每檔一票，取中位數，不受單一股票影響"
            onClick={() => agg.onChange('median')}
          >
            等權（中位數）
          </button>
        </div>
      )}
    </>
  );
}

const pct = (value: number | null) => (value === null ? '—' : `${Math.round(value * 100)}%`);

function IndustryTip({ label, groupLabel, summary, model, period, agg, fellBack, merged }: {
  label: string;
  groupLabel: string;
  summary: IndustrySummary;
  model: IndustryModel;
  period: PeriodKey;
  agg: AggMode;
  fellBack: boolean;
  merged: string[];
}) {
  const weightName = model.basis === 'turnover' ? '成交值加權' : '市值加權';
  const up = summary.upShare === null ? 0 : Math.round(summary.upShare * summary.validCount);
  const list = (items: IndustrySummary['best']) =>
    items.map((s) => (
      <span key={s.symbol} className="map-tip-stock">
        {s.name || s.symbol} <b className={`num ${toneClass(s.value)}`}>{formatPercent(s.value, 1)}</b>
      </span>
    ));
  return (
    <>
      <b>{label}</b>
      <span className="map-tip-theme">{groupLabel ? `${groupLabel} · ` : ''}{summary.count} 檔</span>
      <span>
        {PERIOD_LABELS[period]}報酬 {model.basis === 'none' ? null : `${weightName} `}
        {model.basis !== 'none' && <b className={`num ${toneClass(summary.weighted)}`}>{formatPercent(summary.weighted, 2)}</b>}
        {model.basis !== 'none' && ' · '}等權中位數 <b className={`num ${toneClass(summary.median)}`}>{formatPercent(summary.median, 2)}</b>
      </span>
      {fellBack && agg === 'weighted' && <span className="muted">無權重資料，顏色改用中位數</span>}
      <span>
        上漲 {pct(summary.upShare)}（{up}/{summary.validCount} 檔）
      </span>
      <span>
        市值合計 <b className="num">{formatMoney(summary.marketCap, model.market)}</b>
        {summary.capCount < summary.count ? `（${summary.capCount} 檔有市值）` : ''}
      </span>
      {summary.best.length > 0 && <span className="map-tip-row">強：{list(summary.best)}</span>}
      {summary.worst.length > 0 && <span className="map-tip-row">弱：{list(summary.worst)}</span>}
      {merged.length > 0 && <span className="muted">含：{merged.slice(0, 5).join('、')}{merged.length > 5 ? ` 等 ${merged.length} 個` : ''}</span>}
      <span className="muted">點一下查看成分股</span>
    </>
  );
}

function IndustryMap({
  model,
  period: requested,
  prefs,
  setPrefs,
  openChart,
}: {
  model: IndustryModel;
  period: PeriodKey;
  prefs: MapPrefs;
  setPrefs: (next: MapPrefs) => void;
  openChart: (options: OpenChartOptions) => void;
}) {
  const period = clampIndustryPeriod(requested);
  const [openId, setOpenId] = useState<string | null>(null);
  const sizeLabel = model.basis === 'turnover' ? '成交值' : '市值';
  const weightedEnabled = model.basis !== 'none';
  const agg: AggMode = weightedEnabled ? prefs.agg : 'median';
  const mapModel = useMemo(() => buildMapModel(industryGroups(model), prefs.weight), [model, prefs.weight]);
  const summaries = useMemo(() => {
    const cells = new Map<string, IndustrySummary>();
    const groups = new Map<string, IndustrySummary>();
    for (const group of model.groups) {
      groups.set(group.id, summarize(group.stocks, period, model.basis));
      for (const cell of group.cells) cells.set(cell.id, summarize(cell.stocks, period, model.basis));
    }
    return { cells, groups };
  }, [model, period]);
  const cellById = useMemo(() => new Map(model.groups.flatMap((g) => g.cells.map((c) => [c.id, { cell: c, group: g }] as const))), [model]);

  const views = useMemo(
    (): HeatGroupView[] =>
      mapModel.groups.map((group) => {
        const sector = model.twoLevel ? group.label : '';
        return {
          id: group.id,
          label: group.label,
          value: model.twoLevel ? colorValue(summaries.groups.get(group.id)!, agg).value : undefined,
          cells: group.cells.map((mapCell) => {
            const entry = cellById.get(mapCell.id)!;
            const summary = summaries.cells.get(mapCell.id)!;
            const color = colorValue(summary, agg);
            return {
              id: mapCell.id,
              label: mapCell.label,
              value: color.value,
              aria: `${sector ? `${sector} ` : ''}${mapCell.label}，${summary.count} 檔，${PERIOD_LABELS[period]}報酬 ${formatPercent(color.value, 1)}，開啟成分股`,
              tip: (
                <IndustryTip
                  label={mapCell.label}
                  groupLabel={sector}
                  summary={summary}
                  model={model}
                  period={period}
                  agg={agg}
                  fellBack={color.fellBack}
                  merged={entry.cell.mergedFrom}
                />
              ),
              onOpen: () => setOpenId(mapCell.id),
            };
          }),
        };
      }),
    [mapModel, model, summaries, cellById, agg, period],
  );

  const open = openId ? cellById.get(openId) : undefined;
  const { coverage } = model;
  const weightText = agg === 'median' ? '等權中位數（每檔一票）' : model.basis === 'turnover' ? '成交值加權平均' : '市值加權平均';
  const sizeText = model.basis === 'none' || mapModel.mode === 'equal' ? mapModel.mode === 'equal' && model.basis !== 'none' ? '每個方塊一樣大（等大小）' : '每個方塊一樣大（沒有市值或成交值資料）' : `產業${sizeLabel}合計（${model.market === 'TW' ? '新台幣' : '美元'}）`;
  const modeText =
    mapModel.mode === 'equal'
      ? ''
      : mapModel.mode === 'balanced'
        ? `；均衡模式取平方根壓縮，每塊至少為中位數方塊的 ${Math.round(BALANCED_FLOOR * 100)}%，小產業才不會縮到看不見`
        : `；小產業會很小，可切回「均衡」`;
  return (
    <section className="themes-map" aria-label="產業熱力圖">
      <div className="map-toolbar">
        <Toolbar
          sizeLabel={sizeLabel}
          sizeUsed={mapModel.mode}
          sizeEnabled={model.basis !== 'none'}
          onSize={(weight) => setPrefs({ ...prefs, weight })}
          agg={{
            value: agg,
            weightedLabel: model.basis === 'turnover' ? '成交值加權' : '市值加權',
            weightedEnabled,
            onChange: (next) => setPrefs({ ...prefs, agg: next }),
          }}
        />
        <Legend period={period} cap={HEAT_CAP[period]} />
      </div>
      {model.basis === 'turnover' && (
        <p className="map-note warn" role="status">
          市值資料不足，大小與加權改用近20日平均成交值
        </p>
      )}
      <HeatCanvas
        model={mapModel}
        groups={views}
        flat={!model.twoLevel}
        cap={HEAT_CAP[period]}
        ariaLabel={`產業熱力圖，${mapModel.cellCount} 個產業、${coverage.total} 檔股票`}
        empty="沒有可顯示的產業。"
      />
      <p className="map-foot">
        資料日 {model.asOf ? formatDateTime(model.asOf, false) : '—'} · {model.universe}
        {model.scope === 'full' ? '（全市場）' : '（流動性篩選名單，非全市場）'}，涵蓋 {coverage.total.toLocaleString('en-US')} 檔，其中{' '}
        {coverage.classified.toLocaleString('en-US')} 檔有產業分類。顏色＝{PERIOD_LABELS[period]}報酬，以{weightText}計算。大小＝{sizeText}
        {modeText}。
        {model.basis === 'cap' && coverage.withCap < coverage.total ? ` ${coverage.total - coverage.withCap} 檔缺市值，計入大小與加權時視為 0。` : ''}
        {mapModel.imputed > 0 ? ` ${mapModel.imputed} 個產業沒有大小資料，以中位數代入。` : ''} 成分股少於 {MIN_INDUSTRY_STOCKS} 檔的產業與未分類股票併入「其他」
        {model.twoLevel ? '（同一 sector 內）' : ''}。方塊太小時不顯示文字，滑過或長按可看詳情。
      </p>
      {open && (
        <IndustryPanel
          key={open.cell.id}
          cell={open.cell}
          groupLabel={model.twoLevel ? open.group.label : ''}
          model={model}
          initialPeriod={period}
          onClose={() => setOpenId(null)}
          openChart={openChart}
        />
      )}
    </section>
  );
}

function ThemeMap({
  data,
  themes,
  period,
  source,
  prefs,
  setPrefs,
  onOpenTheme,
}: {
  data: ThemesData;
  themes: Theme[];
  period: PeriodKey;
  source: Extract<MapSource, { kind: 'themes' }>;
  prefs: MapPrefs;
  setPrefs: (next: MapPrefs) => void;
  onOpenTheme: (key: string) => void;
}) {
  const market = data.market;
  const mkey = universeKey(market);
  const inputs = useMemo(() => themeGroups(data, themes, source.lookup), [data, themes, source.lookup]);
  const hasSize = inputs.some((group) => group.cells.some((cell) => cell.size !== null));
  const mapModel = useMemo(() => buildMapModel(inputs, prefs.weight), [inputs, prefs.weight]);
  const themeByKey = useMemo(() => new Map(themes.map((t) => [t.key, t])), [themes]);
  const views = useMemo(
    (): HeatGroupView[] =>
      mapModel.groups.map((group) => ({
        id: group.id,
        label: group.label,
        cells: group.cells.map((mapCell) => {
          const theme = themeByKey.get(mapCell.id)!;
          const value = theme.returns[period];
          const turnover = themeTurnover(theme, source.lookup);
          return {
            id: mapCell.id,
            label: mapCell.label,
            value,
            aria: `${theme.name}，${PERIOD_LABELS[period]}題材報酬 ${formatPercent(value, 1)}，開啟題材詳情`,
            tip: (
              <>
                <b>{theme.name}</b>
                <span className="map-tip-theme">{parentName(data, theme) || '題材'} · {theme.constituents.length} 檔</span>
                <span>
                  {PERIOD_LABELS[period]}題材報酬（成分股等權平均）{' '}
                  <b className={`num ${toneClass(value)}`}>{formatPercent(value, 2)}</b>
                </span>
                <span>
                  成分股近20日成交值合計 <b className="num">{formatMoney(turnover.total, mkey)}</b>
                  {turnover.total !== null && turnover.known < theme.constituents.length ? `（${turnover.known}/${theme.constituents.length} 檔有資料）` : ''}
                </span>
                <span className="muted">點一下開啟題材詳情</span>
              </>
            ),
            onOpen: () => onOpenTheme(theme.key),
          };
        }),
      })),
    [mapModel, themeByKey, period, source.lookup, data, mkey, onOpenTheme],
  );
  const asOf = themesHaveTurnover(themes) ? data.asOf : (source.lookupAsOf ?? data.asOf);
  const currency = market === 'tw' ? '新台幣' : '美元';
  return (
    <section className="themes-map" aria-label="題材熱力圖">
      <div className="map-toolbar">
        <Toolbar sizeLabel="成交值" sizeUsed={mapModel.mode} sizeEnabled={hasSize} onSize={(weight) => setPrefs({ ...prefs, weight })} />
        <Legend period={period} cap={HEAT_CAP[period]} />
      </div>
      <p className="map-note warn" role="status" data-testid="map-fallback">
        {FALLBACK_NOTICE}
        {source.reason === 'failed' ? '（全市場資料載入失敗）' : ''}
      </p>
      <HeatCanvas
        model={mapModel}
        groups={views}
        flat={false}
        cap={HEAT_CAP[period]}
        ariaLabel={`題材熱力圖，${mapModel.cellCount} 個題材`}
        empty="沒有可顯示的題材。"
      />
      <p className="map-foot">
        資料日 {formatDateTime(data.asOf, false)} · 每個方塊是一個題材，不是個股；顏色＝{PERIOD_LABELS[period]}題材報酬（成分股等權平均）。
        {mapModel.mode === 'equal'
          ? hasSize
            ? '大小：每個題材一樣大（等大小）。'
            : '大小：每個題材一樣大（暫無成交值資料）。'
          : `大小＝題材成分股近20日平均成交值合計（${currency}${asOf ? `，資料日 ${formatDateTime(asOf, false)}` : ''}），不是市值${
              mapModel.mode === 'balanced' ? `；均衡模式取平方根壓縮，每塊至少為中位數方塊的 ${Math.round(BALANCED_FLOOR * 100)}%` : '；小題材會很小，可切回「均衡」'
            }。`}
        {mapModel.imputed > 0 ? ` ${mapModel.imputed} 個題材沒有成交值資料，以中位數代入。` : ''} 方塊太小時不顯示文字，滑過或長按可看詳情。
      </p>
    </section>
  );
}

export default function ThemesMap({
  data,
  themes,
  source,
  period,
  onOpenTheme,
  openChart,
}: {
  data: ThemesData;
  themes: Theme[];
  source: MapSource;
  period: PeriodKey;
  onOpenTheme: (key: string) => void;
  openChart: (options: OpenChartOptions) => void;
}) {
  const [prefs, setPrefsState] = useState<MapPrefs>(readPrefs);
  const setPrefs = (next: MapPrefs) => {
    setPrefsState(next);
    writePrefs(next);
  };
  if (source.kind === 'loading')
    return (
      <section className="themes-map" aria-label="產業熱力圖">
        <div className="map-loading" role="status">
          載入全市場產業資料…
        </div>
      </section>
    );
  if (source.kind === 'industry') return <IndustryMap model={source.model} period={period} prefs={prefs} setPrefs={setPrefs} openChart={openChart} />;
  return <ThemeMap data={data} themes={themes} period={period} source={source} prefs={prefs} setPrefs={setPrefs} onOpenTheme={onOpenTheme} />;
}
