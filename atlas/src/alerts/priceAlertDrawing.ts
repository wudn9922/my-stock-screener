import type { AccessSnapshot } from '../auth/access';
import type { CloudSyncState } from '../cloud/startDrawingSync';
import type { Anchor, Drawing, DrawingStyle } from '../drawing/DrawingModel';
import type { Timeframe } from '../market-data/Timeframes';

export type PriceAlertAnchor = Omit<Anchor, 'price'>;
export function discordDrawingAvailability(access: Pick<AccessSnapshot, 'ready' | 'userId' | 'storageMode'>, sync: CloudSyncState, hasAnchor: boolean): string | null {
  if (!access.ready || !access.userId || access.storageMode !== 'cloud') return '需具有 Supabase 雲端保存授權才能使用 Discord 推播；瀏覽器提醒仍可使用。';
  if (sync === 'error' || sync === 'expired') return '畫線雲端同步尚未成功，請完成同步後再建立 Discord 監控線。';
  if (sync !== 'synced') return '等待畫線雲端同步完成後，即可建立 Discord 監控線。';
  if (!hasAnchor) return '請等候此股票的 K 線載入後再建立監控線。';
  return null;
}

/** Canonical chart drawing only: no server, webhook, monitoring config or notification is added. */
export function priceAlertDrawing(symbol: string, timeframe: Timeframe, price: number, anchor: PriceAlertAnchor, drawings: readonly Drawing[], defaults: Partial<DrawingStyle> = {}): { drawing: Drawing; created: boolean } {
  if (!Number.isFinite(price) || price <= 0) throw new Error('請輸入大於零的有效提醒價格。');
  if (!Number.isFinite(anchor.time) || anchor.time <= 0 || !Number.isFinite(anchor.logical) || anchor.timeframe !== timeframe) throw new Error('此股票與週期的 K 線尚未就緒。');
  const existing = drawings.find(d => d.symbol === symbol && d.type === 'horizontal' && d.visible && (d.scope.timeframes === 'all' || d.scope.timeframes.includes(timeframe)) && d.points[0]?.timeframe === timeframe && d.points[0].price === price);
  if (existing) return { drawing: existing, created: false };
  return { created: true, drawing: {
    id: crypto.randomUUID(), symbol, type: 'horizontal', points: [{ ...anchor, price }],
    locked: false, visible: true, scope: { timeframes: [timeframe] },
    style: { color: '#5ca9ff', lineWidth: 2, ...defaults },
  } };
}
