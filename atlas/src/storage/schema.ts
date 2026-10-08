import { z } from 'zod';
import { CANONICAL_SYMBOL_REGEX } from '../market-data/MarketProfile';

const symbol = z.string().regex(CANONICAL_SYMBOL_REGEX);
const finite = z.number().finite();
export const timeframes = ['5m', '15m', '30m', '1H', '4H', '1D', '1W', '1M'] as const;
export const timeframeSchema = z.enum(timeframes);
export type Timeframe = z.infer<typeof timeframeSchema>;
const tf = timeframeSchema;
const color = z.string().regex(/^#[0-9a-fA-F]{6}$/);
const widthMode = z.enum(['pixels', 'atr']);
export type StrokeWidthMode = z.infer<typeof widthMode>;
const tool = z.enum(['trend', 'horizontal', 'ray', 'rectangle', 'fibonacci', 'channel', 'price-range', 'date-range', 'price-date-range', 'vertical']);
const drawingStyleFields = {
  color,
  lineWidth: finite.min(1).max(4),
  lineStyle: z.enum(['solid', 'dashed', 'dotted']).optional(),
  opacity: finite.min(0).max(1).optional(),
  fillOpacity: finite.min(0).max(0.3).optional(),
  labelsVisible: z.boolean().optional(),
  hiddenLevels: z.array(finite.min(0).max(1)).max(32).optional(),
};
const v3DrawingStyleSchema = z.object(drawingStyleFields);
export const drawingStyleSchema = z.object({ ...drawingStyleFields, widthMode: widthMode.optional() });
export const anchorSchema = z.object({
  time: finite.min(1).max(32503680000),
  logical: finite,
  price: finite,
  timeframe: tf,
});
const drawingFields = {
  id: z.string().min(1).max(100),
  symbol,
  type: tool,
  points: z.array(anchorSchema).min(1).max(3),
  levels: z.array(finite.min(0).max(1)).min(2).max(32).optional(),
  locked: z.boolean(),
  visible: z.boolean(),
  style: drawingStyleSchema,
};
type DrawingRuleShape = {
  type: string;
  points: { price: number }[];
  levels?: number[];
  style: { hiddenLevels?: number[] };
};
const drawingRules = <T extends z.ZodType>(schema: T) => schema
  .refine(value => {
    const d = value as DrawingRuleShape;
    return d.points.length === (d.type === 'horizontal' || d.type === 'vertical' ? 1 : d.type === 'channel' ? 3 : 2);
  }, 'Invalid control point count')
  .refine(value => {
    const d = value as DrawingRuleShape;
    return d.type !== 'ray' || d.points[0].price === d.points[1].price;
  }, 'Ray must be horizontal')
  .refine(value => {
    const d = value as DrawingRuleShape;
    return d.type === 'fibonacci' ? !!d.levels && new Set(d.levels).size === d.levels.length : !d.levels;
  }, 'Invalid Fibonacci levels')
  .refine(value => {
    const d = value as DrawingRuleShape;
    return !d.style.hiddenLevels?.length || d.type === 'fibonacci' && d.style.hiddenLevels.every(level => d.levels?.includes(level));
  }, 'Hidden levels must belong to Fibonacci levels');

// V3 records have one timeframe owner. Legacy readers use legacyDrawingSchema, then migrate
// global or multi-timeframe records before validating them against this schema.
export const drawingSchema = drawingRules(z.object({
  ...drawingFields,
  scope: z.object({ timeframes: z.array(tf).length(1) }),
}));
const v3DrawingSchema = drawingRules(z.object({
  ...drawingFields,
  style: v3DrawingStyleSchema,
  scope: z.object({ timeframes: z.array(tf).length(1) }),
}));
const legacyDrawingSchema = drawingRules(z.object({
  ...drawingFields,
  style: v3DrawingStyleSchema,
  scope: z.object({ timeframes: z.union([z.literal('all'), z.array(tf)]) }),
}));
const v3IndicatorSchema = z.object({
  id: z.string().min(1).max(100),
  symbol,
  type: z.enum(['SMA', 'EMA', 'Volume']),
  period: z.number().int().min(1).max(5000),
  source: z.enum(['open', 'high', 'low', 'close']),
  visible: z.boolean(),
  locked: z.boolean(),
  lineWidth: z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(4)]),
  color,
  scope: z.object({ timeframe: tf }),
});
const indicatorWithWidthModeSchema = v3IndicatorSchema.extend({ widthMode: widthMode.optional() });
function validateIndicatorWidthMode(indicator: { type: 'SMA' | 'EMA' | 'Volume'; widthMode?: StrokeWidthMode }, c: z.RefinementCtx) {
    if (indicator.type === 'Volume' && indicator.widthMode === 'atr') {
      c.addIssue({ code: 'custom', message: 'Volume indicators cannot use ATR width mode' });
    }
}
export const indicatorSchema = indicatorWithWidthModeSchema.superRefine(validateIndicatorWidthMode);
const legacyIndicatorSchema = v3IndicatorSchema.extend({ scope: z.object({ timeframe: tf.optional() }) });
const range = z.object({ from: finite, to: finite, barCount: z.number().int().positive().optional() })
  .refine(v => v.to > v.from && v.to - v.from <= 100000);
