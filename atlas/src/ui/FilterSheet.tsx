import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useDialogFocus } from './useDialogFocus';
import { formatDateTime } from './format';
import type { GrowthFile } from '../fundamentals/GrowthProvider';
import {
  EMPTY_FILTERS,
  GROWTH_PRESETS,
  MAX_GATE_QUARTERS,
  MAX_GATE_YEARS,
  TREND_FLAGS,
  TREND_LABELS,
  VOLUME_PRESETS,
  formatRange,
  isCustomGrowthMin,
  isCustomVolume,
  needsGrowth,
  toggleVolume,
  trendFlagUnavailable,
  type FilterAvailability,
  type FilterRange,
  type FilterState,
  type FundamentalKind,
} from '../pages/screenerFilters';
import './filter-sheet.css';

/**
 * Screener filter editor: a bottom sheet on phones (drag the handle down to close) and a right-hand
 * drawer on wider screens. Edits a draft; 「套用」 hands it back. Conditions the current rows cannot
 * use are replaced by a short explanation instead of controls.
 */
export interface FilterSheetProps {
  open: boolean;
  market: 'TW' | 'US';
  groupName: string;
  value: FilterState;
  availability: FilterAvailability;
  /** Rows the draft would show (group rows, text search included). */
  countFor: (state: FilterState) => number;
  growth: GrowthFile | null | undefined;
  growthLoading: boolean;
  /** How many of the group's rows have the data a growth condition needs (the rest are excluded, or kept with `nd:keep`). */
  coverage: (kind: FundamentalKind | 'accel', periods: number, includeTurn: boolean) => { covered: number; total: number };
  /** The draft uses a fundamental condition: load the growth file. */
  onNeedGrowth: () => void;
  /** Shortest 52-week window among the rows when it is not a full year yet. */
  shortHighWindow: number | null;
  onApply: (state: FilterState) => void;
  onClose: () => void;
}

const DIALOG_ID = 'screener-filters';
const MA_SLIDER_LIMIT = 20;
const CHANGE_PRESETS: { label: string; range: FilterRange }[] = [
  { label: '漲 ≥3%', range: { min: 3, max: null } },
  { label: '漲 0～3%', range: { min: 0, max: 3 } },
  { label: '跌 0～3%', range: { min: -3, max: 0 } },
  { label: '跌 ≥3%', range: { min: null, max: -3 } },
];
const sameRange = (a: FilterRange | null | undefined, b: FilterRange | null | undefined) =>
  (a?.min ?? null) === (b?.min ?? null) && (a?.max ?? null) === (b?.max ?? null) && !!a === !!b;

function Section({ title, hint, unavailable, children }: { title: string; hint?: ReactNode; unavailable?: string | null; children: ReactNode }) {
  return (
    <section className={`fs-section ${unavailable ? 'is-unavailable' : ''}`} aria-label={title}>
      <h3 className="fs-section-title">{title}</h3>
      {unavailable ? <p className="fs-unavailable">{unavailable}</p> : children}
      {hint && !unavailable && <p className="fs-hint">{hint}</p>}
    </section>
  );
}

