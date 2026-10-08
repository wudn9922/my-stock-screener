/** Identifies transient source availability failures eligible for stale-cache fallback. */
export class MarketDataUnavailableError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'MarketDataUnavailableError';
  }
}
