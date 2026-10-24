import { HelpDisclosure } from './HelpDisclosure';
import { useMemo, useState } from 'react';
import type { ThemesData } from '../report/themes';
import { ROTATION_LABELS, themeRotation, type RotationState } from '../pages/themeRotation';
import { formatPercent } from './format';
import './research.css';

export function ThemeRotationCard({ data, onOpen }: { data: ThemesData; onOpen: (key: string) => void }) {
  const [state, setState] = useState<RotationState | 'all'>('all');
  const entries = useMemo(() => themeRotation(data), [data]);
  return (
    <details className="theme-rotation research-disclosure">
      <summary>
        題材輪動追蹤 <span>領先・轉強・轉弱・落後</span>
      </summary>
      <div className="research-disclosure-body">
        <HelpDisclosure label="輪動計算說明">用各題材相對 {data.benchmark.label} 的近 21 個交易日報酬，以及相對前一個 21 日區間的變化，觀察強弱輪動。</HelpDisclosure>
        <div className="console-actions" role="group" aria-label="輪動狀態">
          <button type="button" className="chip" aria-pressed={state === 'all'} onClick={() => setState('all')}>
            全部
          </button>
          {(Object.keys(ROTATION_LABELS) as RotationState[]).map((key) => (
            <button
              key={key}
              type="button"
              className={`chip ${state === key ? 'active' : ''}`}
              aria-pressed={state === key}
              onClick={() => setState(key)}
            >
              {ROTATION_LABELS[key]} {entries.filter((entry) => entry.state === key).length}
            </button>
          ))}
        </div>
        {!entries.length ? (
          <p>需要至少 43 個對齊的交易日資料才能比較兩段相對報酬；目前歷史不足，暫不推算輪動狀態。</p>
        ) : (
          <div className="research-table-scroll">
            <table className="research-table" aria-label="題材輪動比較">
              <thead>
                <tr>
                  <th>題材</th>
                  <th>相對排名</th>
                  <th>排名變化</th>
                  <th>近月相對報酬</th>
                  <th>變化（百分點）</th>
                  <th>狀態</th>
                </tr>
              </thead>
              <tbody>
                {entries
                  .filter((entry) => state === 'all' || entry.state === state)
                  .map((entry) => (
                    <tr key={entry.key}>
                      <th>
                        <button type="button" className="text-button" onClick={() => onOpen(entry.key)}>
                          {entry.name}
                        </button>
                      </th>
                      <td>{entry.rank}</td>
                      <td>{entry.rankChange > 0 ? `↑${entry.rankChange}` : entry.rankChange < 0 ? `↓${-entry.rankChange}` : '—'}</td>
                      <td>{formatPercent(entry.strength)}</td>
                      <td>
                        {entry.change > 0 ? '+' : ''}
                        {entry.change.toFixed(2)}
                      </td>
                      <td>
                        <span className={`rotation-state ${entry.state}`}>{ROTATION_LABELS[entry.state]}</span>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
        <HelpDisclosure label="狀態與排名說明">
          領先＝相對報酬為正且改善；轉弱＝為正但下滑；落後＝為負且下滑；轉強＝為負但改善。排名變化僅比較兩期皆有資料的題材，並非資金流入流出。
          {entries[0] ? `比較日 ${entries[0].previousDate} → ${entries[0].asOf}。` : ''}
        </HelpDisclosure>
      </div>
    </details>
  );
}