const ownershipMigrationSchema = z.object({
  fromVersion: z.union([z.literal(1), z.literal(2)]),
  indicatorHome: tf,
  drawingRule: z.literal('first-anchor'),
});
export const preferencesSchema = z.object({
  timeframe: tf,
  views: z.record(z.string(), range),
  magnet: z.boolean(),
  legacyVolume: z.boolean().optional(),
  volumeOverrides: z.partialRecord(tf, z.boolean()).optional(),
  ownershipMigration: ownershipMigrationSchema.optional(),
});
export function legacyVolumeEnabled(
  preferences: Pick<z.infer<typeof preferencesSchema>, 'legacyVolume' | 'volumeOverrides'>,
  timeframe: Timeframe,
) {
  return preferences.volumeOverrides?.[timeframe] ?? preferences.legacyVolume !== false;
}

function stateRelations<T extends { symbol: string; drawings: { symbol: string; id: string }[]; indicators: { symbol: string; id: string }[] }>(s: T, c: z.RefinementCtx) {
  for (const list of [s.drawings, s.indicators]) {
    const ids = new Set<string>();
    for (const item of list) {
      if (item.symbol !== s.symbol || ids.has(item.id)) c.addIssue({ code: 'custom', message: 'Symbol mismatch or duplicate id' });
      ids.add(item.id);
    }
  }
}
export const symbolStateSchema = z.object({
  symbol,
  drawings: z.array(drawingSchema).max(10000),
  indicators: z.array(indicatorSchema).max(800),
  preferences: preferencesSchema,
}).superRefine((s, c) => {
  stateRelations(s, c);
  const counts = new Map<Timeframe, number>();
  for (const indicator of s.indicators) {
    const timeframe = indicator.scope.timeframe;
    const count = (counts.get(timeframe) ?? 0) + 1;
    counts.set(timeframe, count);
    if (count > 100) c.addIssue({ code: 'custom', message: `More than 100 indicators in ${timeframe}` });
  }
});
const v3SymbolStateSchema = z.object({
  symbol,
  drawings: z.array(v3DrawingSchema).max(10000),
  indicators: z.array(v3IndicatorSchema).max(800),
  preferences: preferencesSchema,
}).superRefine((s, c) => {
  stateRelations(s, c);
  const counts = new Map<Timeframe, number>();
  for (const indicator of s.indicators) {
    const timeframe = indicator.scope.timeframe;
    const count = (counts.get(timeframe) ?? 0) + 1;
    counts.set(timeframe, count);
    if (count > 100) c.addIssue({ code: 'custom', message: `More than 100 indicators in ${timeframe}` });
  }
});
const legacySymbolStateSchema = z.object({
  symbol,
  drawings: z.array(legacyDrawingSchema).max(10000),
  indicators: z.array(legacyIndicatorSchema).max(100),
  preferences: preferencesSchema,
}).superRefine(stateRelations);

