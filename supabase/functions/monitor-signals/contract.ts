/** Public, read-only projection. Never return raw state metadata or account IDs. */
export interface MonitorSignal {
  groupId: string;
  groupName: string;
  ticker: string;
  name: string;
  market: 'TW' | 'US';
  direction: 'breakout_up' | 'breakout_down';
  maPeriod: number;
  sentAt: string;
  price: number | null;
  maValue: number | null;
  volumeRatio: number | null;
}
export interface MonitorSnapshot {
  version: 1;
  generatedAt: string;
  windowStart: string;
  truncated: boolean;
  signals: MonitorSignal[];
}

const GROUP_NAMES: Record<string, string> = {
  tw_g1: '台股權值精選', tw_g2: '台股熱門', us_g1: '美股權值精選',
  us_g2: '美股低本益比', us_g3: '美股超級績效', us_g4: '美股熱門',
  ungrouped: '個股固定參數', admin_index: '大盤指數',
};
const text = (value: unknown, length: number) => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, length) : '';
const positive = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;

/** The state retains last_alert_* only after a successful Discord send. */
export function projectSignal(value: unknown): MonitorSignal | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const groupId = text(row.group_id, 80), ticker = text(row.ticker, 30).toUpperCase();
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(groupId) || groupId.startsWith('atlas_')
    || !/^[A-Z0-9.^_-]{1,30}$/.test(ticker)
    || (row.market !== 'TW' && row.market !== 'US')
    || (row.last_alert_direction !== 'breakout_up' && row.last_alert_direction !== 'breakout_down')
    || typeof row.last_alert_at !== 'string' || !Number.isFinite(Date.parse(row.last_alert_at))
    || !Number.isInteger(row.ma_period) || Number(row.ma_period) < 1 || Number(row.ma_period) > 1000) return null;
  const metadata = row.metadata && typeof row.metadata === 'object' ? row.metadata as Record<string, unknown> : {};
  return {
    groupId, groupName: text(metadata.group_name, 100) || GROUP_NAMES[groupId] || '自訂監控組別', ticker,
    name: text(metadata.display_name, 100) || ticker, market: row.market,
    direction: row.last_alert_direction, maPeriod: Number(row.ma_period), sentAt: new Date(row.last_alert_at).toISOString(),
    price: positive(row.last_alert_price), maValue: positive(row.last_alert_ma_value), volumeRatio: positive(row.last_alert_volume_ratio),
  };
}
