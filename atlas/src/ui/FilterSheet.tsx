import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useDialogFocus } from './useDialogFocus';
import { formatDateTime } from './format';
import type { GrowthFile } from '../fundamentals/GrowthProvider';
import {
  EMPTY_FILTERS,
  TREND_FLAGS,
  TREND_LABELS,
  formatRange,
  needsGrowth,
  trendFlagUnavailable,
  type FilterAvailability,
  type FilterRange,
  type FilterState,
  type GrowthGate,
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
}: {
  label: string;
  options: readonly { value: T | null; label: string }[];
  value: T | null;
  onChange: (value: T | null) => void;
}) {
  return (
    <div className="fs-chips" role="group" aria-label={label}>
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

export function FilterSheet(props: FilterSheetProps) {
  const { open, market, value, availability, growth, growthLoading, onNeedGrowth, onClose } = props;
  const [draft, setDraft] = useState<FilterState>(value);
  const [quarters, setQuarters] = useState(value.eps?.quarters ?? value.rev?.quarters ?? 1);
  const sheetRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; y: number; dy: number } | null>(null);
  // Each opening starts from the applied conditions.
  useEffect(() => {
    if (!open) return;
    setDraft(value);
    setQuarters(value.eps?.quarters ?? value.rev?.quarters ?? 1);
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
  const wantsGrowth = market === 'US' && needsGrowth(draft);
  useEffect(() => {
    if (open && wantsGrowth) onNeedGrowth();
  }, [open, wantsGrowth, onNeedGrowth]);
  const count = useMemo(() => (open ? props.countFor(draft) : 0), [open, draft, props.countFor]);
  if (!open) return null;

  const update = (patch: Partial<FilterState>) => setDraft((previous) => ({ ...previous, ...patch }));
  const setGate = (key: 'eps' | 'rev', min: number | null) =>
    update({ [key]: min === null ? null : ({ quarters, min } satisfies GrowthGate) });
  const setQuarterCount = (next: number | null) => {
    const q = next ?? 1;
    setQuarters(q);
    setDraft((previous) => ({
      ...previous,
      eps: previous.eps ? { ...previous.eps, quarters: q } : null,
      rev: previous.rev ? { ...previous.rev, quarters: q } : null,
    }));
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
  const fundamentalsBlocked = availability.eps && !(market === 'US' && (growth === undefined || growthLoading)) ? availability.eps : null;
  const benchmark = market === 'US' ? 'S&P 500（SPY）' : '加權指數';

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
            hint="收盤價高於（+）或低於（−）均線的百分比；滑到兩端代表不限。"
            unavailable={availability.ma20 && availability.ma50 && availability.ma200 ? availability.ma20 : null}
          >
            {(['20', '50', '200'] as const).map((period) =>
              availability[`ma${period}`] ? null : (
                <RangeSlider
                  key={period}
                  label={`MA${period}`}
                  value={draft.ma[period]}
                  onChange={(range) => {
                    const ma = { ...draft.ma };
                    if (range) ma[period] = range;
                    else delete ma[period];
                    update({ ma });
                  }}
                />
              ),
            )}
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
            title="相對強弱"
            unavailable={availability.rs && availability.rs3 ? availability.rs : null}
            hint={`與${benchmark}比較近 3 個月的漲跌；RS 百分位是同市場股票中的強弱排名（99 最強）。`}
          >
            {!availability.rs3 && (
              <div className="fs-chips" role="group" aria-label="3個月相對強弱">
                <button
                  type="button"
                  className={`fs-chip ${draft.rs3 === 0 ? 'active' : ''}`}
                  aria-pressed={draft.rs3 === 0}
                  onClick={() => update({ rs3: draft.rs3 === 0 ? null : 0 })}
                >
                  3個月強於大盤（RS3M &gt; 0）
                </button>
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

          <Section title="量比" unavailable={availability.vr} hint="今日成交量 ÷ 前 20 日平均量（不含今日）。">
            <Choice
              label="量比"
              value={draft.vr}
              onChange={(vr) => update({ vr })}
              options={[
                { value: null, label: '不限' },
                { value: 1.5, label: '≥1.5x' },
                { value: 2, label: '≥2x' },
              ]}
            />
          </Section>

          <Section title="基本面（美股限定）" unavailable={fundamentalsBlocked}>
            <Choice
              label="期間"
              value={quarters}
              onChange={setQuarterCount}
              options={[
                { value: 1, label: '最近 1 季' },
                { value: 2, label: '近 2 季皆' },
                { value: 3, label: '近 3 季皆' },
              ]}
            />
            <div className="fs-sub">EPS 年增率（YoY）</div>
            <Choice
              label="EPS 年增率"
              value={draft.eps?.min ?? null}
              onChange={(min) => setGate('eps', min)}
              options={[
                { value: null, label: '不限' },
                { value: 0, label: '≥0%' },
                { value: 15, label: '≥15%' },
                { value: 25, label: '≥25%' },
              ]}
            />
            <div className="fs-sub">營收年增率（YoY）</div>
            <Choice
              label="營收年增率"
              value={draft.rev?.min ?? null}
              onChange={(min) => setGate('rev', min)}
              options={[
                { value: null, label: '不限' },
                { value: 0, label: '≥0%' },
                { value: 10, label: '≥10%' },
                { value: 20, label: '≥20%' },
              ]}
            />
            <label className="fs-switch">
              <input
                type="checkbox"
                role="switch"
                checked={!draft.keepMissing}
                onChange={(event) => update({ keepMissing: !event.target.checked })}
              />
              <span className="fs-switch-track" aria-hidden="true" />
              <span>
                資料不足時排除
                <small>缺季度資料或前年同季虧損（YoY 無法計算）時；關閉則保留這些股票。最新一季轉盈視為成長。</small>
              </span>
            </label>
            <p className="fs-hint">
              {growthLoading
                ? '基本面資料載入中…'
                : growth
                  ? `資料：${growth.source ?? 'Yahoo Finance'}${growthDate ? ` · 更新 ${growthDate}` : ''} · 季度財報公布後數日更新`
                  : '選擇條件後載入基本面資料（Yahoo Finance 季度 EPS／營收）。'}
            </p>
          </Section>
        </div>
        <footer className="fs-foot">
          <button type="button" className="ghost-button" onClick={() => setDraft({ ...EMPTY_FILTERS, ma: {}, tr: [] })}>
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
