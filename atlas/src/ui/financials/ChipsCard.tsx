import { useEffect, useState } from 'react';
import { FinancialNote } from './FinancialNote';
import { Globe2, Users } from 'lucide-react';
import {
  chipsForSymbol,
  formatCount,
  formatCountChange,
  formatPoints,
  isForeignStale,
  isHoldersStale,
  loadTwChips,
  monthDay,
  sparkline,
  type ChipSource,
  type ForeignChips,
  type StockChips,
  type TwChipsLoadResult,
} from '../../report/twChips';
import { formatDateTime, toneClass } from '../format';
import '../chips.css';
import { LargeHolders } from '../research/LargeHolders';

const FOREIGN_SOURCE_NAMES = { twse: '證交所', tpex: '櫃買中心' } as const;

function Sparkline({ values, dates, label, unit, digits }: { values: readonly (number | null)[]; dates: readonly string[]; label: string; unit: string; digits: number }) {
  const line = sparkline(values);
  if (!line) return <p className="chip-spark-empty">歷史資料累積中，目前不足以畫出走勢。</p>;
  const shown = (value: number) => `${digits ? value.toFixed(digits) : Math.round(value).toLocaleString('en-US')}${unit}`;
  return (
    <figure className="chip-spark">
      <svg viewBox="0 0 300 64" preserveAspectRatio="none" role="img" aria-label={`${label}，區間 ${shown(line.min)} 到 ${shown(line.max)}，共 ${line.points} 個資料點`}>
        <path className="chip-spark-line" d={line.d} vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption>
        <span>{formatDateTime(dates[0] ?? null, false)}</span>
        <span className="chip-spark-range">
          區間 {shown(line.min)} – {shown(line.max)}
        </span>
        <span>{formatDateTime(dates[dates.length - 1] ?? null, false)}</span>
      </figcaption>
    </figure>
  );
}

function sourceNote(source: ChipSource | null) {
  if (!source) return null;
  if (source.status === 'stale') return <p className="chip-warn">最近一次更新失敗，這裡顯示的是先前累積的資料。</p>;
  return null;
}

function ForeignCard({ chips, symbol }: { chips: StockChips; symbol: string }) {
  const foreign: ForeignChips | null = chips.foreign;
  const sourceName = FOREIGN_SOURCE_NAMES[chips.foreignSourceKey];
  return (
    <article className="card chip-card" data-chip="foreign" aria-label="外資持股比例">
      <header className="chip-head">
        <h3>
          <Globe2 size={16} aria-hidden="true" />
          外資持股比例
        </h3>
        <span className="chip-source">{sourceName}</span>
      </header>
      {foreign ? (
        <div className="chip-body">
          <div className="fin-headline">
            <b className="fin-big">{foreign.pct.toFixed(2)}</b>
            <span className="fin-big-unit">%</span>
            <span className="fin-period">資料日 {formatDateTime(foreign.asOf, false)}</span>
          </div>
          <div className="fin-chips">
            <span className={`fin-chip ${toneClass(foreign.d1)}`} data-chip-change="d1">
              較前一交易日{foreign.d1Date ? `（${monthDay(foreign.d1Date)}）` : ''} {formatPoints(foreign.d1)}
            </span>
            <span className={`fin-chip ${toneClass(foreign.m1)}`} data-chip-change="m1">
              {foreign.m1 === null ? '近 1 月：歷史尚不足' : `近 1 月（較 ${monthDay(foreign.m1Date)}） ${formatPoints(foreign.m1)}`}
            </span>
            {isForeignStale(foreign.asOf) && <span className="fin-chip warn">資料較舊（超過 5 個交易日）</span>}
          </div>
          {sourceNote(chips.foreignSource)}
          <p className="fin-chart-title">近 {foreign.series.filter((value) => value !== null).length} 個交易日</p>
          <Sparkline values={foreign.series} dates={foreign.dates} label={`${symbol} 外資持股比例走勢`} unit="%" digits={2} />
          <FinancialNote className="chip-def" label="外資持股口徑"><p>
            口徑：全體外資及陸資持股比率（持有股數 ÷ 發行股數），{sourceName}逐日公布；本站每個交易日收錄一次、逐日累積，歷史長度以實際累積為準。變動以百分點計，僅呈現數字，不代表買賣訊號。
          </p></FinancialNote>
        </div>
      ) : (
        <p className="chip-empty" role="status">
          {chips.foreignSource?.status === 'unavailable' && chips.foreignSourceKey === 'tpex'
            ? '櫃買中心的外資持股資料暫時無法取得；股東人數與其他資料不受影響。'
            : chips.foreignSource?.status === 'unavailable'
              ? '證交所的外資持股資料暫時無法取得。'
              : '這檔股票目前沒有外資持股資料（新上市、剛加入名單，或來源未列入）。'}
        </p>
      )}
    </article>
  );
}

