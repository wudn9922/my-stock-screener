import type { ReactNode } from 'react';

/** Secondary explanations stay available without taking space from the financial figures. */
export function FinancialNote({ children, label = '說明', className = '' }: { children: ReactNode; label?: string; className?: string }) {
  return (
    <details className={`fin-explanation ${className}`.trim()}>
      <summary>{label}</summary>
      <div className="fin-explanation-content">{children}</div>
    </details>
  );
}
