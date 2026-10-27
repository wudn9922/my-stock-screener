import { useId, useSyncExternalStore } from 'react';
import { sitePreferences } from '../app/sitePreferences';
import { InstallPanel } from '../pwa/InstallPanel';
import { AppUpdate } from '../pwa/AppUpdate';
import { HelpDisclosure } from './HelpDisclosure';
import { formatDateTime } from './format';

/** Shared verbatim display/App controls for the gear dialog and 我的. */
export function DisplaySettings({ reportDate, generatedAt }: { reportDate: string | null; generatedAt: string | null }) {
  const preferences = useSyncExternalStore(sitePreferences.subscribe, sitePreferences.get);
  const colorGroup = useId();
  return <>
    <fieldset className="segmented-field">
      <legend>漲跌顏色</legend>
      <div className="segmented">
        {([['tw', '紅漲綠跌'], ['us', '綠漲紅跌']] as const).map(([value, label]) => (
          <label key={value} className={preferences.colors === value ? 'active' : ''}>
            <input type="radio" name={colorGroup} value={value} checked={preferences.colors === value}
              onChange={() => sitePreferences.setColors(value)} />
            <span className={`swatch ${value}`} aria-hidden="true" />{label}
          </label>
        ))}
      </div>
    </fieldset>
    <InstallPanel />
    <AppUpdate />
    <dl className="dialog-facts">
      <div><dt>報告日期</dt><dd>{reportDate ? formatDateTime(reportDate, false) : '—'}</dd></div>
      <div><dt>產生時間</dt><dd>{generatedAt ? formatDateTime(generatedAt) : '—'}</dd></div>
    </dl>
    <HelpDisclosure label="行情與儲存說明">行情為延遲資料，僅供研究參考，非投資建議。圖表設定與畫線依帳號儲存權限保留。</HelpDisclosure>
  </>;
}
