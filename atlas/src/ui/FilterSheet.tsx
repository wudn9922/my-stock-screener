import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { ChevronDown, X } from 'lucide-react';
import { createPortal } from 'react-dom';
import { useDialogFocus } from './useDialogFocus';
import { formatDateTime } from './format';
import type { GrowthFile } from '../fundamentals/GrowthProvider';
import {
  EMPTY_FILTERS,
  GROWTH_PRESETS,
  QUARTER_PRESETS,
  SHARE_DEFAULT,
  SHARE_PRESETS,
  TREND_FLAGS,
  TREND_LABELS,
  VOLUME_PRESETS,
  SHARE_VOLUME_PRESETS,
  YEAR_PRESETS,
  enoughPeriods,
  formatRange,
  formatShares,
  gateCountText,
  gateLabel,
  gateSentence,
  isCustomVolume,
  needsGrowth,
  toggleVolume,
  trendFlagUnavailable,
  yearGateLabel,
  type FilterAvailability,
  type FilterRange,
  type FilterState,
  type FundamentalKind,
  type GrowthGate,
  type YearGate,
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
  coverage: (kind: FundamentalKind | 'accel' | 'streak', periods: number, includeTurn: boolean) => { covered: number; total: number };
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

interface ChoiceOption<T extends number> {
  value: T | null;
  label: string;
  /** Not offered: no row of this group has the data (the applied option stays clickable). */
  disabled?: boolean;
  title?: string;
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
  options: readonly ChoiceOption<T>[];
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
          disabled={option.disabled}
          title={option.title}
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
/** The terms of one growth block: how many periods, and the 成長佔比 P. Kept while its threshold is 「不限」. */
interface GateTerms {
  periods: number;
  share: number;
}
type GateTermsMap = Record<GateKey, GateTerms>;
const isAnnualGate = (key: GateKey) => key === 'epsY' || key === 'revY';
const termsOf = (state: FilterState): GateTermsMap => ({
  eps: { periods: state.eps?.quarters ?? 1, share: state.eps?.share ?? SHARE_DEFAULT },
  rev: { periods: state.rev?.quarters ?? 1, share: state.rev?.share ?? SHARE_DEFAULT },
  epsY: { periods: state.epsY?.years ?? 1, share: state.epsY?.share ?? SHARE_DEFAULT },
  revY: { periods: state.revY?.years ?? 1, share: state.revY?.share ?? SHARE_DEFAULT },
});
/** The gate a block applies once it has a threshold. */
const gateFor = (key: GateKey, terms: GateTerms, min: number): GrowthGate | YearGate =>
  isAnnualGate(key) ? { years: terms.periods, min, share: terms.share } : { quarters: terms.periods, min, share: terms.share };
/** Presets plus the applied count when a link carries another one (sorted, unique). */
const countsWith = (presets: readonly number[], current: number | null): number[] =>
  [...new Set([...presets, ...(current !== null ? [current] : [])])].sort((a, b) => a - b);
/**
 * A choice no row can cover (e.g. 10 years of Taiwan annual data, which has three) is disabled with the reason,
 * unless it is the applied one. `covered` null = the growth data is still loading: nothing is disabled yet.
 */
const coverageChoice = (value: number, current: number | null, label: string, covered: number | null, reason: string): ChoiceOption<number> => {
  const none = covered === 0;
  return { value, label, disabled: none && value !== current, title: none ? reason : undefined };
};
/** Period choices of a growth block (quarters or years), with the rows that have enough of them. */
function periodChoices(unit: '季' | '年', current: number, covered: ((count: number) => number) | null): ChoiceOption<number>[] {
  const presets = unit === '季' ? QUARTER_PRESETS : YEAR_PRESETS;
  return countsWith(presets, current).map((count) =>
    coverageChoice(
      count,
      current,
      count === 1 ? `最近 1 ${unit}` : `近 ${count} ${unit}`,
      covered ? covered(count) : null,
      `本群組沒有足夠 ${enoughPeriods(count)} ${unit}財報資料的股票`,
    ),
  );
}
const GROWTH_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: '不限' },
  ...GROWTH_PRESETS.map((preset) => ({ value: preset, label: `≥${preset}%` })),
];
const SHARE_OPTIONS: ChoiceOption<number>[] = SHARE_PRESETS.map((share) => ({
  value: share,
  label: `${share}%`,
  title: share === SHARE_DEFAULT ? '每一期都要成長（預設）' : `有資料的期數中至少 ${share}% 要成長`,
}));
/** Where each market's growth numbers come from (build_growth.py's source order). */
const GROWTH_SOURCE_TEXT: Record<'TW' | 'US', string> = {
  TW: '公開資訊觀測站／證交所・櫃買中心 OpenAPI 綜合損益表（官方）與 FinMind 歷史財報（源自公開資訊觀測站，約 10 年，全部上市櫃），缺的才用 Yahoo 財報',
  US: 'SEC 財報（companyfacts）與 SEC XBRL frames（全市場），其次為 Yahoo 財報／Yahoo 季資料（S&P 500）',
};

