import { HelpDisclosure } from './HelpDisclosure';
import { useEffect, useState } from 'react';
import { Gauge } from 'lucide-react';
import {
  linePath,
  loadBreadth,
  type BreadthHistory,
  type BreadthLoadResult,
  type BreadthMarket,
  type BreadthMarketKey,
  type BreadthRatio,
} from '../report/breadth';
import {
  loadBreadthSignals,
  type BreadthSignalIndex,
  type BreadthSignalReading,
  type BreadthSignalsLoadResult,
  type BreadthZone,
  type UsBreadthSignals,
  type TwBreadthSignals,
} from '../report/breadthSignals';
import { formatDateTime } from './format';
import './breadth.css';

const MARKET_NAMES: Record<BreadthMarketKey, string> = {
  tw: '台股',
  us: '美股',
};
const ZONE_LABELS: Record<BreadthZone, string> = {
  extreme_oversold: '極端超賣',
  oversold: '超賣',
  neutral: '中性',
  overbought: '超買（趨勢強）',
  extreme_overbought: '極端超買（趨勢強）',
};
const STATE_LABELS = {
  none: '無訊號',
  watch: '等待回檔',
  new: '新訊號',
  active: '持有中',
  target: '已停利',
  stop: '已停損',
  timeout: '持有到期',
};
const price = (value: number | null) =>
  value === null
    ? '—'
    : value.toLocaleString('zh-TW', { maximumFractionDigits: 2 });
const rate = (value: number | null) =>
  value === null ? '—' : `${Math.round(value * 100)}%`;
function Pct({ value }: { value: number | null }) {
  return <>{value === null ? '—' : `${value.toFixed(1)}%`}</>;
}

function BreadthRow({
  label,
  ratio,
  reading,
  bounds,
}: {
  label: string;
  ratio: BreadthRatio;
  reading?: BreadthSignalReading;
  bounds?: UsBreadthSignals['thresholds']['ma20'];
}) {
  return (
    <div className="breadth-row">
      <div className="breadth-row-head">
        <span className="breadth-row-label">{label}</span>
        {reading ? (
          <span className={`breadth-tag ${reading.zone}`}>
            {ZONE_LABELS[reading.zone]}
          </span>
        ) : null}
        <b className="breadth-pct">
          <Pct value={ratio.pct} />
        </b>
      </div>
      <div className="breadth-bar" aria-hidden="true">
        <i
          style={{
            width: `${ratio.pct === null ? 0 : Math.min(100, Math.max(0, ratio.pct))}%`,
          }}
        />
      </div>
      <div className="breadth-row-foot">
        {ratio.total > 0
          ? `${ratio.above.toLocaleString('zh-TW')} / ${ratio.total.toLocaleString('zh-TW')} 檔`
          : '沒有足夠的日 K 可計算'}
        {reading ? (
          <span> · 歷史第 {reading.percentile.toFixed(1)} 百分位</span>
        ) : null}
      </div>
      {bounds ? <p className="breadth-foot breadth-bounds">下限 P10 {bounds.p10.toFixed(1)}% · 上限 P90 {bounds.p90.toFixed(1)}%</p> : null}
    </div>
  );
}

