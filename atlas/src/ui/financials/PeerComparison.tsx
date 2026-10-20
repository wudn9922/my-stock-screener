import { useEffect, useState } from 'react';
import { loadFinancials, type FinancialHistory, type FinancialsLoadResult, type PeriodKind } from '../../fundamentals/FinancialHistory';
import { industryPeers, peerFigures, PEER_COLUMNS } from '../../fundamentals/peerComparison';
import { loadUniverse } from '../../report/universe';
import type { PageProps } from '../../pages/pageProps';
import { SymbolSearch } from '../SymbolSearch';
import { displayTicker, formatPercent } from '../format';

/** Kept below the existing statement cards and loaded only when expanded. */
export function PeerComparison({
  history,
  kind,
  navigate,
}: {
  history: FinancialHistory;
  kind: PeriodKind;
  navigate: PageProps['navigate'];
}) {
  const [open, setOpen] = useState(false);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, FinancialsLoadResult>>({});
  const [note, setNote] = useState('展開後依全市場產業分類尋找同業，也可自行加入股票。');
  useEffect(() => {
    if (!open) return;
    let active = true;
    void loadUniverse().then((result) => {
      if (!active) return;
      const peers = result.status === 'ok' ? industryPeers(history.symbol, result.universe.markets[history.market]?.items ?? []) : [];
      setSymbols((previous) => (previous.length ? previous : peers.map((item) => item.symbol)));
      setNote(
        peers.length ? '自動加入相同產業的公司（依市值排序）；可移除或自行加入比較對象。' : '沒有可用的同產業分類，請搜尋並加入比較對象。',
      );
    });
    return () => {
      active = false;
    };
  }, [open, history.symbol, history.market]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    void Promise.all(
      symbols.map(async (symbol) => {
        const result = await loadFinancials(symbol);
        if (active) setResults((previous) => ({ ...previous, [symbol]: result }));
      }),
    );
    return () => {
      active = false;
    };
  }, [open, symbols]);
  const rows = [history.symbol, ...symbols];
  return (
    <details className="fin-peers research-disclosure" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        同業比較 <span>展開比較成長、獲利與現金流</span>
      </summary>
      {open && (
        <div className="research-disclosure-body">
          <p className="muted">{note}</p>
          {symbols.length < 4 && (
            <SymbolSearch
              mode="dropdown"
              localSymbols={[]}
              onSelect={(result) => {
                if (result.symbol !== history.symbol)
                  setSymbols((previous) => (previous.includes(result.symbol) ? previous : [...previous, result.symbol].slice(0, 4)));
              }}
            />
          )}
          <div className="research-table-scroll">
            <table className="research-table" aria-label="同業財報比較">
              <thead>
                <tr>
                  <th>股票／期末日</th>
                  {PEER_COLUMNS.map((column) => (
                    <th key={column.id}>{column.label}</th>
                  ))}
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((symbol) => {
                  const result = results[symbol];
                  const data = symbol === history.symbol ? history : result?.status === 'ok' ? result.data : null;
                  const figures = data ? peerFigures(data, kind) : null;
                  return (
                    <tr key={symbol}>
                      <th>
                        <button
                          type="button"
                          className="text-button"
                          onClick={() => navigate({ page: 'financials', symbol, p: kind === 'quarterly' ? 'q' : 'a' })}
                        >
                          {displayTicker(symbol)}
                        </button>
                        <small>
                          {figures?.date ?? (result && result.status !== 'ok' ? result.message : '載入中…')}
                          {data ? ` · ${data.currency}` : ''}
                        </small>
                      </th>
                      {PEER_COLUMNS.map((column, index) => (
                        <td key={column.id} title={figures?.figures[index]?.reason ?? undefined}>
                          {formatPercent(figures?.figures[index]?.value ?? null)}
                        </td>
                      ))}
                      <td>
                        {symbol !== history.symbol && (
                          <button
                            type="button"
                            className="text-button"
                            aria-label={`移除比較 ${symbol}`}
                            onClick={() => setSymbols((previous) => previous.filter((entry) => entry !== symbol))}
                          >
                            移除
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted">
            目前比較{kind === 'quarterly' ? '季度' : '年度'}
            資料；各公司會計年度與最新期末日可能不同，請對照日期。缺值或金融業不適用欄位顯示「—」，滑過數值可查看原因。ROE
            沿用既有財報計算口徑。
          </p>
        </div>
      )}
    </details>
  );
}
