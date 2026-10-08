import { RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';

/** Loading / empty / error placeholder used by the report pages. */
export function PageState({
  kind,
  title,
  message,
  onRetry,
  children,
}: {
  kind: 'loading' | 'empty' | 'error';
  title: string;
  message?: string;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={`page-state ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {kind === 'loading' ? <span className="loading-ring" aria-hidden="true" /> : null}
      <b>{title}</b>
      {message && <p>{message}</p>}
      {children}
      {onRetry && (
        <button type="button" className="ghost-button" onClick={onRetry}>
          <RefreshCw size={15} />
          重新載入
        </button>
      )}
    </div>
  );
}

export function Skeleton({ height = 16, width = '100%' }: { height?: number; width?: number | string }) {
  return <span className="skeleton" style={{ height, width }} aria-hidden="true" />;
}