function BreadthChart({
  history,
  signals,
}: {
  history: BreadthHistory;
  signals?: UsBreadthSignals;
}) {
  const [symbol, setSymbol] = useState('');
  const selected =
    signals?.lowSignal.indices.find((index) => index.symbol === symbol) ??
    signals?.lowSignal.indices[0];
  if (history.dates.length < 2)
    return <p className="muted-text">歷史資料不足，暫時無法畫出走勢。</p>;
  const latest20 =
    [...history.ma20Pct].reverse().find((value) => value !== null) ?? null;
  const latest60 =
    [...history.ma60Pct].reverse().find((value) => value !== null) ?? null;
  return (
    <figure className="breadth-chart">
      {signals && selected ? (
        <label className="breadth-marker-select">
          歷史訊號
          <select
            aria-label="歷史訊號指數"
            value={selected.symbol}
            onChange={(event) => setSymbol(event.target.value)}
          >
            {signals.lowSignal.indices.map((index) => (
              <option key={index.symbol} value={index.symbol}>
                {index.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
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
          <line
            className="breadth-ref"
            x1="0"
            x2="300"
            y1="50"
            y2="50"
            vectorEffect="non-scaling-stroke"
          />
          {signals
            ? (['ma20', 'ma60'] as const).flatMap((ma) =>
                (['p10', 'p90'] as const).map((p) => {
                  const value = signals.thresholds[ma][p];
                  return (
                    <line
                      key={`${ma}-${p}`}
                      className={`breadth-threshold ${ma}`}
                      x1="0"
                      x2="300"
                      y1={100 - value}
                      y2={100 - value}
                      vectorEffect="non-scaling-stroke"
                    >
                      <title>{`${ma.toUpperCase()} ${p.toUpperCase()}：${value}%`}</title>
                    </line>
                  );
                }),
              )
            : null}
          <path
            className="breadth-line ma60"
            d={linePath(history.ma60Pct)}
            vectorEffect="non-scaling-stroke"
          />
          <path
            className="breadth-line ma20"
            d={linePath(history.ma20Pct)}
            vectorEffect="non-scaling-stroke"
          />
          {selected?.markers.map((day) => {
            const i = history.dates.indexOf(day),
              value = history.ma20Pct[i];
            return i >= 0 && value !== null && value !== undefined ? (
              <circle
                key={day}
                className="breadth-signal-marker"
                data-signal-date={day}
                cx={(i * 300) / (history.dates.length - 1)}
                cy={100 - Math.min(100, Math.max(0, value))}
                r="3"
              >
                <title>{`${selected.name} 抓低點訊號 ${day}`}</title>
              </circle>
            ) : null;
          })}
        </svg>
      </div>
      <figcaption className="breadth-chart-axis">
        <span>{formatDateTime(history.dates[0]!, false)}</span>
        <span>{formatDateTime(history.dates.at(-1)!, false)}</span>
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
      {signals ? (
        <p className="breadth-foot">
          虛線：各均線歷史 P10／P90 門檻；圓點：{selected?.name ?? '所選指數'}
          訊號日。
        </p>
      ) : null}
    </figure>
  );
}

function IndexSignal({ index }: { index: BreadthSignalIndex }) {
  const { trade, stats } = index;
  return (
    <article
      className="breadth-index-signal"
      aria-label={`${index.name}抓低點訊號`}
    >
      <div className="breadth-signal-head">
        <b>{index.name}</b>
        <span className={`breadth-tag signal-${index.state}`}>
          {STATE_LABELS[index.state]}
        </span>
      </div>
      {index.state === 'watch' ? (
        <p className="breadth-foot">
          指數收盤跌破 {price(index.triggerPrice)} 即觸發（寬度須仍超賣）。
        </p>
      ) : null}
      {trade ? (
        <>
          {index.state === 'new' ? (
            <p className="breadth-foot">
              下一個交易日開盤進場；以下依訊號日收盤估算，實際停損停利隨進場價調整。
            </p>
          ) : null}
          <dl className="breadth-trade-plan">
            <div>
              <dt>{index.state === 'new' ? '進場參考' : '進場'}</dt>
              <dd>{price(trade.entry ?? trade.entryRef)}</dd>
            </div>
            <div>
              <dt>{index.state === 'new' ? '停損估計' : '停損'}</dt>
              <dd>{price(trade.stop)}</dd>
            </div>
            <div>
              <dt>{index.state === 'new' ? '停利估計' : '停利'}</dt>
              <dd>{price(trade.target)}</dd>
            </div>
          </dl>
          <p className="breadth-foot">
            訊號 {trade.signalDate} · 持有 {trade.daysHeld ?? '—'}／
            {trade.maxHold ?? '—'} 個交易日 · 預估到期{' '}
            {trade.expires ??
              (index.state === 'new' ? '待進場後確認' : '未提供')}
          </p>
          {trade.exitDate ? (
            <p className="breadth-foot">
              出場 {trade.exitDate} · {price(trade.exit)} · 報酬{' '}
              {rate(trade.return)}
            </p>
          ) : null}
        </>
      ) : null}
      <p className="breadth-signal-stats">
        命中率 {rate(stats.hitRate)}（隨便進場 {rate(stats.baseRate)}） ·{' '}
        {stats.trades ?? '—'} 筆
        {['^RUT', '^SOX'].includes(index.symbol) &&
        stats.hitRate !== null &&
        stats.hitRate < 0.6 ? (
          <span className="breadth-low-confidence">參考性較低</span>
        ) : null}
      </p>
    </article>
  );
}
function LowSignals({ signals }: { signals: UsBreadthSignals }) {
  const indices = signals.lowSignal.indices;
  if (!indices.length) return null;
  const rows = indices.map((index) => (
    <IndexSignal key={index.symbol} index={index} />
  ));
  return indices.every((index) => index.state === 'none') ? (
    <HelpDisclosure label="抓低點訊號 · 目前無訊號">{rows}</HelpDisclosure>
  ) : (
    <section className="breadth-low-signals" aria-label="抓低點訊號">
      <h3>抓低點訊號</h3>
      {rows}
    </section>
  );
}

/** Rendering stays independent of optional signal loading. */
export function BreadthBody({
  data,
  signals,
  twSignals,
}: {
  data: BreadthMarket;
  signals?: UsBreadthSignals;
  twSignals?: TwBreadthSignals;
}) {
  const { all, twse, tpex } = data.segments;
  return (
    <>
      <BreadthRow
        label="站上 MA20"
        ratio={all.ma20}
        reading={signals?.readings.ma20}
        bounds={signals?.thresholds.ma20}
      />
      <BreadthRow
        label="站上 MA60"
        ratio={all.ma60}
        reading={signals?.readings.ma60}
        bounds={signals?.thresholds.ma60}
      />
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
            {(
              [
                ['上市', twse],
                ['上櫃', tpex],
              ] as const
            ).map(([label, segment]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{segment.count.toLocaleString('zh-TW')}</td>
                <td>
                  <Pct value={segment.ma20.pct} />
                </td>
                <td>
                  <Pct value={segment.ma60.pct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <BreadthChart
        history={
          signals?.history && signals.history.dates.length >= 2
            ? signals.history
            : data.history
        }
        signals={signals}
      />
      {twSignals ? (
        <HelpDisclosure label="超買超賣訊號不適用">
          {twSignals.reason}
        </HelpDisclosure>
      ) : null}
      {signals ? <LowSignals signals={signals} /> : null}
      {signals?.overbought?.active ? (
        <div className="breadth-overbought">
          <p>{signals.overbought.note ?? '趨勢強勢，超買不是賣出訊號。'}</p>
          <p className="breadth-foot">
            超買後先跌 {rate(signals.overbought.dropFirstRate)} vs 平常{' '}
            {rate(signals.overbought.baseRate)}
          </p>
        </div>
      ) : null}
      <HelpDisclosure label="樣本與歷史說明">
        {data.universe}，當日有成交 {all.count.toLocaleString('zh-TW')}{' '}
        檔。歷史走勢以目前名單回推，含存活者偏差。
        {data.scope === 'full' && data.coverage ? (
          <>
            完整名單 {data.universeCount?.toLocaleString('zh-TW')} 檔；快取缺漏{' '}
            {data.coverage.missing} 檔，當日未成交／停更{' '}
            {data.coverage.notTraded}{' '}
            檔。每條均線只以有足夠歷史且當日成交的股票為分母，缺資料不當成跌破。
          </>
        ) : null}
        {signals ? (
          <>
            <p>{signals.lowSignal.rule}</p>
            <p>
              命中率＝進場後先碰到停利的比例；同一天碰到停損及停利視為停損。隔天開盤進場，最多持有
              60 個交易日。
              未來到期日以工作日粗估，未扣除國定假日。
            </p>
            <p>
              校準樣本期間：{signals.calibration?.sample?.start ?? '未提供'} 至{' '}
              {signals.calibration?.sample?.end ?? '未提供'}
              ；以目前名單回推，含存活者偏差。歷史結果不保證未來表現，並非投資建議。
            </p>
            {signals.lowSignal.indices.map((index) => (
              <p key={index.symbol}>
                {index.name}：2016 年前／後命中率{' '}
                {rate(index.stats.hitRateEarly)}／
                {rate(index.stats.hitRateLate)}
              </p>
            ))}
          </>
        ) : null}
      </HelpDisclosure>
    </>
  );
}

export function BreadthCard({ market }: { market: BreadthMarketKey }) {
  const [result, setResult] = useState<BreadthLoadResult | null>(null);
  const [signalResult, setSignalResult] =
    useState<BreadthSignalsLoadResult | null>(null);
  useEffect(() => {
    let active = true;
    void loadBreadth().then((next) => {
      if (active) setResult(next);
    });
    void loadBreadthSignals().then((next) => {
      if (active) setSignalResult(next);
    });
    return () => {
      active = false;
    };
  }, []);
  const name = MARKET_NAMES[market];
  const data =
    result?.status === 'ok' ? result.data.markets[market] : undefined;
  const usSignals =
    signalResult?.status === 'ok' ? signalResult.data.markets.us : undefined;
  // Daily failures retain an older file: never attach its plan to a newer breadth date.
  const signals =
    market === 'us' && usSignals?.asOf === data?.asOf ? usSignals : undefined;
  const twSignals =
    market === 'tw' && signalResult?.status === 'ok'
      ? signalResult.data.markets.tw
      : undefined;
  return (
    <section
      className="card breadth-card"
      aria-label={`${name}市場寬度`}
      aria-busy={result === null}
    >
      <header className="card-head">
        <h2>
          <Gauge size={16} aria-hidden="true" />
          市場寬度
          <small>
            {name}
            {data?.scope === 'full' ? '全市場' : ''}
          </small>
        </h2>
        {data?.asOf ? (
          <span className="breadth-asof">
            資料日 {formatDateTime(data.asOf, false)}
          </span>
        ) : null}
      </header>
      <div className="breadth-body">
        {result === null ? (
          <p className="muted-text" role="status">
            載入中…
          </p>
        ) : data ? (
          <BreadthBody data={data} signals={signals} twSignals={twSignals} />
        ) : (
          <p className="muted-text" role="status">
            {result.status === 'ok'
              ? `${name}的市場寬度尚未產生。`
              : result.message}
          </p>
        )}
      </div>
    </section>
  );
}
