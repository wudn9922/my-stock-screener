import { userStorage } from '../../auth/storage';
import { RESTATED_EPS_HELP, type EpsDisplayBasis } from '../../fundamentals/epsBasis';

const KEY = 'atlas-financials-eps-basis-v1';
/** Remembered per viewer; 還原 (today's share basis) unless the viewer picked 公告. */
export const readEpsBasis = (): EpsDisplayBasis => {
  try {
    return userStorage.getItem(KEY) === 'reported' ? 'reported' : 'restated';
  } catch {
    return 'restated';
  }
};
export const writeEpsBasis = (basis: EpsDisplayBasis) => {
  try {
    userStorage.setItem(KEY, basis);
  } catch {
    /* Remembering the choice is only a convenience. */
  }
};

/**
 * 「EPS 口徑 還原｜公告」: shown only when some period's EPS differs on today's share basis (配股 or a split). 還原 is
 * the default; 公告 shows the figures as the company printed them. Year-over-year and CAGR compare restated EPS either way.
 */
export function EpsBasisToggle({ basis, label, onBasis }: { basis: EpsDisplayBasis; label: string; onBasis: (basis: EpsDisplayBasis) => void }) {
  return (
    <div className="fin-range fin-eps-basis" data-testid="eps-basis" title={RESTATED_EPS_HELP}>
      <span className="fin-range-label" id="fin-eps-basis-label">
        EPS 口徑
      </span>
      <div className="segmented compact" role="group" aria-labelledby="fin-eps-basis-label">
        {(['restated', 'reported'] as const).map((value) => (
          <button key={value} type="button" className={value === basis ? 'active' : ''} aria-pressed={value === basis} onClick={() => onBasis(value)}>
            {value === 'restated' ? '還原' : '公告'}
          </button>
        ))}
      </div>
      <span className="fin-range-note">{basis === 'restated' ? label : '公告 EPS（公司當時財報數字）；年增率仍以還原 EPS 計算'}</span>
    </div>
  );
}
