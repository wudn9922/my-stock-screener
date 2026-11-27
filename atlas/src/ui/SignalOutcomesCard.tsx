import { useEffect, useState } from 'react';
import { fetchSignalOutcomes, type SignalOutcomes } from '../report/signalOutcomes';
import type { UniverseMarketKey } from '../report/universe';
import './breakouts.css';
import './signal-outcomes.css';

const TYPES = ['ambush-v1', 'daily-scan-v1'] as const;
const NAMES = { 'ambush-v1': '全市場潛伏（Atlas 條件）', 'daily-scan-v1': '每日公開掃描清單' };
const HORIZONS = ['1', '5', '20'] as const;
const MONTHS = ['1', '3', '6'] as const;
const pct = (value: number | null) => value === null ? '—' : `${value.toFixed(2)}%`;

export function SignalOutcomesCard({ market }: { market: UniverseMarketKey }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<SignalOutcomes | null>(null);
  const [error, setError] = useState('');
  const [months, setMonths] = useState<(typeof MONTHS)[number]>('1');
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    void fetchSignalOutcomes(abort.signal).then(value => {
      if (!abort.signal.aborted) { setData(value); setError(''); }
    }).catch((reason: unknown) => {
      if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : '訊號統計暫時無法取得。');
    });
    return () => abort.abort();
  }, [open]);
  const entry = data?.markets[market];
  return <details className="card breakout-card signal-outcomes" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>訊號事後表現</summary>
    <div className="bo-body">
      <p className="bo-note">公開全市場訊號，與目前個人篩選條件無關。每檔每交易日各算一筆；以訊號日收盤至後續收盤的分割調整價格計算，不含股息及交易成本。</p>
      {error ? <p role="status">{error}</p> : !data ? <p role="status">載入中…</p> : !entry ? <p role="status">此市場尚未開始累積。</p> : <>
        <p className="bo-note">行情觀察至 {entry.observedThrough} · 訊號窗口基準 {entry.asOf} · 大盤：{market === 'TW' ? '加權指數' : 'S&P 500'}</p>
        <div className="signal-window" role="group" aria-label="訊號期間">
          {MONTHS.map(m => <button key={m} type="button" aria-pressed={months === m} onClick={() => setMonths(m)}>近 {m} 月</button>)}
        </div>
        {TYPES.map(kind => {
          const signal = entry.signals[kind];
          if (!signal) return <p key={kind} className="bo-note">{NAMES[kind]}：尚無可確認的訊號日期。</p>;
          const result = signal.windows[months];
          return <section key={kind} className="signal-cohort" aria-label={NAMES[kind]}>
            <h3>{NAMES[kind]}</h3>
            <p className="bo-note">自 {signal.startDate} 開始累積 · 最近紀錄 {signal.lastSignalDate} · {signal.capturedSessions} 個已記錄交易日</p>
            <p className="bo-ratio"><strong>{result.samples.toLocaleString()}</strong> 筆訊號</p>
            {!!result.unavailable && <p className="bo-note">另有 {result.unavailable.toLocaleString()} 筆因停牌、零成交或缺少 K 線而未計入。</p>}
            {HORIZONS.map(n => {
              const h = result.horizons[n];
              const insufficient = h.insufficient || h.mature < data.minSample;
              return <div key={n} className="bo-row signal-horizon" aria-label={`T+${n}`}>
                <div className="bo-row-head"><b>T+{n}</b><b className="bo-win">{insufficient ? '—' : pct(h.winRate)}<small>{insufficient ? '樣本不足' : '勝率'}</small></b></div>
                <p className="bo-note">成熟 {h.mature.toLocaleString()} · 尚未成熟 {h.pending.toLocaleString()}</p>
                <dl className="bo-metrics">
                  <div><dt>平均報酬</dt><dd>{pct(h.mean)}</dd></div>
                  <div><dt>中位數報酬</dt><dd>{pct(h.median)}</dd></div>
                  <div><dt>同期大盤平均</dt><dd>{pct(h.benchmarkMean)}</dd></div>
                </dl>
              </div>;
            })}
            <dl className="bo-metrics">
              <div><dt>20 日平均最大順風</dt><dd>{pct(result.mfe)}</dd></div>
              <div><dt>20 日平均最大逆風</dt><dd>{pct(result.mae)}</dd></div>
            </dl>
          </section>;
        })}
        <p className="bo-note">各天期獨立成熟；未到指定交易日不計勝率。大盤按相同訊號日期與天期配對平均。成熟不足 20 筆不顯示勝率；未記錄日期不視為零訊號。</p>
      </>}
    </div>
  </details>;
}