export const alertSchema = z.object({
  id: z.string().min(1).max(100),
  symbol,
  timeframe: tf,
  kind: z.enum(['level', 'ma', 'drawing']),
  direction: z.enum(['above', 'below', 'cross']),
  level: finite.optional(),
  maType: z.enum(['SMA', 'EMA']).optional(),
  period: z.number().int().min(1).max(5000).optional(),
  drawingId: z.string().min(1).max(100).optional(),
  enabled: z.boolean(),
  invalidReason: z.string().max(300).nullable().default(null),
  lastTriggered: finite.nullable().default(null),
  lastEvaluated: finite.nullable().default(null),
  baseline: finite.nullable().default(null),
  referencePrice: finite.nullable().default(null),
}).refine(a => a.kind === 'level' ? a.level !== undefined : a.kind === 'ma' ? !!a.maType && !!a.period : !!a.drawingId, 'Missing alert condition');
export type AlertDefinition = z.infer<typeof alertSchema>;
const presetFields = {
  id: z.string().min(1).max(100),
  name: z.string().trim().min(1).max(80),
};
const presetSchema = z.object({
  ...presetFields,
  indicators: z.array(indicatorWithWidthModeSchema.omit({ id: true, symbol: true }).superRefine(validateIndicatorWidthMode)).max(100),
});
const v3PresetSchema = z.object({
  ...presetFields,
  indicators: z.array(v3IndicatorSchema.omit({ id: true, symbol: true })).max(100),
});
const legacyPresetSchema = z.object({
  ...presetFields,
  indicators: z.array(legacyIndicatorSchema.omit({ id: true, symbol: true })).max(100),
});
const workspaceSchema = z.object({
  rightOpen: z.boolean().default(true),
  rightTab: z.enum(['indicators', 'drawings', 'financials', 'backtest', 'alerts', 'settings']).default('indicators'),
  debug: z.boolean().default(false),
});
const appFields = {
  watchlist: z.array(symbol).max(1000),
  activeSymbol: symbol,
  provider: z.enum(['demo', 'yahoo', 'snapshot']),
  recentSymbols: z.array(symbol).max(20).default([]),
  drawingDefaults: z.partialRecord(tool, drawingStyleSchema).default({}),
  alerts: z.array(alertSchema).max(1000).default([]),
  workspace: workspaceSchema.default({ rightOpen: true, rightTab: 'indicators', debug: false }),
};
const v3AppFields = {
  ...appFields,
  drawingDefaults: z.partialRecord(tool, v3DrawingStyleSchema).default({}),
};
function appRelations(a: { watchlist: string[]; alerts: { id: string }[]; indicatorPresets: { id: string }[] }, c: z.RefinementCtx) {
  if (new Set(a.watchlist).size !== a.watchlist.length) c.addIssue({ code: 'custom', message: 'Duplicate watchlist symbol' });
  for (const list of [a.alerts, a.indicatorPresets]) {
    if (new Set(list.map(x => x.id)).size !== list.length) c.addIssue({ code: 'custom', message: 'Duplicate workspace id' });
  }
}
export const appSchema = z.object({
  ...appFields,
  indicatorPresets: z.array(presetSchema).max(100).default([]),
}).superRefine(appRelations);
const v3AppSchema = z.object({
  ...v3AppFields,
  indicatorPresets: z.array(v3PresetSchema).max(100).default([]),
}).superRefine(appRelations);
const legacyAppSchema = z.object({
  ...v3AppFields,
  indicatorPresets: z.array(legacyPresetSchema).max(100).default([]),
}).superRefine(appRelations);
export type SymbolState = z.infer<typeof symbolStateSchema>;
export type AppSettings = z.infer<typeof appSchema>;
export type SettingsExport = z.infer<typeof exportSchema>;
type LegacySymbolState = z.infer<typeof legacySymbolStateSchema>;
type LegacyAppSettings = z.infer<typeof legacyAppSchema>;

export function emptySymbol(symbolName: string): SymbolState {
  return { symbol: symbolName, drawings: [], indicators: [], preferences: { timeframe: '1D', views: {}, magnet: false } };
}

const envelope = { exportedAt: z.string(), app: appSchema, symbols: z.array(symbolStateSchema).max(1000) };
const v3Envelope = { exportedAt: z.string(), app: v3AppSchema, symbols: z.array(v3SymbolStateSchema).max(1000) };
function validDrawingAlert(d: SymbolState['drawings'][number] | undefined, timeframe: Timeframe) {
  return !!d && (d.type === 'horizontal' || d.type === 'ray') && d.scope.timeframes.includes(timeframe);
}
function checkExportRelations(d: { app: AppSettings; symbols: SymbolState[] }, c: z.RefinementCtx) {
  if (new Set(d.symbols.map(s => s.symbol)).size !== d.symbols.length) c.addIssue({ code: 'custom', message: 'Duplicate symbol state' });
  for (const alert of d.app.alerts) {
    if (alert.kind !== 'drawing' || !alert.enabled) continue;
    const drawing = d.symbols.find(s => s.symbol === alert.symbol)?.drawings.find(x => x.id === alert.drawingId);
    if (!validDrawingAlert(drawing, alert.timeframe)) c.addIssue({ code: 'custom', message: 'Orphan or out-of-scope drawing alert' });
  }
}
const v3ExportEnvelope = z.object({ version: z.literal(3), ...v3Envelope });
export const exportSchemaV3 = v3ExportEnvelope.superRefine(checkExportRelations);
const exportEnvelope = z.object({ version: z.literal(4), ...envelope });
export const exportSchema = exportEnvelope.superRefine(checkExportRelations);

