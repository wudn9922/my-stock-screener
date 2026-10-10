import { useEffect, useState } from 'react';
import { Gauge } from 'lucide-react';
import {
  breadthTone,
  linePath,
  loadBreadth,
  type BreadthHistory,
  type BreadthLoadResult,
  type BreadthMarket,
  type BreadthMarketKey,
  type BreadthRatio,
} from '../report/breadth';
import { formatDateTime } from './format';
import './breadth.css';

const MARKET_NAMES: Record<BreadthMarketKey, string> = { tw: '台股', us: '美股' };
const TONE_LABELS = { weak: '偏弱／超賣區', hot: '偏熱' } as const;

function Pct({ value }: { value: number | null }) {
  return <>{value === null ? '—' : `${value.toFixed(1)}%`}</>;
}

function BreadthRow({ label, ratio }: { label: string; ratio: BreadthRatio }) {
  const tone = breadthTone(ratio.pct);
  const width = ratio.pct === null ? 0 : Math.min(100, Math.max(0, ratio.pct));
  return (
    <div className="breadth-row">
      <div className="breadth-row-head">
        <span className="breadth-row-label">{label}</span>
        {tone === 'weak' || tone === 'hot' ? <span className={`breadth-tag ${tone}`}>{TONE_LABELS[tone]}</span> : null}
        <b className="breadth-pct">
          <Pct value={ratio.pct} />
        </b>
      </div>
      <div className="breadth-bar" aria-hidden="true">
        <i style={{ width: `${width}%` }} />
      </div>
      <div className="breadth-row-foot">
        {ratio.total > 0 ? `${ratio.above.toLocaleString('zh-TW')} / ${ratio.total.toLocaleString('zh-TW')} 檔` : '沒有足夠的日 K 可計算'}
      </div>
    </div>
  );
}

function BreadthChart({ history }: { history: BreadthHistory }) {
  if (history.dates.length < 2) return <p className="muted-text">歷史資料不足，暫時無法畫出走勢。</p>;
  const first = history.dates[0]!;
  const last = history.dates[history.dates.length - 1]!;
  const latest20 = [...history.ma20Pct].reverse().find((value) => value !== null) ?? null;
  const latest60 = [...history.ma60Pct].reverse().find((value) => value !== null) ?? null;
  return (
    <figure className="breadth-chart">
      <div className="breadth-chart-plot">
        <span className="breadth-axis top">100</span>
        <span className="breadth-axis mid">50</span>
        <span className="breadth-axis bottom">0</span>
        <svg
          viewBox="0 0 300 100"
          preserveAspectRatio="none"
          role="img"
          aria-label={`近 ${history.dates.length} 個交易日站上 MA20 與 MA60 的比例走勢，最新 MA20 ${latest20 ?? '—'}%、MA60 ${latest60 ?? '—'}%`}
        >
          <line className="breadth-ref" x1="0" x2="300" y1="50" y2="50" vectorEffect="non-scaling-stroke" />
          <path className="breadth-line ma60" d={linePath(history.ma60Pct)} vectorEffect="non-scaling-stroke" />
          <path className="breadth-line ma20" d={linePath(history.ma20Pct)} vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
      <figcaption className="breadth-chart-axis">
        <span>{formatDateTime(first, false)}</span>
        <span>{formatDateTime(last, false)}</span>
      </figcaption>
      <ul className="breadth-legend">
        <li>
          <i className="ma20" aria-hidden="true" />
          站上 MA20
        </li>
        <li>
          <i className="ma60" aria-hidden="true" />
          站上 MA60
        </li>
      </ul>
    </figure>
  );
}

function BreadthBody({ data }: { data: BreadthMarket }) {
  const { all, twse, tpex } = data.segments;
  return (
    <>
      <BreadthRow label="站上 MA20" ratio={all.ma20} />
      <BreadthRow label="站上 MA60" ratio={all.ma60} />
      {twse && tpex ? (
        <table className="breadth-seg">
          <thead>
            <tr>
              <th scope="col">市場</th>
              <th scope="col">檔數</th>
              <th scope="col">MA20</th>
              <th scope="col">MA60</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">上市</th>
              <td>{twse.count.toLocaleString('zh-TW')}</td>
              <td>
                <Pct value={twse.ma20.pct} />
              </td>
              <td>
                <Pct value={twse.ma60.pct} />
              </td>
            </tr>
            <tr>
              <th scope="row">上櫃</th>
              <td>{tpex.count.toLocaleString('zh-TW')}</td>
              <td>
                <Pct value={tpex.ma20.pct} />
              </td>
              <td>
                <Pct value={tpex.ma60.pct} />
              </td>
            </tr>
          </tbody>
        </table>
      ) : null}
      <BreadthChart history={data.history} />
      <p className="breadth-foot">
        {data.universe}，當日有成交 {all.count.toLocaleString('zh-TW')} 檔。歷史走勢以目前名單回推，含存活者偏差。
      </p>
    </>
  );
}

/** Share of stocks above their 20- and 60-day averages, from the daily bars of the last report run. */
export function BreadthCard({ market }: { market: BreadthMarketKey }) {
  const [result, setResult] = useState<BreadthLoadResult | null>(null);
  useEffect(() => {
    let active = true;
    void loadBreadth().then((next) => {
      if (active) setResult(next);
    });
    return () => {
      active = false;
    };
  }, []);

  const name = MARKET_NAMES[market];
  const data = result?.status === 'ok' ? result.data.markets[market] : undefined;
  return (
    <section className="card breadth-card" aria-label={`${name}市場寬度`} aria-busy={result === null}>
      <header className="card-head">
        <h2>
          <Gauge size={16} aria-hidden="true" />
          市場寬度
          <small>{name}</small>
        </h2>
        {data?.asOf ? <span className="breadth-asof">資料日 {formatDateTime(data.asOf, false)}</span> : null}
      </header>
      <div className="breadth-body">
        {result === null ? (
          <p className="muted-text" role="status">
            載入中…
          </p>
        ) : data ? (
          <BreadthBody data={data} />
        ) : (
          <p className="muted-text" role="status">
            {result.status === 'ok' ? `${name}的市場寬度尚未產生。` : result.message}
          </p>
        )}
      </div>
    </section>
  );
}
