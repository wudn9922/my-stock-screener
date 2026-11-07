import { useState } from 'react';
import type { WatchlistVisit, WatchlistMember } from '../watchlists/model';
import type { WatchlistChangeReasons } from '../watchlists/reasons';
import { displayTicker } from './format';

export function WatchlistChanges({ visit, reasons, onOpen }: { visit: WatchlistVisit; reasons?: WatchlistChangeReasons; onOpen: (member: WatchlistMember, kind: 'entered' | 'exited') => void }) {
  const [limit, setLimit] = useState(10);
  return <div className="watchlist-changes">
    {(['entered', 'exited'] as const).map(kind => <section key={kind} aria-label={kind === 'entered' ? '進榜原因' : '出榜原因'}>
      <h3>{kind === 'entered' ? '新入選' : '出榜'}</h3>
      {!visit[kind].length ? <p>{kind === 'entered' && visit.firstVisit ? '首次建立基準' : '無'}</p> : <>
        {visit[kind].slice(0, limit).map(member => {
          const reason = reasons?.[kind][member.symbol];
          return <details className="watchlist-change-reason" key={member.symbol}>
            <summary><b>{displayTicker(member.symbol)} {member.name}</b><span>{reason?.title ?? '這次未提供原因資料'}</span></summary>
            <p>比較上次開啟基準 {visit.previousDate ?? '—'} 與本次 {visit.list.history.at(-1)?.date ?? '—'}；以下為本次條件數值，不推測先前未保存的指標變化。</p>
            <ul>{reason?.conditions.map((condition, index) => <li key={`${index}:${condition.label}`}><b>{condition.passed === null ? '資料不足' : condition.passed ? '符合' : '未通過'} · {condition.label}</b><span>{condition.value}</span></li>)}</ul>
            <button type="button" className="ghost-button" onClick={() => onOpen(member, kind)}>開啟個股圖表</button>
          </details>;
        })}
        {visit[kind].length > limit && <button type="button" className="ghost-button" onClick={() => setLimit(value => value + 20)}>再看更多原因（{limit}／{visit[kind].length}）</button>}
      </>}
    </section>)}
  </div>;
}
