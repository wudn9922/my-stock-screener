import { z } from 'zod';
import type { ParameterGroup, ParameterStock } from './settingsApi';

type ParameterStorage = Pick<Storage, 'getItem' | 'setItem'>;
const KEY = 'atlas-parameter-workspace-v1';
const ma = z.number().int().min(0).max(1000);
const group = z.object({
  id: z.number().int().positive(),
  name: z.string().min(1).max(30),
  ma1: ma,
  ma2: ma,
  ma3: ma,
  ma4: ma,
});
const stock = z.object({
  id: z.number().int().positive(),
  ticker: z.string().regex(/^[A-Z0-9.^_-]{1,30}$/),
  group_id: z.string(),
  ma1: ma,
  ma2: ma,
  ma3: ma,
  ma4: ma,
});
const document = z.object({
  version: z.literal(1),
  nextId: z.number().int().positive(),
  groups: z.array(group).max(50),
  stocks: z.array(stock).max(500),
});
const FIXED_GROUPS = new Set(['tw_g1', 'tw_g2', 'us_g1', 'us_g2', 'us_g3', 'us_g4', 'ungrouped']);

function id(value: unknown) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) throw new Error('參數 ID 不正確。');
  return parsed;
}
function name(value: unknown) {
  const parsed = String(value ?? '').trim();
  if (!parsed || parsed.length > 30) throw new Error('分組名稱需為 1～30 個字。');
  return parsed;
}
function averages(value: unknown) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 4) throw new Error('均線參數需為 1～4 組。');
  const numbers = value.map(Number);
  if (numbers.some((n) => !Number.isInteger(n) || n < 1 || n > 1000) || new Set(numbers).size !== numbers.length)
    throw new Error('均線請填 1～4 個不重複的整數（1～1000）。');
  return {
    ma1: numbers[0] ?? 0,
    ma2: numbers[1] ?? 0,
    ma3: numbers[2] ?? 0,
    ma4: numbers[3] ?? 0,
  };
}

/** The same parameter operations in an account-bound device store or disposable memory. */
export class LocalParameters {
  constructor(private readonly storage: ParameterStorage) {}
  private read(): z.infer<typeof document> {
    const raw = this.storage.getItem(KEY);
    if (!raw) return { version: 1, nextId: 1, groups: [], stocks: [] };
    const parsed = document.safeParse(JSON.parse(raw));
    if (!parsed.success) throw new Error('此帳號的裝置參數格式無法辨識；未覆寫原有資料。');
    return parsed.data;
  }
  private save(data: z.infer<typeof document>) {
    this.storage.setItem(KEY, JSON.stringify(document.parse(data)));
  }
  call(action: string, payload: Record<string, unknown>): unknown {
    const data = this.read();
    if (action === 'groups.list') return structuredClone(data.groups);
    if (action === 'stocks.list') return structuredClone(data.stocks).sort((a, b) => a.ticker.localeCompare(b.ticker));
    if (action === 'groups.create') {
      if (data.groups.length >= 50) throw new Error('分組數量已達 50 組上限。');
      const result: ParameterGroup = {
        id: data.nextId++,
        name: name(payload.name),
        ma1: 5,
        ma2: 10,
        ma3: 20,
        ma4: 60,
      };
      data.groups.push(result);
      this.save(data);
      return structuredClone(result);
    }
    if (action === 'groups.rename' || action === 'groups.delete') {
      const key = id(payload.id);
      const current = data.groups.find((entry) => entry.id === key);
      if (!current) throw new Error('分組不存在或不屬於此帳號。');
      if (action === 'groups.rename') current.name = name(payload.name);
      else {
        if (data.stocks.some((entry) => entry.group_id === `custom_${key}`)) throw new Error('分組內仍有股票，請先移除。');
        data.groups = data.groups.filter((entry) => entry.id !== key);
      }
      this.save(data);
      return [
        {
          id: key,
          ...(action === 'groups.rename' ? { name: current.name } : {}),
        },
      ];
    }
    if (action === 'stocks.delete') {
      const key = id(payload.id);
      if (!data.stocks.some((entry) => entry.id === key)) throw new Error('這筆股票參數不存在或已刪除。');
      data.stocks = data.stocks.filter((entry) => entry.id !== key);
      this.save(data);
      return [{ id: key }];
    }
    if (action === 'stocks.save') {
      const ticker = String(payload.ticker ?? '')
        .trim()
        .toUpperCase();
      if (!/^[A-Z0-9.^_-]{1,30}$/.test(ticker)) throw new Error('股票代號格式不正確。');
      const groupId = String(payload.groupId ?? '').trim();
      if (!FIXED_GROUPS.has(groupId) && !data.groups.some((entry) => groupId === `custom_${entry.id}`))
        throw new Error('分組不存在或沒有權限。');
      const values = averages(payload.mas);
      const hasId = payload.id !== undefined && payload.id !== null && payload.id !== '';
      const current = hasId
        ? data.stocks.find((entry) => entry.id === id(payload.id))
        : data.stocks.find((entry) => entry.ticker === ticker && entry.group_id === groupId);
      if (hasId && !current) throw new Error('這筆股票參數不存在或沒有權限。');
      if (!current && data.stocks.length >= 500) throw new Error('股票參數數量已達 500 筆上限。');
      if (
        data.stocks.some(
          (entry) =>
            entry !== current &&
            entry.ticker === ticker &&
            (entry.group_id === groupId || (entry.group_id !== 'ungrouped' && groupId !== 'ungrouped')),
        )
      )
        throw new Error('此股票已有分組參數，請編輯原有參數；固定均線可另外保留。');
      const next: ParameterStock = {
        id: current?.id ?? data.nextId++,
        ticker,
        group_id: groupId,
        ...values,
      };
      data.stocks = current ? data.stocks.map((entry) => (entry.id === current.id ? next : entry)) : [...data.stocks, next];
      this.save(data);
      return [{ id: next.id }];
    }
    throw new Error('此操作不適用於裝置或暫存參數。');
  }
}