/**
 * A typed number of a growth term (periods, threshold or share). Only a valid value is committed; partial input such
 * as `-` stays on screen with the hint. An empty threshold is 「不限」 (`allowEmpty`).
 */
function TermInput({
  label,
  value,
  unit,
  min,
  max,
  integer = false,
  allowEmpty = false,
  hint,
  onCommit,
}: {
  label: string;
  value: number | null;
  unit: string;
  min: number;
  max: number;
  integer?: boolean;
  allowEmpty?: boolean;
  hint: string;
  onCommit: (value: number | null) => void;
}) {
  const [text, setText] = useState(value === null ? '' : String(value));
  const [invalid, setInvalid] = useState(false);
  const parse = (raw: string): { ok: true; value: number | null } | { ok: false } => {
    const trimmed = raw.trim();
    if (trimmed === '') return allowEmpty ? { ok: true, value: null } : { ok: false };
    const number = Number(trimmed);
    if (!Number.isFinite(number) || (integer && !Number.isInteger(number)) || number < min || number > max) return { ok: false };
    return { ok: true, value: number };
  };
  // An outside change (a preset, a reset, another link) rewrites the text; the value just committed does not,
  // so a typed 「1.」 or 「-」 survives its own commit.
  useEffect(() => {
    const current = parse(text);
    if (current.ok && current.value === value) return;
    setText(value === null ? '' : String(value));
    setInvalid(false);
  }, [value]);
  return (
    <label className={`fs-term-input ${invalid ? 'is-invalid' : ''}`}>
      <input
        type="text"
        inputMode="decimal"
        enterKeyHint="done"
        placeholder={allowEmpty ? '不限' : undefined}
        value={text}
        aria-label={label}
        aria-invalid={invalid}
        onChange={(event) => {
          const next = event.target.value.replace(/[^\d.+-]/g, '').slice(0, 7);
          setText(next);
          const parsed = parse(next);
          setInvalid(!parsed.ok);
          if (parsed.ok && parsed.value !== value) onCommit(parsed.value);
        }}
      />
      <span className="fs-unit">{unit}</span>
      {invalid && <span className="fs-term-error">{hint}</span>}
    </label>
  );
}

/** One growth block (EPS or revenue, quarterly or annual): periods, threshold X, 成長佔比 P, and the rule in words. */
function GrowthGateBlock({
  title,
  unit,
  subject,
  terms,
  gate,
  blocked,
  note,
  coveredOf,
  onTerms,
  onMin,
}: {
  title: string;
  unit: '季' | '年';
  /** The indicator in the sentence, e.g. 「EPS 年增」. */
  subject: string;
  terms: GateTerms;
  /** The applied gate (null while its threshold is 「不限」). */
  gate: GrowthGate | YearGate | null;
  blocked: string | null;
  note: string | null;
  /** Rows with enough periods for a period count (null while loading). */
  coveredOf: ((count: number) => number) | null;
  onTerms: (patch: Partial<GateTerms>) => void;
  onMin: (min: number | null) => void;
}) {
  if (blocked)
    return (
      <div className="fs-block" role="group" aria-label={title}>
        <div className="fs-sub">{title}</div>
        <p className="fs-unavailable">{blocked}</p>
      </div>
    );
  const maxPeriods = unit === '季' ? 12 : 10;
  const { periods, share } = terms;
  const min = gate?.min ?? null;
  return (
    <div className="fs-block" role="group" aria-label={title}>
      <div className="fs-sub">{title}</div>
      <div className="fs-term">
        <span className="fs-term-name">期數</span>
        <Choice label={`${title} · 期數`} value={periods} onChange={(count) => onTerms({ periods: count ?? 1 })} options={periodChoices(unit, periods, coveredOf)} compact />
        <TermInput
          label={`${title} · 期數（${unit}）`}
          value={periods}
          unit={unit}
          min={1}
          max={maxPeriods}
          integer
          hint={`請輸入 1–${maxPeriods} 的整數`}
          onCommit={(count) => count !== null && onTerms({ periods: count })}
        />
      </div>
      <div className="fs-term">
        <span className="fs-term-name">幅度 X</span>
        <Choice label={`${title} · 幅度`} value={min} onChange={onMin} options={GROWTH_OPTIONS} compact />
        <TermInput
          label={`${title} · 幅度 X（%）`}
          value={min}
          unit="%"
          min={-10000}
          max={10000}
          allowEmpty
          hint="請輸入數字（可負數、小數），留空即不限"
          onCommit={onMin}
        />
      </div>
      <div className="fs-term">
        <span className="fs-term-name">成長佔比 P</span>
        <Choice label={`${title} · 成長佔比`} value={share} onChange={(next) => onTerms({ share: next ?? SHARE_DEFAULT })} options={SHARE_OPTIONS} compact />
        <TermInput
          label={`${title} · 成長佔比 P（%）`}
          value={share}
          unit="%"
          min={1}
          max={SHARE_DEFAULT}
          hint="請輸入 1–100（預設 100＝每一期都要成長）"
          onCommit={(next) => next !== null && onTerms({ share: next })}
        />
      </div>
      {gate ? (
        <>
          <p className="fs-rule">{gateSentence(subject, unit, periods, gate.min, share)}</p>
          <p className="fs-hint">
            每一期 YoY ≥ {Number(gate.min.toFixed(2))}% 記為成長，低於則為衰退；{gateCountText(periods, share)}即通過（{periods} {unit}皆有資料時）。
            缺資料的期數不計入；有資料的期數不足 {enoughPeriods(periods)} 期時依「資料不足時排除」處理。
          </p>
        </>
      ) : (
        <p className="fs-hint">選擇或輸入幅度 X 後才會套用（期數與成長佔比 P 會保留）。</p>
      )}
      {note && <p className="fs-hint fs-coverage">{note}</p>}
    </div>
  );
}