function HoldersCard({ chips, symbol }: { chips: StockChips; symbol: string }) {
  const holders = chips.holders;
  return (
    <article className="card chip-card" data-chip="holders" aria-label="總股東人數">
      <header className="chip-head">
        <h3>
          <Users size={16} aria-hidden="true" />
          總股東人數
        </h3>
        <span className="chip-source">集保結算所</span>
      </header>
      {holders ? (
        <div className="chip-body">
          <div className="fin-headline">
            <b className="fin-big">{formatCount(holders.count)}</b>
            <span className="fin-big-unit">人</span>
            <span className="fin-period">資料日 {formatDateTime(holders.asOf, false)}</span>
          </div>
          <div className="fin-chips">
            <span className="fin-chip" data-chip-change="wow">
              較上週{holders.wowDate ? `（${monthDay(holders.wowDate)}）` : ''} {formatCountChange(holders.wow, holders.count)}
            </span>
            {isHoldersStale(holders.asOf) && <span className="fin-chip warn">資料較舊（超過 10 天）</span>}
          </div>
          {sourceNote(chips.holdersSource)}
          <p className="fin-chart-title">近 {holders.series.filter((value) => value !== null).length} 週</p>
          <Sparkline values={holders.series} dates={holders.dates} label={`${symbol} 總股東人數走勢`} unit=" 人" digits={0} />
          <LargeHolders rows={chips.largeHolders ?? []} />
          <FinancialNote className="chip-def" label="股東人數口徑"><p>
            口徑：集保戶股權分散表第 17 級（合計）人數，每週公布一次，不含第 16 級差異數調整；本站逐週累積，歷史長度以實際累積為準。僅呈現人數與走勢，不代表買賣訊號。
          </p></FinancialNote>
        </div>
      ) : (
        <p className="chip-empty" role="status">
          {chips.holdersSource?.status === 'unavailable' ? '集保結算所的股東人數資料暫時無法取得。' : '這檔股票目前沒有股東人數資料（新上市、剛加入名單，或來源未列入）。'}
        </p>
      )}
    </article>
  );
}

/** 籌碼 section of the financials page for a Taiwan stock: foreign ownership ratio and total shareholder count. */
export function ChipsSection({ symbol }: { symbol: string }) {
  const [result, setResult] = useState<TwChipsLoadResult | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    void loadTwChips(attempt > 0).then((next) => {
      if (active) setResult(next);
    });
    return () => {
      active = false;
    };
  }, [attempt]);

  const chips = result?.status === 'ok' ? chipsForSymbol(result.data, symbol) : null;
  return (
    <section className="fin-chips-section" aria-label="籌碼" aria-busy={result === null}>
      <div className="chip-section-head">
        <h2>籌碼</h2>
        <FinancialNote label="籌碼說明"><p>外資持股比例與總股東人數；官方公開資料，僅呈現數字與走勢。</p></FinancialNote>
      </div>
      {result === null ? (
        <p className="chip-empty" role="status">
          載入籌碼資料中…
        </p>
      ) : !chips ? (
        <div className="card chip-card chip-card-wide" role="status">
          <p className="chip-empty">
            {result.status === 'ok' ? '' : result.message}
            {result.status === 'error' && (
              <button type="button" className="chip-retry" onClick={() => setAttempt((n) => n + 1)}>
                重試
              </button>
            )}
          </p>
        </div>
      ) : !chips.foreign && !chips.holders && chips.foreignSource?.status !== 'unavailable' && chips.holdersSource?.status !== 'unavailable' ? (
        <div className="card chip-card chip-card-wide" role="status">
          <p className="chip-empty">這檔股票目前沒有籌碼資料（新上市、剛加入名單，或不在更新名單內；ETF 與指數不收錄）。</p>
        </div>
      ) : (
        <>
          <div className="chip-grid">
            <ForeignCard chips={chips} symbol={symbol} />
            <HoldersCard chips={chips} symbol={symbol} />
          </div>
          {result.status === 'ok' && result.data.generatedAt && <p className="chip-generated">檔案更新時間 {formatDateTime(result.data.generatedAt)}</p>}
        </>
      )}
    </section>
  );
}
