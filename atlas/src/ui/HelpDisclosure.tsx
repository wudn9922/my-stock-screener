import type { ReactNode } from 'react';
import './helpDisclosure.css';

/** Secondary explanations stay available without taking space from the data. */
export function HelpDisclosure({
  label = '說明',
  children,
  className = '',
}: {
  label?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details className={`help-disclosure ${className}`.trim()}>
      <summary>{label}</summary>
      <div className="help-disclosure-body">{children}</div>
    </details>
  );
}
