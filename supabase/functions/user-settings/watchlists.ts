/** Pure validation shared with offline tests. No browser or service-role credentials. */
const SYMBOL = /^[A-Z0-9^][A-Z0-9.^=-]{0,31}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('清單格式不正確');
  return value as Record<string, unknown>;
}
function text(value: unknown, max: number): string {
  if (typeof value !== 'string' || value.length > max) throw new Error('清單文字欄位格式不正確');
  return value;
}
function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('清單時間格式不正確');
  return value;
}
function ticker(value: unknown): string {
  const result = text(value, 32);
  if (!SYMBOL.test(result)) throw new Error('清單股票代號不正確');
  return result;
}
function day(value: unknown): string {
  const result = text(value, 10);
  const time = Date.parse(`${result}T00:00:00Z`);
  if (!DAY.test(result) || !Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== result)
    throw new Error('清單資料日期不正確');
  return result;
}
export function watchlistId(value: unknown): string {
  const result = text(value, 36);
  if (!UUID.test(result)) throw new Error('清單 ID 不正確');
  return result;
}
export function watchlistVersion(value: unknown): string {
  const result = text(value, 40);
  if (!Number.isFinite(Date.parse(result))) throw new Error('雲端版本不正確，請重新載入雲端清單');
  return result;
}
export function validateWatchlist(value: unknown) {
  const raw = object(value);
  const id = watchlistId(raw.id);
  const name = text(raw.name, 40).trim();
  if (!name) throw new Error('請填寫清單名稱');
  if (raw.market !== 'TW' && raw.market !== 'US') throw new Error('清單市場不正確');
  if (raw.kind !== 'rules' && raw.kind !== 'manual') throw new Error('清單種類不正確');
  const group = text(raw.group, 80);
  if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(group)) throw new Error('清單來源群組不正確');
  // Keep in sync with Atlas FILTER_PARAM_MAX; repeated fundamental gates share this encoding.
  const filters = text(raw.filters, 2000);
  if (filters && !/^[A-Za-z0-9:.,;_~-]+$/.test(filters)) throw new Error('篩選條件格式不正確');
  if (!Array.isArray(raw.symbols) || raw.symbols.length > 500) throw new Error('手動清單最多 500 檔');
  const symbols = [...new Set(raw.symbols.map(ticker))];
  if (
    !Array.isArray(raw.maList) ||
    raw.maList.length < 1 ||
    raw.maList.length > 4 ||
    raw.maList.some((n) => !Number.isInteger(n) || n < 1 || n > 1000) ||
    new Set(raw.maList).size !== raw.maList.length
  )
    throw new Error('均線需為 1～4 個不重複的整數（1～1000）');
  if (typeof raw.monitorBreakouts !== 'boolean') throw new Error('突破監控選項不正確');
  if (!Array.isArray(raw.history) || raw.history.length > 90) throw new Error('清單歷史最多 90 個資料日');
  let count = 0;
  const history = raw.history.map((entry: unknown) => {
    const row = object(entry);
    const date = day(row.date);
    if (!Array.isArray(row.members) || row.members.length > 15000) throw new Error('入選名單最多 15000 檔');
    count += row.members.length;
    if (count > 150000) throw new Error('清單歷史過大，請先匯出備份並縮短歷史');
    const members = row.members.map((entry: unknown) => {
      const item = object(entry);
      return { symbol: ticker(item.symbol), name: text(item.name, 200) };
    });
    if (new Set(members.map((item) => item.symbol)).size !== members.length) throw new Error('入選股票不可重複');
    return { date, members, previousSession: row.previousSession == null ? null : day(row.previousSession) };
  });
  if (history.some((entry, i) => i > 0 && entry.date <= history[i - 1].date)) throw new Error('清單歷史日期必須遞增且不重複');
  let lastViewed: { date: string; members: { symbol: string; name: string }[] } | null = null;
  if (raw.lastViewed != null) {
    const row = object(raw.lastViewed);
    if (!Array.isArray(row.members) || row.members.length > 15000) throw new Error('上次開啟名單格式不正確');
    lastViewed = {
      date: day(row.date),
      members: row.members.map((entry: unknown) => {
        const item = object(entry);
        return { symbol: ticker(item.symbol), name: text(item.name, 200) };
      }),
    };
  }
  return {
    id,
    name,
    market: raw.market,
    kind: raw.kind,
    group,
    filters,
    symbols,
    maList: raw.maList as number[],
    monitorBreakouts: raw.monitorBreakouts,
    updatedAt: number(raw.updatedAt),
    lastOpenedAt: raw.lastOpenedAt === null ? null : number(raw.lastOpenedAt),
    history,
    lastViewed,
  };
}