/** Single-choice toggle chips; `null` is the 「不限」 option. */
function Choice<T extends number>({
  label,
  options,
  value,
  onChange,
  compact = false,
}: {
  label: string;
  options: readonly { value: T | null; label: string }[];
  value: T | null;
  onChange: (value: T | null) => void;
  /** Tighter chips so a row of six short options fits one line on a phone. */
  compact?: boolean;
}) {
  return (
    <div className={compact ? 'fs-chips compact' : 'fs-chips'} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          className={`fs-chip ${option.value === value ? 'active' : ''}`}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** Text input for an optional number; keeps partial input such as `-` while typing. */
function NumberField({ label, value, onChange }: { label: string; value: number | null; onChange: (value: number | null) => void }) {
  const [text, setText] = useState(value === null ? '' : String(value));
  useEffect(() => {
    const parsed = text.trim() === '' ? null : Number(text);
    if (parsed !== value) setText(value === null ? '' : String(value));
    // Only an outside change (preset, reset) rewrites the text.
  }, [value]);
  return (
    <label className="fs-number">
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        enterKeyHint="done"
        placeholder="不限"
        value={text}
        aria-label={`今日漲跌${label}（%）`}
        onChange={(event) => {
          const next = event.target.value.replace(/[^\d.+-]/g, '').slice(0, 7);
          setText(next);
          if (next.trim() === '') onChange(null);
          else if (Number.isFinite(Number(next))) onChange(Number(next));
        }}
      />
      <span className="fs-unit">%</span>
    </label>
  );
}

/** Two overlaid range inputs; an end at the slider limit means 「不限」. */
function RangeSlider({ label, value, onChange }: { label: string; value: FilterRange | undefined; onChange: (value: FilterRange | undefined) => void }) {
  const limit = MA_SLIDER_LIMIT;
  const low = Math.max(-limit, Math.min(limit, value?.min ?? -limit));
  const high = Math.max(-limit, Math.min(limit, value?.max ?? limit));
  const set = (min: number, max: number) => {
    const next = { min: min <= -limit ? null : min, max: max >= limit ? null : max };
    onChange(next.min === null && next.max === null ? undefined : next);
  };
  const percent = (n: number) => ((n + limit) / (2 * limit)) * 100;
  const text = value ? formatRange(value) : '不限';
  return (
    <div className="fs-range">
      <div className="fs-range-head">
        <span>{label}</span>
        <b className={value ? 'set' : ''}>{text}</b>
      </div>
      <div className="fs-range-track">
        <span className="fs-range-zero" style={{ left: `${percent(0)}%` }} aria-hidden="true" />
        <span
          className={`fs-range-fill ${value ? 'set' : ''}`}
          style={{ left: `${percent(low)}%`, right: `${100 - percent(high)}%` }}
          aria-hidden="true"
        />
        <input
          type="range"
          min={-limit}
          max={limit}
          step={1}
          value={low}
          // Overlapping thumbs on the right half: the lower bound must stay reachable.
          style={low >= high && low > 0 ? { zIndex: 2 } : undefined}
          aria-label={`${label} 下限`}
          aria-valuetext={low <= -limit ? '不限' : `${low}%`}
          onChange={(event) => set(Math.min(Number(event.target.value), high), high)}
        />
        <input
          type="range"
          min={-limit}
          max={limit}
          step={1}
          value={high}
          aria-label={`${label} 上限`}
          aria-valuetext={high >= limit ? '不限' : `${high}%`}
          onChange={(event) => set(low, Math.max(Number(event.target.value), low))}
        />
      </div>
      <div className="fs-range-scale" aria-hidden="true">
        <span>≤−{limit}%</span>
        <span>0</span>
        <span>≥+{limit}%</span>
      </div>
    </div>
  );
}

type GateKey = 'eps' | 'rev' | 'epsY' | 'revY';
/** How many periods each growth block compares; kept per block even while its threshold is 「不限」. */
type GatePeriods = Record<GateKey, number>;
const periodsOf = (state: FilterState): GatePeriods => ({
  eps: state.eps?.quarters ?? 1,
  rev: state.rev?.quarters ?? 1,
  epsY: state.epsY?.years ?? 1,
  revY: state.revY?.years ?? 1,
});
const QUARTER_OPTIONS = Array.from({ length: MAX_GATE_QUARTERS }, (_, index) => ({
  value: index + 1,
  label: index === 0 ? '最近 1 季' : `近 ${index + 1} 季皆`,
}));
const YEAR_OPTIONS = Array.from({ length: MAX_GATE_YEARS }, (_, index) => ({
  value: index + 1,
  label: index === 0 ? '最近 1 年' : `近 ${index + 1} 年皆`,
}));
const GROWTH_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: '不限' },
  ...GROWTH_PRESETS.map((preset) => ({ value: preset, label: `≥${preset}%` })),
];
/** Where each market's growth numbers come from (build_growth.py's source order). */
const GROWTH_SOURCE_TEXT: Record<'TW' | 'US', string> = {
  TW: '公開資訊觀測站／證交所・櫃買中心 OpenAPI 綜合損益表（官方，全部上市櫃），缺的才用 Yahoo 財報',
  US: 'SEC 財報（companyfacts）與 SEC XBRL frames（全市場），其次為 Yahoo 財報／Yahoo 季資料（S&P 500）',
};
const GROWTH_BLOCKS: { key: GateKey; title: string; unit: '季' | '年' }[] = [
  { key: 'eps', title: '季度 EPS 成長', unit: '季' },
  { key: 'epsY', title: '年度 EPS 成長', unit: '年' },
  { key: 'rev', title: '季度營收成長', unit: '季' },
  { key: 'revY', title: '年度營收成長', unit: '年' },
];