/** A block that opens with a button (aria-expanded). Closed by default; its applied conditions stay visible in the head. */
function Disclosure({
  id,
  title,
  count,
  summary,
  children,
}: {
  id: string;
  title: string;
  /** Applied conditions in the block (shown as a count in the head). */
  count: number;
  summary: string | null;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="fs-disclosure">
      <button type="button" className="fs-disclosure-head" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>
        <span className="fs-disclosure-title">{title}</span>
        <span className="fs-disclosure-end">
          {count > 0 && <span className="fs-disclosure-count">已啟用 {count} 項</span>}
          <ChevronDown size={18} aria-hidden="true" className={`fs-chevron ${open ? 'open' : ''}`} />
        </span>
      </button>
      {!open && count > 0 && summary && <p className="fs-hint fs-disclosure-summary">{summary}</p>}
      <div id={id} className="fs-disclosure-body" hidden={!open}>
        {children}
      </div>
    </div>
  );
}

export function FilterSheet(props: FilterSheetProps) {
  const { open, market, value, availability, growth, growthLoading, onNeedGrowth, onClose } = props;
  const [draft, setDraft] = useState<FilterState>(value);
  const [volumeText, setVolumeText] = useState(value.vol === null ? '' : String(value.vol));
  const [terms, setTerms] = useState<GateTermsMap>(() => termsOf(value));
  const sheetRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; y: number; dy: number } | null>(null);
  // Each opening starts from the applied conditions.
  useEffect(() => {
    if (!open) return;
    setDraft(value);
    setVolumeText(value.vol === null ? '' : String(value.vol));
    setTerms(termsOf(value));
  }, [open]);
  useDialogFocus(open, DIALOG_ID);
  useEffect(() => {
    if (!open) return;
    const viewport = window.visualViewport;
    const resize = () => {
      const backdrop = backdropRef.current;
      if (!backdrop) return;
      backdrop.style.height = `${viewport?.height ?? window.innerHeight}px`;
      backdrop.style.top = `${viewport?.offsetTop ?? 0}px`;
      backdrop.style.bottom = 'auto';
    };
    resize();
    viewport?.addEventListener('resize', resize);
    viewport?.addEventListener('scroll', resize);
    window.addEventListener('resize', resize);
    return () => { viewport?.removeEventListener('resize', resize); viewport?.removeEventListener('scroll', resize); window.removeEventListener('resize', resize); };
  }, [open]);
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
  const volumeInvalid = volumeText.trim() !== '' && (!/^\d+$/.test(volumeText.trim()) || !Number.isSafeInteger(Number(volumeText)));
  const setShareVolume = (vol: number | null) => {
    setVolumeText(vol === null ? '' : String(vol));
    update({ vol });
  };
  const setGateMin = (key: GateKey, min: number | null) => update({ [key]: min === null ? null : gateFor(key, terms[key], min) });
  /** Periods or 成長佔比 of a block: kept for later, and applied at once when the block already has a threshold. */
  const setGateTerms = (key: GateKey, patch: Partial<GateTerms>) => {
    const next = { ...terms[key], ...patch };
    setTerms((previous) => ({ ...previous, [key]: next }));
    const gate = draft[key];
    if (gate) update({ [key]: gateFor(key, next, gate.min) });
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
  /** While no streak is applied: how many rows have each number of years (the rest of the block explains the rule). */
  const streakCoverageLine = (() => {
    const { total } = props.coverage('streak', 1, draft.turn);
    const counts = YEAR_PRESETS.map((years) => `≥${years} 年 ${props.coverage('streak', years, draft.turn).covered.toLocaleString('en-US')}`);
    return `各年數涵蓋檔數：${counts.join(' · ')} 檔（共 ${total.toLocaleString('en-US')} 檔）`;
  })();
  const coverageText = (kind: FundamentalKind | 'accel' | 'streak', count: number, applied: boolean) => {
    if (growth === undefined) return growthLoading ? '基本面資料載入中…' : null;
    if (growthLoading) return '基本面資料載入中…';
    const { covered, total } = props.coverage(kind, count, draft.turn);
    if (!total) return null;
    const rest = total - covered;
    const percent = Math.round((covered / total) * 100);
    const unit = kind.endsWith('Y') || kind === 'streak' ? '年' : '季';
    const metric = kind.startsWith('eps') || kind === 'streak' ? 'EPS' : '營收';
    // A streak needs every one of its years (no gap); a growth block needs ceil(N / 2) of its N periods with data.
    const need =
      kind === 'accel'
        ? '最近 2 季的 EPS YoY'
        : kind === 'streak'
          ? `最近 ${count} 年每一年都有 ${metric} YoY`
          : `近 ${count} ${unit}中至少 ${enoughPeriods(count)} ${unit}的 ${metric} YoY`;
    const counts = `${covered.toLocaleString('en-US')} / ${total.toLocaleString('en-US')} 檔（${percent}%）`;
    if (!applied) return `${marketName}財報資料涵蓋 ${counts}，足以判斷：${need}。`;
    return `此條件僅涵蓋 ${counts} 有足夠財報歷史（${need}）；${
      rest
        ? `其餘 ${rest.toLocaleString('en-US')} 檔${draft.keepMissing ? '會保留（已關閉「資料不足時排除」）' : '會被排除（可到下方關閉「資料不足時排除」保留）'}。`
        : '沒有被排除的股票。'
    }`;
  };

  const gateBlock = (key: GateKey, title: string, unit: '季' | '年', subject: string) => {
    const blocked = growthPending ? null : availability[key];
    return (
      <GrowthGateBlock
        title={title}
        unit={unit}
        subject={subject}
        terms={terms[key]}
        gate={draft[key]}
        blocked={blocked}
        note={blocked ? null : coverageText(key, terms[key].periods, draft[key] !== null)}
        coveredOf={growthPending ? null : (periods) => props.coverage(key, periods, draft.turn).covered}
        onTerms={(patch) => setGateTerms(key, patch)}
        onMin={(min) => setGateMin(key, min)}
      />
    );
  };
  const revenueCount = (draft.rev ? 1 : 0) + (draft.revY ? 1 : 0);
  const revenueSummary = [draft.rev ? gateLabel('營收', draft.rev) : null, draft.revY ? yearGateLabel('營收', draft.revY) : null]
    .filter((text): text is string => text !== null)
    .join('；');

  return createPortal(
    <div className="fs-backdrop" ref={backdropRef} onClick={onClose}>
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
          <Section title="每日成交股數" unavailable={availability.vol} hint="以最近一個交易日的成交股數篩選，台美股都以「股」計算（台股 1 張＝1,000 股）。預設不限；留白或輸入 0 可取消門檻。">
            <Choice
              label="成交股數門檻"
              value={draft.vol}
              options={[{ value: null, label: '不限' }, ...SHARE_VOLUME_PRESETS.map((value) => ({ value, label: `≥${formatShares(value)}` }))]}
              onChange={(vol) => setShareVolume(toggleVolume(draft.vol, vol))}
            />
            <div className="fs-number-row">
              <label className="fs-number">
                <span>至少</span>
                <input
                  type="text"
                  inputMode="numeric"
                  enterKeyHint="done"
                  placeholder="不限"
                  aria-label="自訂成交股數下限（股）"
                  aria-invalid={volumeInvalid}
                  aria-describedby={volumeInvalid ? 'fs-volume-error' : undefined}
                  value={volumeText}
                  onChange={(event) => {
                    const text = event.target.value;
                    setVolumeText(text);
                    if (text.trim() === '') update({ vol: null });
                    else if (/^\d+$/.test(text.trim()) && Number.isSafeInteger(Number(text))) update({ vol: Number(text) || null });
                  }}
                />
                <span>股</span>
              </label>
            </div>
            {volumeInvalid && <p id="fs-volume-error" className="fs-unavailable" role="alert">請輸入有效的非負整數股數。</p>}
          </Section>
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
              年增率（YoY）一律和「去年同期」比：季度比去年同一季，年度比前一個會計年度。每一期 YoY ≥ 幅度 X% 記為成長，低於則為衰退；
              近 N 期中，成長期數 ÷ 有資料的期數 ≥ 成長佔比 P% 才通過（P 預設 100%，即每一期都要成長）。缺資料的期數不計入。
              {market === 'TW'
                ? ' 台股季度由官方綜合損益表的年度累計數相減而得（第 4 季＝全年－前三季）；FinMind 歷史資料同樣源自觀測站，單季或累計由其數字判定；股票股利（配股）不追溯調整，配股的公司 EPS 年增率會偏低。'
                : ' 美股第 4 季多由「全年－前三季」推算；外國發行人（20-F）多只有年度資料，以申報幣別計算。'}
            </p>
            {gateBlock('eps', '季度 EPS 成長', '季', 'EPS 年增')}
            {gateBlock('epsY', '年度 EPS 成長', '年', 'EPS 年增')}
            <div className="fs-block" role="group" aria-label="EPS 連續成長年數">
              <div className="fs-sub">EPS 連續成長年數</div>
              {availability.epss && !growthPending ? (
                <p className="fs-unavailable">{availability.epss}</p>
              ) : (
                <>
                  <Choice
                    label="連續成長年數"
                    value={draft.epss}
                    onChange={(years) => update({ epss: years })}
                    options={[
                      { value: null, label: '不限' },
                      ...countsWith(YEAR_PRESETS, draft.epss).map((years) =>
                        coverageChoice(
                          years,
                          draft.epss,
                          `≥${years} 年`,
                          growthPending ? null : props.coverage('streak', years, draft.turn).covered,
                          `本群組沒有足夠 ${years} 年 EPS 年增率的股票`,
                        ),
                      ),
                    ]}
                    compact
                  />
                  {draft.epss !== null ? (
                    <p className="fs-hint fs-coverage">{coverageText('streak', draft.epss, true)}</p>
                  ) : (
                    !growthPending && <p className="fs-hint fs-coverage">{streakCoverageLine}</p>
                  )}
                  <p className="fs-hint">
                    從最新年度往回數，年度 EPS 年增率 ＞ 0 的連續年數（上限 10 年）；「含虧轉盈」開啟時，虧轉盈計為成長。
                    年增率 ≤ 0 會中斷計數；缺資料（去年同期虧損、缺年度）也會中斷，此時依「資料不足時排除」處理。
                  </p>
                </>
              )}
            </div>

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
                  <small>去年同期 EPS ≤ 0、本期轉為獲利時 YoY 無法計算；開啟時計為成長，關閉時在成長期數中計為衰退（連續成長年數與加速則視為缺資料）。</small>
                </span>
              </label>
            </div>
            <Disclosure id="screener-revenue-growth" title="營收" count={revenueCount} summary={revenueSummary}>
              {gateBlock('rev', '季度營收成長', '季', '營收 年增')}
              {gateBlock('revY', '年度營收成長', '年', '營收 年增')}
            </Disclosure>
            <div className="fs-block" role="group" aria-label="資料不足的處理">
              <div className="fs-sub">資料不足</div>
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
                  <small>有財報資料的期數少於 ⌈N÷2⌉ 期、財報歷史不夠長，或去年同期虧損（YoY 無法計算）時；關閉則保留這些股票。</small>
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
          <button type="button" className="ghost-button" onClick={() => { setDraft({ ...EMPTY_FILTERS, tr: [] }); setVolumeText(''); }}>
            重設
          </button>
          <button type="button" className="primary-button fs-apply" disabled={volumeInvalid} onClick={() => props.onApply(draft)}>
            {wantsGrowth && (growth === undefined || growthLoading) ? '套用' : `套用（${count.toLocaleString('en-US')} 檔）`}
          </button>
        </footer>
      </div>
    </div>,
    document.fullscreenElement ?? document.body,
  );
}
