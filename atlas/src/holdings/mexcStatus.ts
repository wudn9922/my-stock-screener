/** connectedAt is a reminder reference, not the exchange's actual expiry date. */
export function mexcKeyAgeNotice(connectedAt: string, now = Date.now()): string | undefined {
  const connected = Date.parse(connectedAt);
  return Number.isFinite(connected) && Number.isFinite(now) && now - connected >= 80 * 86400000
    ? 'MEXC 未綁 IP 的 Key 約 90 天到期，請準備更換'
    : undefined;
}