export function FilterSheet(props: FilterSheetProps) {
  const { open, market, value, availability, growth, growthLoading, onNeedGrowth, onClose } = props;
  const [draft, setDraft] = useState<FilterState>(value);
  const [periods, setPeriods] = useState<GatePeriods>(() => periodsOf(value));
  const sheetRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; y: number; dy: number } | null>(null);
  // Each opening starts from the applied conditions.
  useEffect(() => {
    if (!open) return;
    setDraft(value);
    setPeriods(periodsOf(value));
  }, [open]);
  useDialogFocus(open, DIALOG_ID);
  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [open, onClose]);
  const wantsGrowth = needsGrowth(draft);
  // The growth data is small; load it as soon as the sheet opens so the coverage notes can show real counts.
  useEffect(() => {
    if (open) onNeedGrowth();
  }, [open, onNeedGrowth]);
  const count = useMemo(() => (open ? props.countFor(draft) : 0), [open, draft, props.countFor]);
  if (!open) return null;

  const update = (patch: Partial<FilterState>) => setDraft((previous) => ({ ...previous, ...patch }));
  const gateMin = (key: GateKey): number | null => draft[key]?.min ?? null;
  const setGateMin = (key: GateKey, min: number | null) =>
    update({
      [key]:
        min === null
          ? null
          : key === 'epsY' || key === 'revY'
            ? { years: periods[key], min }
            : { quarters: periods[key], min },
    });
  const setGatePeriods = (key: GateKey, count: number | null) => {
    const next = count ?? 1;
    setPeriods((previous) => ({ ...previous, [key]: next }));
    setDraft((previous) => {
      const gate = previous[key];
      if (!gate) return previous;
      return { ...previous, [key]: key === 'epsY' || key === 'revY' ? { years: next, min: gate.min } : { quarters: next, min: gate.min } };
    });
  };
  const toggleTrend = (flag: (typeof TREND_FLAGS)[number]) =>
    setDraft((previous) => ({
      ...previous,
      tr: previous.tr.includes(flag) ? previous.tr.filter((f) => f !== flag) : TREND_FLAGS.filter((f) => f === flag || previous.tr.includes(f)),
    }));

  // Drag the handle down to dismiss; the pointer moves only touch the transform.
  const onHandleDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    drag.current = { id: event.pointerId, y: event.clientY, dy: 0 };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    if (sheetRef.current) sheetRef.current.style.transition = 'none';
  };
  const onHandleMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (!state || state.id !== event.pointerId || !sheetRef.current) return;
    state.dy = Math.max(0, event.clientY - state.y);
    sheetRef.current.style.transform = `translateY(${state.dy}px)`;
  };
  const onHandleUp = () => {
    const state = drag.current;
    drag.current = null;
    if (!state || !sheetRef.current) return;
    sheetRef.current.style.transition = '';
    if (state.dy > 90) onClose();
    else sheetRef.current.style.transform = '';
  };

  const growthDate = growth?.generatedAt ? formatDateTime(growth.generatedAt, false) : null;
  const growthPending = growth === undefined || growthLoading;
  // Nothing at all to filter on (data missing, or none of the rows has any): one explanation for the whole block.
  const fundamentalsBlocked =
    !growthPending && availability.eps && availability.rev && availability.epsY && availability.revY && availability.accel
      ? availability.eps
      : null;
  const benchmark = market === 'US' ? 'S&P 500（SPY）' : '加權指數（^TWII）';
  const marketName = market === 'US' ? '美股' : '台股';
  /**
   * Coverage of one condition among this group's rows: a short count while it is not applied, and the
   * full 「僅涵蓋…其餘會被排除」 warning once it is (so a short financial history is never hidden).
   */
  const coverageText = (kind: FundamentalKind | 'accel', count: number, applied: boolean) => {
    if (growth === undefined) return growthLoading ? '基本面資料載入中…' : null;
    if (growthLoading) return '基本面資料載入中…';
    const { covered, total } = props.coverage(kind, count, draft.turn);
    if (!total) return null;
    const rest = total - covered;
    const percent = Math.round((covered / total) * 100);
    const need = kind === 'accel' ? '最近 2 季 EPS YoY' : `近 ${count} ${kind.endsWith('Y') ? '年' : '季'}${kind.startsWith('eps') ? ' EPS' : '營收'} YoY`;
    const counts = `${covered.toLocaleString('en-US')} / ${total.toLocaleString('en-US')} 檔（${percent}%）`;
    if (!applied) return `${marketName}財報資料涵蓋 ${counts} 有${need}。`;
    return `此條件僅涵蓋 ${counts} 有足夠財報歷史（需有${need}）；${
      rest
        ? `其餘 ${rest.toLocaleString('en-US')} 檔${draft.keepMissing ? '會保留（已關閉「資料不足時排除」）' : '會被排除（可到下方關閉「資料不足時排除」保留）'}。`
        : '沒有被排除的股票。'
    }`;
  };

  return (
    <div className="fs-backdrop" onClick={onClose}>
      <div
        ref={sheetRef}
        className="fs-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fs-title"
        data-dialog-focus={DIALOG_ID}
        onClick={(event) => event.stopPropagation()}
      >
        <div
          className="fs-handle"
          aria-hidden="true"
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
        >
          <span />
        </div>
        <header className="fs-head">
          <div>
            <b id="fs-title">篩選條件</b>
            <small>
              {market === 'TW' ? '台股' : '美股'} · {props.groupName}
            </small>
          </div>
          <button type="button" className="icon-button" aria-label="關閉篩選" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="fs-body">
          <Section title="今日漲跌" unavailable={availability.chg}>
            <div className="fs-chips" role="group" aria-label="今日漲跌快選">
              {CHANGE_PRESETS.map((preset) => {
                const active = sameRange(draft.chg, preset.range);
                return (
                  <button
                    key={preset.label}
                    type="button"
                    className={`fs-chip ${active ? 'active' : ''}`}
                    aria-pressed={active}
                    onClick={() => update({ chg: active ? null : preset.range })}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>
            <div className="fs-number-row">
              <NumberField label="最小" value={draft.chg?.min ?? null} onChange={(min) => update({ chg: min === null && (draft.chg?.max ?? null) === null ? null : { min, max: draft.chg?.max ?? null } })} />
              <span className="fs-dash" aria-hidden="true">～</span>
              <NumberField label="最大" value={draft.chg?.max ?? null} onChange={(max) => update({ chg: max === null && (draft.chg?.min ?? null) === null ? null : { min: draft.chg?.min ?? null, max } })} />
            </div>
          </Section>

          <Section
            title="均線乖離"
            hint="收盤價高於（+）或低於（−）EMA20（20 日指數移動平均）的百分比；滑到兩端代表不限。"
            unavailable={availability.ema20}
          >
            <RangeSlider label="EMA20" value={draft.ema20 ?? undefined} onChange={(range) => update({ ema20: range ?? null })} />
          </Section>

          <Section
            title="趨勢（可複選）"
            unavailable={TREND_FLAGS.every((flag) => trendFlagUnavailable(flag, availability)) ? availability.tr : null}
            hint="多頭排列：股價 > MA20 > MA50 > MA200。排除空頭：股價 < MA20 < MA50 且 MA20 下彎。"
          >
            <div className="fs-chips" role="group" aria-label="趨勢條件">
              {TREND_FLAGS.map((flag) => {
                const reason = trendFlagUnavailable(flag, availability);
                // A selected flag stays clickable so it can be turned off.
                return (
                  <button
                    key={flag}
                    type="button"
                    className={`fs-chip ${draft.tr.includes(flag) ? 'active' : ''}`}
                    aria-pressed={draft.tr.includes(flag)}
                    disabled={!!reason && !draft.tr.includes(flag)}
                    title={reason ?? undefined}
                    onClick={() => toggleTrend(flag)}
                  >
                    {TREND_LABELS[flag]}
                  </button>
                );
              })}
            </div>
          </Section>

          <Section
            title="距 52 週高點"
            unavailable={availability.hi}
            hint={props.shortHighWindow ? `歷史資料累積中：目前以近 ${props.shortHighWindow} 日高點計算。` : '收盤價距離近一年最高價的跌幅。'}
          >
            <Choice
              label="距 52 週高點"
              value={draft.hi}
              onChange={(hi) => update({ hi })}
              options={[
                { value: null, label: '不限' },
                { value: 5, label: '≤5%' },
                { value: 10, label: '≤10%' },
                { value: 20, label: '≤20%' },
              ]}
            />
          </Section>

          <Section
            title="相對強弱（對大盤指數）"
            unavailable={availability.rs && availability.rs3 && availability.rs1 ? availability.rs : null}
            hint={`大盤基準：${benchmark}。相對報酬＝(1＋個股報酬)÷(1＋大盤報酬)−1，大於 0 代表跑贏大盤。RS 百分位＝個股 3 個月相對報酬，在${marketName}「全市場篩選」名單中的排名（99 最強、1 最弱；名單範圍見全市場篩選頁的說明）。`}
          >
            {(!availability.rs1 || !availability.rs3) && (
              <div className="fs-chips" role="group" aria-label="相對大盤報酬">
                {!availability.rs1 && (
                  <button
                    type="button"
                    className={`fs-chip ${draft.rs1 === 0 ? 'active' : ''}`}
                    aria-pressed={draft.rs1 === 0}
                    onClick={() => update({ rs1: draft.rs1 === 0 ? null : 0 })}
                  >
                    1個月強於大盤
                  </button>
                )}
                {!availability.rs3 && (
                  <button
                    type="button"
                    className={`fs-chip ${draft.rs3 === 0 ? 'active' : ''}`}
                    aria-pressed={draft.rs3 === 0}
                    onClick={() => update({ rs3: draft.rs3 === 0 ? null : 0 })}
                  >
                    3個月強於大盤
                  </button>
                )}
              </div>
            )}
            {!availability.rs && (
              <Choice
                label="RS 百分位"
                value={draft.rs}
                onChange={(rs) => update({ rs })}
                options={[
                  { value: null, label: 'RS 不限' },
                  { value: 70, label: '≥70' },
                  { value: 80, label: '≥80' },
                  { value: 90, label: '≥90' },
                ]}
              />
            )}
          </Section>

          <Section title="量比" unavailable={availability.vr} hint="今日成交量 ÷ 前一交易日成交量。">
            <Choice
              label="量比"
              value={draft.vr}
              onChange={(vr) => update({ vr: toggleVolume(draft.vr, vr) })}
              options={[
                { value: null, label: '不限' },
                ...VOLUME_PRESETS.map((preset) => ({ value: preset, label: `≥${preset}` })),
              ]}
            />
            {isCustomVolume(draft.vr) && (
              <p className="fs-hint fs-current">目前條件：≥{Number(draft.vr.toFixed(2))}</p>
            )}
          </Section>

          <Section title="基本面（EPS／營收成長）" unavailable={fundamentalsBlocked}>
            <p className="fs-hint fs-lead">
              年增率（YoY）一律和「去年同期」比：季度比去年同一季，年度比前一個會計年度。幅度 ≥ X% 且「近 N 期皆」達標才通過。
              {market === 'TW'
                ? ' 台股季度由官方綜合損益表的年度累計數相減而得（第 4 季＝全年－前三季）；股票股利（配股）不追溯調整，配股的公司 EPS 年增率會偏低。'
                : ' 美股第 4 季多由「全年－前三季」推算；外國發行人（20-F）多只有年度資料，以申報幣別計算。'}
            </p>
            {GROWTH_BLOCKS.map(({ key, title, unit }) => {
              const blocked = growthPending ? null : availability[key];
              const min = gateMin(key);
              const note = blocked ? null : coverageText(key, periods[key], draft[key] !== null);
              return (
                <div key={key} className="fs-block" role="group" aria-label={title}>
                  <div className="fs-sub">{title}</div>
                  {blocked ? (
                    <p className="fs-unavailable">{blocked}</p>
                  ) : (
                    <>
                      <Choice
                        label={`${title}期數`}
                        value={periods[key]}
                        onChange={(count) => setGatePeriods(key, count)}
                        options={unit === '季' ? QUARTER_OPTIONS : YEAR_OPTIONS}
                      />
                      <Choice
                        label={`${title}幅度`}
                        value={min}
                        onChange={(next) => setGateMin(key, next)}
                        options={GROWTH_OPTIONS}
                        compact
                      />
                      {isCustomGrowthMin(min) && <p className="fs-hint fs-current">目前條件：≥{Number(min.toFixed(2))}%</p>}
                      {note && <p className="fs-hint fs-coverage">{note}</p>}
                    </>
                  )}
                </div>
              );
            })}
            <div className="fs-block" role="group" aria-label="成長進階選項">
              <div className="fs-sub">進階</div>
              {availability.accel && !growthPending ? (
                <p className="fs-unavailable">EPS 成長加速：{availability.accel}</p>
              ) : (
                <label className="fs-switch">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={draft.accel}
                    aria-label="EPS 成長加速"
                    onChange={(event) => update({ accel: event.target.checked })}
                  />
                  <span className="fs-switch-track" aria-hidden="true" />
                  <span>
                    EPS 成長加速
                    <small>最新一季的 EPS 年增率 ＞ 前一季的年增率（例：前一季 +10%、最新一季 +25%）。</small>
                    {draft.accel && coverageText('accel', 2, true) && <small className="fs-coverage">{coverageText('accel', 2, true)}</small>}
                  </span>
                </label>
              )}
              <label className="fs-switch">
                <input
                  type="checkbox"
                  role="switch"
                  checked={draft.turn}
                  aria-label="含虧轉盈"
                  onChange={(event) => update({ turn: event.target.checked })}
                />
                <span className="fs-switch-track" aria-hidden="true" />
                <span>
                  含虧轉盈
                  <small>去年同期 EPS ≤ 0、本期轉為獲利時 YoY 無法計算；開啟時視為通過（EPS 成長與加速），關閉則視為缺資料。</small>
                </span>
              </label>
              <label className="fs-switch">
                <input
                  type="checkbox"
                  role="switch"
                  checked={!draft.keepMissing}
                  aria-label="資料不足時排除"
                  onChange={(event) => update({ keepMissing: !event.target.checked })}
                />
                <span className="fs-switch-track" aria-hidden="true" />
                <span>
                  資料不足時排除
                  <small>缺財報資料、財報歷史不夠長，或去年同期虧損（YoY 無法計算）時；關閉則保留這些股票。</small>
                </span>
              </label>
            </div>
            <p className="fs-hint">
              {growthLoading
                ? '基本面資料載入中…'
                : growth
                  ? `資料：${GROWTH_SOURCE_TEXT[market]}${growthDate ? ` · 更新 ${growthDate}` : ''} · 財報公布後數日更新`
                  : '開啟面板後載入基本面資料。'}
            </p>
          </Section>
        </div>
        <footer className="fs-foot">
          <button type="button" className="ghost-button" onClick={() => setDraft({ ...EMPTY_FILTERS, tr: [] })}>
            重設
          </button>
          <button type="button" className="primary-button fs-apply" onClick={() => props.onApply(draft)}>
            {wantsGrowth && (growth === undefined || growthLoading) ? '套用' : `套用（${count.toLocaleString('en-US')} 檔）`}
          </button>
        </footer>
      </div>
    </div>
  );
}
