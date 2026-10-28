import { useEffect, useRef, useState } from 'react';
import {
  detectMapping,
  makeTable,
  parseText,
  previewErrors,
  previewHoldings,
  previewSnapshot,
  previewTransactions,
  readHoldingFile,
  validatePreviewRow,
  type ImportPreview,
  type ImportSymbol,
  type ImportTable,
  type PreviewRow,
} from './import';
import { HOLDINGS_LIMITS, type ColumnMapping, type Holding, type HoldingAccount } from './model';

interface Props {
  account: HoldingAccount;
  directory: ImportSymbol[];
  initial?: Holding[];
  onCancel: () => void;
  onSave: (account: HoldingAccount, holdings: Holding[]) => Promise<void>;
}
export function ImportHoldings({ account, directory, initial, onCancel, onSave }: Props) {
  const [text, setText] = useState(''),
    [table, setTable] = useState<ImportTable | null>(null);
  const [mapping, setMapping] = useState<ColumnMapping | null>(null),
    [step, setStep] = useState<'input' | 'mapping' | 'preview'>(initial ? 'preview' : 'input');
  const [preview, setPreview] = useState<ImportPreview | null>(
    initial
      ? {
          rows: initial.map((h, i) => ({
            line: i + 1,
            symbol: h.symbol,
            quantity: h.quantity,
            averageCost: h.averageCost,
            errors: [],
            skip: false,
          })),
          skipped: [],
          warnings: [],
        }
      : null,
  );
  const [source, setSource] = useState<'import' | 'paste' | 'manual'>(initial ? 'manual' : 'paste');
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [ack, setAck] = useState(false),
    [emptyAck, setEmptyAck] = useState(false);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const accept = (next: ImportTable) => {
    if (!active.current) return;
    if (!next.transactions && next.rows.length > HOLDINGS_LIMITS.perAccount) {
      setError('單一帳戶最多 1,000 檔持股，請先縮小匯入範圍。');
      return;
    }
    setTable(next);
    setAck(false);
    setEmptyAck(false);
    if (next.transactions && account.kind === 'us-broker') {
      const result = previewTransactions(next, account, directory);
      if (result.rows.length > HOLDINGS_LIMITS.perAccount) {
        setError('預覽超過 1,000 列，請先核對並縮小匯入範圍。');
        return;
      }
      setPreview(result);
      setStep('preview');
      return;
    }
    const detected = detectMapping(next, account.mapping);
    setMapping(
      detected ?? {
        fingerprint: next.fingerprint,
        symbol: 0,
        quantity: Math.min(1, next.headers.length - 1),
        unit: 'shares',
      },
    );
    if (detected) {
      setPreview(previewSnapshot(next, detected, account, directory));
      setStep('preview');
    } else setStep('mapping');
  };
  const run = async (task: () => Promise<void>) => {
    setError('');
    setBusy(true);
    try {
      await task();
    } catch {
      if (active.current) setError('操作未完成。請檢查檔案格式與欄位；若已按儲存，請先重新載入確認結果後再試。');
    } finally {
      if (active.current) setBusy(false);
    }
  };
  const edit = (i: number, key: 'symbol' | 'quantity' | 'averageCost', value: string) =>
    setPreview((previous) =>
      previous
        ? {
            ...previous,
            rows: previous.rows.map((row, index) =>
              index === i ? validatePreviewRow({ ...row, [key]: value }, account, directory) : row,
            ),
          }
        : null,
    );
  const included = preview?.rows.filter((row) => !row.skip) ?? [];
  const invalid = preview ? previewErrors(preview.rows) : true;
  const duplicates = new Set(included.map((row) => row.symbol)).size !== included.length;
  const columns = (key: 'symbol' | 'quantity' | 'average' | 'total', title: string, required = false) => (
    <label>
      {title}
      <select
        aria-label={title}
        value={mapping?.[key] ?? -1}
        onChange={(event) =>
          setMapping((previous) => {
            if (!previous) return previous;
            const next = { ...previous };
            if (event.target.value === '-1') delete next[key as 'average' | 'total'];
            else next[key] = Number(event.target.value);
            return next;
          })
        }
      >
        {!required && <option value={-1}>未提供（可在預覽補正）</option>}
        {table?.headers.map((header, index) => (
          <option key={index} value={index}>
            {index + 1}. {header || '未命名欄位'}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <section className="console-card holdings-import" aria-label="匯入持股">
      <div className="holdings-toolbar">
        <h3>
          {initial ? '修正持股' : '匯入持股'} · {account.name}
        </h3>
        <button type="button" className="ghost-button" disabled={busy} onClick={onCancel}>
          取消
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {step === 'input' && (
        <>
          <p>上傳或貼上整份庫存，確認後取代此帳戶持股。原始檔只在瀏覽器解析。</p>
          <label>
            上傳 CSV／XLSX
            <input
              type="file"
              accept=".csv,.xlsx"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) {
                  setSource('import');
                  void run(async () => {
                    accept(await readHoldingFile(file));
                  });
                }
              }}
            />
          </label>
          <label>
            貼上庫存表格
            <textarea
              aria-label="貼上庫存表格"
              rows={6}
              value={text}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setText(event.target.value)}
              placeholder="貼上含欄名的表格，例如：代號、股數、平均成本"
            />
          </label>
          <button
            className="primary-button"
            type="button"
            disabled={busy || !text.trim()}
            onClick={() => {
              setSource('paste');
              void run(async () => {
                accept(await makeTable(parseText(text)));
                setText('');
              });
            }}
          >
            辨識貼上內容
          </button>
          <details>
            <summary>各券商匯入方式</summary>
            <p>
              國泰、永豐、口袋可貼上庫存表格；凱基匯出 Excel 後上傳 XLSX（舊版 XLS 請另存 XLSX
              或貼上）。若只有張數，請在欄位對應選「張」，成本缺漏可在預覽補正。
            </p>
            <p>
              Firstrade 的 Tax Center CSV
              是交易紀錄：會以平均成本法重建持股，請包含持有期間全部買賣。非交易列會列在預覽供核對。
            </p>
          </details>
        </>
      )}
      {step === 'mapping' && table && mapping && (
        <>
          <h4>欄位對應</h4>
          <p>選擇欄位後預覽，確認時會記住這個帳戶與相同欄名的對應。</p>
          <div className="holdings-fields">
            {columns('symbol', '代號欄位', true)}
            {columns('quantity', '股數欄位', true)}
            {columns('average', '均價欄位')}
            {columns('total', '總成本欄位')}
            <label>
              數量單位
              <select
                aria-label="數量單位"
                value={mapping.unit}
                onChange={(event) => setMapping({ ...mapping, unit: event.target.value as 'shares' | 'lots' })}
              >
                <option value="shares">股／幣</option>
                {account.kind === 'tw-broker' && <option value="lots">張（1 張＝1,000 股）</option>}
              </select>
            </label>
          </div>
          <div className="holdings-sample" aria-label="原始欄位預覽">
            {table.headers.map((header, i) => (
              <p key={i}>
                {header || '未命名欄位'}：{table.rows[0]?.[i] || '—'}
              </p>
            ))}
          </div>
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              const indices = [mapping.symbol, mapping.quantity, mapping.average, mapping.total].filter(
                (value) => value !== undefined,
              );
              if (new Set(indices).size !== indices.length) {
                setError('每個欄位只能指定一種用途。');
                return;
              }
              setError('');
              setPreview(previewSnapshot(table, mapping, account, directory));
              setStep('preview');
            }}
          >
            預覽解析結果
          </button>
        </>
      )}
      {step === 'preview' && preview && (
        <>
          <div className="holdings-toolbar">
            <h4>預覽確認 · {included.length} 檔</h4>
            {table && !table.transactions && (
              <button className="ghost-button" type="button" onClick={() => setStep('mapping')}>
                調整欄位對應
              </button>
            )}
          </div>
          {preview.warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
          {duplicates && (
            <p role="alert" className="holdings-error">
              同一帳戶有重複代號，請合併數量與成本，或略過重複列。
            </p>
          )}
          <div className="holdings-preview">
            {preview.rows.map((row: PreviewRow, i) => (
              <fieldset key={i} className={row.errors.length && !row.skip ? 'holdings-invalid' : ''}>
                <legend>第 {row.line} 列</legend>
                <label className="holdings-check">
                  <input
                    type="checkbox"
                    checked={row.skip}
                    onChange={(event) =>
                      setPreview({
                        ...preview,
                        rows: preview.rows.map((entry, j) =>
                          j === i ? { ...entry, skip: event.target.checked } : entry,
                        ),
                      })
                    }
                  />
                  略過此列
                </label>
                <div className="holdings-fields">
                  <label>
                    代號
                    <input
                      aria-label={`第 ${i + 1} 列代號`}
                      disabled={row.skip}
                      autoComplete="off"
                      spellCheck={false}
                      value={row.symbol}
                      onChange={(event) => edit(i, 'symbol', event.target.value)}
                    />
                  </label>
                  <label>
                    股數／幣數
                    <input
                      aria-label={`第 ${i + 1} 列股數`}
                      disabled={row.skip}
                      inputMode="decimal"
                      autoComplete="off"
                      value={row.quantity}
                      onChange={(event) => edit(i, 'quantity', event.target.value)}
                    />
                  </label>
                  <label>
                    平均成本（{account.currency}）
                    <input
                      aria-label={`第 ${i + 1} 列均價`}
                      disabled={row.skip}
                      inputMode="decimal"
                      autoComplete="off"
                      value={row.averageCost}
                      onChange={(event) => edit(i, 'averageCost', event.target.value)}
                    />
                  </label>
                </div>
                {!row.skip && row.errors.length > 0 && (
                  <p className="holdings-error" role="alert">
                    {row.errors.join('；')}
                  </p>
                )}
              </fieldset>
            ))}
          </div>
          <button
            className="ghost-button"
            type="button"
            disabled={preview.rows.length >= HOLDINGS_LIMITS.perAccount}
            onClick={() =>
              setPreview({
                ...preview,
                rows: [
                  ...preview.rows,
                  {
                    line: preview.rows.length + 1,
                    symbol: '',
                    quantity: '',
                    averageCost: '',
                    errors: ['請補齊修正資料'],
                    skip: false,
                  },
                ],
              })
            }
          >
            新增修正列
          </button>
          {preview.skipped.length > 0 && (
            <details open>
              <summary>略過 {preview.skipped.length} 筆非交易列</summary>
              <ul>
                {preview.skipped.map((row) => (
                  <li key={row.line}>
                    第 {row.line} 列：{row.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {preview.warnings.length > 0 && (
            <label className="holdings-check">
              <input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} />
              已核對券商庫存與略過的非交易列
            </label>
          )}
          {!included.length && (
            <label className="holdings-check">
              <input type="checkbox" checked={emptyAck} onChange={(event) => setEmptyAck(event.target.checked)} />
              確認清空此帳戶的所有持股
            </label>
          )}
          <button
            type="button"
            className="primary-button"
            disabled={busy || invalid || (preview.warnings.length > 0 && !ack) || (!included.length && !emptyAck)}
            onClick={() =>
              void run(async () => {
                const validMapping =
                  mapping && (mapping.average !== undefined || mapping.total !== undefined) ? mapping : account.mapping;
                const nextAccount = { ...account, ...(validMapping ? { mapping: validMapping } : {}) };
                await onSave(nextAccount, previewHoldings(preview.rows, nextAccount, source));
              })
            }
          >
            {busy ? '儲存中…' : '用這份資料取代此帳戶持股'}
          </button>
        </>
      )}
    </section>
  );
}