const legacyEnvelopeFields = {
  exportedAt: z.string(),
  app: legacyAppSchema,
  symbols: z.array(legacySymbolStateSchema).max(1000),
};
const legacyV1Schema = z.object({ version: z.literal(1), ...legacyEnvelopeFields });
const legacyV2Schema = z.object({ version: z.literal(2), ...legacyEnvelopeFields });

function legacyDrawingInTimeframe(drawing: LegacySymbolState['drawings'][number], timeframe: Timeframe) {
  return drawing.scope.timeframes === 'all' || drawing.scope.timeframes.includes(timeframe);
}
function migrateSymbolState(state: LegacySymbolState, version: 1 | 2): SymbolState {
  const { preferences } = state;
  const volumeOverrides = preferences.legacyVolume === false
    ? Object.fromEntries(timeframes.map(timeframe => [timeframe, false])) as Record<Timeframe, boolean>
    : preferences.volumeOverrides;
  return symbolStateSchema.parse({
    ...state,
    drawings: state.drawings.map(drawing => {
      const scopes = drawing.scope.timeframes;
      if (Array.isArray(scopes) && scopes.length === 1) return drawing;
      return { ...drawing, scope: { timeframes: [drawing.points[0].timeframe] } };
    }),
    indicators: state.indicators.map(indicator => ({
      ...indicator,
      scope: { timeframe: indicator.scope.timeframe ?? preferences.timeframe },
    })),
    preferences: {
      ...preferences,
      ...(volumeOverrides ? { volumeOverrides } : {}),
      ownershipMigration: {
        fromVersion: version,
        indicatorHome: preferences.timeframe,
        drawingRule: 'first-anchor',
      },
    },
  });
}
function migrateLegacyApp(app: LegacyAppSettings, symbols: SymbolState[]): AppSettings {
  const indicatorHome = symbols.find(state => state.symbol === app.activeSymbol)?.preferences.timeframe ?? '1D';
  return appSchema.parse({
    ...app,
    indicatorPresets: app.indicatorPresets.map(preset => ({
      ...preset,
      indicators: preset.indicators.map(indicator => ({
        ...indicator,
        scope: { timeframe: indicator.scope.timeframe ?? indicatorHome },
      })),
    })),
  });
}
function migrateLegacyAlerts(app: AppSettings, symbols: SymbolState[]): AppSettings {
  const alerts = app.alerts.map(alert => {
    if (alert.kind !== 'drawing' || !alert.enabled) return alert;
    const referenced = symbols.find(state => state.symbol === alert.symbol)?.drawings.find(d => d.id === alert.drawingId);
    if (!referenced || (referenced.type !== 'horizontal' && referenced.type !== 'ray')) {
      return { ...alert, enabled: false, invalidReason: 'Referenced drawing was deleted', baseline: null };
    }
    if (!legacyDrawingInTimeframe(referenced, alert.timeframe)) {
      return { ...alert, enabled: false, invalidReason: 'Referenced drawing is scoped to another timeframe', baseline: null };
    }
    return alert;
  });
  return appSchema.parse({ ...app, alerts });
}
function upgradeLegacyEnvelope(parsed: { exportedAt: string; app: LegacyAppSettings; symbols: LegacySymbolState[] }, version: 1 | 2): SettingsExport {
  const symbols = parsed.symbols.map(state => migrateSymbolState(state, version));
  const app = migrateLegacyAlerts(migrateLegacyApp(parsed.app, symbols), symbols);
  return exportSchema.parse({ version: 4, exportedAt: parsed.exportedAt, app, symbols });
}

export function upgradeLegacySettings(data: unknown, version: 1 | 2): SettingsExport {
  const record = z.record(z.string(), z.unknown()).parse(data);
  const parsed = (version === 1 ? legacyV1Schema : legacyV2Schema).parse({ ...record, version });
  return upgradeLegacyEnvelope(parsed, version);
}

export function upgradeV3Settings(data: unknown): SettingsExport {
  const parsed = exportSchemaV3.parse(data);
  return exportSchema.parse({ ...parsed, version: 4 });
}

export function parseSettings(json: string): SettingsExport {
  if (json.length > 10_000_000) throw new Error('Import exceeds 10 MB');
  const data: unknown = JSON.parse(json);
  const version = z.object({ version: z.number().int() }).parse(data).version;
  if (version === 1 || version === 2) return upgradeLegacySettings(data, version);
  if (version === 3) return upgradeV3Settings(data);
  return exportSchema.parse(data);
}
