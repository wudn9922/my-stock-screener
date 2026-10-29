import { useEffect, useState } from 'react';
import { bandAt, bandStatus, suppliedYieldBands, type BondCountry, type YieldBandParameters } from '../../rates/yieldBand';
import { yieldBandLoader } from '../../report/bondResearch';
import { fmtYield, slashDate } from './shared';

export function YieldBandCard({ country, policy, value, date }: { country: BondCountry; policy: number | null; value: number | null; date: string | null }) {
  const [model, setModel] = useState<YieldBandParameters>(suppliedYieldBands);
  const [baseline, setBaseline] = useState(true);
  const [stale, setStale] = useState(false);
  const [method, setMethod] = useState<'regression' | 'spread'>('regression');
  useEffect(() => {
    let live = true;
    void yieldBandLoader.load().then(result => {
      if (live && result.status === 'ok') { setModel(result.data); setBaseline(false); setStale(result.stale); }
    });
    return () => { live = false; };
  }, []);
  const p = model[country];
  const band = p && policy !== null ? bandAt(p, policy, model.z80, method) : null;
  const z = band && value !== null && Number.isFinite(value) ? (value - band.center) / band.sd : null;
  const low = band ? Math.min(band.lo, value ?? band.lo) : 0, high = band ? Math.max(band.hi, value ?? band.hi) : 1;
  const width = high - low || 1;
  const place = (number: number) => `${5 + (number - low) / width * 90}%`;
  return <section className="card rates-card rates-wide yield-band-card" aria-label={`${country === 'US' ? '美國' : '日本'}10年債合理區間`}>
    <header className="card-head"><h2>10 年債殖利率合理區間<small>80% 模型參考帶</small></h2>
      <label className="yield-band-method">算法<select aria-label="合理區間算法" value={method} onChange={e => setMethod(e.target.value as typeof method)}>
        <option value="regression">回歸法</option><option value="spread">利差法</option></select></label>
    </header>
    <div className="rates-card-body">
      {band ? <>
        <div className="yield-band-values"><span>下緣<b>{fmtYield(band.lo)}</b></span><span>中心<b>{fmtYield(band.center)}</b></span><span>上緣<b>{fmtYield(band.hi)}</b></span></div>
        <div className="yield-band-track" role="img" aria-label={`下緣 ${fmtYield(band.lo)}，中心 ${fmtYield(band.center)}，上緣 ${fmtYield(band.hi)}，目前 ${fmtYield(value)}`}>
          <i className="yield-band-interval" style={{ left: place(band.lo), width: `${(band.hi - band.lo) / width * 90}%` }} />
          <i className="yield-band-center" style={{ left: place(band.center) }} />
          {value !== null && <i className="yield-band-current" style={{ left: place(value) }} />}
        </div>
        <p className="yield-band-current-value">目前 {fmtYield(value)} <b>{z === null ? '無報價可比較' : bandStatus(z, model.z80)}</b>{z !== null && <small>z＝{z.toFixed(2)}</small>}</p>
      </> : <p role="status">政策利率或模型參數不足，無法計算合理區間。</p>}
      <p className="rates-muted">政策利率 {fmtYield(policy)}（{country === 'US' ? 'Fed 上限' : '日銀目標'}） · 行情日 {slashDate(date)} · {baseline || !p?.fittedAt ? '附檔基準參數' : stale ? '快取參數' : '參數更新'} {slashDate(p?.fittedAt ?? model.asof)}</p>
      <details className="rates-disclosure"><summary>合理區間算法與來源</summary><div className="rates-disclosure-body">
        <p>{method === 'regression' ? '中心＝a＋b × 政策利率；上下緣＝中心 ± 1.2816 × 殘差標準差，殘差自由度 n−2。' : '中心＝政策利率＋歷史平均利差；上下緣＝中心 ± 1.2816 × 利差樣本標準差。'}</p>
        <p>依使用者提供的 yield_band.py／yield_band.js 算法，每月以完整月份更新全樣本估計；美國使用 Fed 目標區間上限，日本使用日銀政策目標。</p>
        {p && <p>樣本 {slashDate(p.first)}–{slashDate(p.last)}，{p.n} 個月，R²＝{p.r2.toFixed(3)}。參考帶描述歷史偏離，不是未來報酬或保證範圍。</p>}
      </div></details>
    </div>
  </section>;
}
