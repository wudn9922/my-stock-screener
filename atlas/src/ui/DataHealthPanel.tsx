import { useRef, useState, useSyncExternalStore } from 'react';
import { dataHealth, healthAge, type HealthEntry } from '../report/dataHealth';
import type { ReportState } from '../report/useReport';
import { loadUniverse } from '../report/universe';
import { loadThemes } from '../report/themes';
import { growthSourceStatus, loadMarketGrowth } from '../fundamentals/GrowthProvider';
import { fetchRates } from '../report/rates';
import { formatDateTime } from './format';
import './research.css';

export function DataHealthPanel({ report }: { report: ReportState }) {
  const recorded = useSyncExternalStore(dataHealth.subscribe, dataHealth.getSnapshot);
  const [checking, setChecking] = useState(false);
  const started = useRef(false);
  const reportEntry: HealthEntry =
    report.status === 'ok'
      ? {
          id: 'report',
          label: '每日報告',
          status: 'ok',
          date: report.report.generatedAt ?? report.report.reportDate,
          source: '每日量化報告',
          detail: `${report.report.groups.length} 個群組`,
        }
      : { id: 'report', label: '每日報告', status: report.status, detail: report.status === 'loading' ? '載入中' : report.message };
  const check = async () => {
    started.current = true;
    setChecking(true);
    const jobs = [
      {
        id: 'universe',
        label: '全市場與技術指標',
        run: async (): Promise<HealthEntry> => {
          const result = await loadUniverse(true);
          return result.status === 'ok'
            ? {
                id: 'universe',
                label: '全市場與技術指標',
                status: 'ok',
                date: result.universe.generatedAt,
                source: '全市場資料',
                detail: Object.values(result.universe.markets)
                  .map((market) => `${market.market} ${market.items.length} 檔 · ${market.asOf ?? '日期不明'}`)
                  .join('；'),
              }
            : { id: 'universe', label: '全市場與技術指標', status: result.status, detail: result.message };
        },
      },
      ...(['us', 'tw'] as const).map((market) => ({
        id: `themes-${market}`,
        label: `${market === 'us' ? '美股' : '台股'}題材`,
        run: async (): Promise<HealthEntry> => {
          const result = await loadThemes(true, market);
          return result.status === 'ok'
            ? {
                id: `themes-${market}`,
                label: `${market === 'us' ? '美股' : '台股'}題材`,
                status: 'ok',
                date: result.data.generatedAt ?? result.data.asOf,
                source: `等權題材 · ${result.data.benchmark.label}`,
                detail: `${result.data.themes.length} 個題材 · 資料日 ${result.data.asOf ?? '未知'}`,
              }
            : { id: `themes-${market}`, label: `${market === 'us' ? '美股' : '台股'}題材`, status: result.status, detail: result.message };
        },
      })),
      ...(['TW', 'US'] as const).map((market) => ({
        id: `growth-${market}`,
        label: `${market === 'TW' ? '台股' : '美股'}成長指標`,
        run: async (): Promise<HealthEntry> => {
          const result = await loadMarketGrowth(market, true);
          const status = growthSourceStatus();
          return {
            id: `growth-${market}`,
            label: `${market === 'TW' ? '台股' : '美股'}成長指標`,
            status: result ? (status.reportFailed ? 'error' : 'ok') : 'missing',
            date: result?.generatedAt,
            source: result?.source,
            detail: result
              ? `${status.reportFailed ? '本次來源讀取失敗，沿用仍可用的資料；' : ''}${market === 'US' && status.yahooFailed ? 'Yahoo 補充來源不可用；' : ''}${result.items.size} 檔；個別缺值與涵蓋期數仍以篩選面板為準`
              : '基本面資料不可用，不能據此判定股票不符合條件',
          };
        },
      })),
      {
        id: 'rates',
        label: '美債與利率',
        run: async (): Promise<HealthEntry> => {
          const result = await fetchRates();
          return result.status === 'ok'
            ? {
                id: 'rates',
                label: '美債與利率',
                status: 'ok',
                date: result.data.generatedAt ?? result.data.asOf,
                source: result.data.sources.rates,
                detail: result.data.missing.length ? `沿用舊值：${result.data.missing.join('、')}` : `資料日 ${result.data.asOf}`,
              }
            : { id: 'rates', label: '美債與利率', status: result.status, detail: result.message };
        },
      },
    ];
    await Promise.allSettled(
      jobs.map(async (job) => {
        dataHealth.record({ id: job.id, label: job.label, status: 'loading' });
        try {
          dataHealth.record(await job.run());
        } catch {
          dataHealth.record({ id: job.id, label: job.label, status: 'error', detail: '讀取失敗，請重試' });
        }
      }),
    );
    setChecking(false);
  };
  return (
    <details
      className="data-health research-disclosure"
      onToggle={(event) => {
        if (event.currentTarget.open && !started.current) void check();
      }}
    >
      <summary>
        資料健康總覽 <span>更新時間・來源・缺漏狀態</span>
      </summary>
      <div className="research-disclosure-body">
        <button type="button" className="ghost-button" disabled={checking} onClick={() => void check()}>
          {checking ? '檢查中…' : '重新檢查'}
        </button>
        <ul className="health-entries">
          {[reportEntry, ...recorded].map((entry) => {
            const age = healthAge(entry.date);
            const state =
              entry.status === 'ok'
                ? age === null
                  ? '日期不明'
                  : age > 4
                    ? `已 ${age} 天未更新`
                    : '可讀取'
                : { loading: '檢查中', missing: '資料缺少', invalid: '格式異常', error: '讀取失敗' }[entry.status];
            return (
              <li key={entry.id}>
                <div>
                  <b>{entry.label}</b>
                  <span className={entry.status !== 'ok' || age === null || age > 4 ? 'health-warning' : ''}>{state}</span>
                </div>
                <small>
                  {entry.date ? formatDateTime(entry.date) : '更新時間未知'}
                  {entry.source ? ` · ${entry.source}` : ''}
                </small>
                {entry.detail && <p>{entry.detail}</p>}
              </li>
            );
          })}
        </ul>
        <p className="muted">
          「可讀取」表示來源格式可辨識，不代表每檔股票的所有指標都完整；超過 4
          個日曆日會提示舊資料。個股財報在開啟後列入下方，資料更新日不等於財報期末日。
        </p>
      </div>
    </details>
  );
}
