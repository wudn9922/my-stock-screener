import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

export interface FoldCardProps {
  title: string;
  /** One line under the title, visible folded or open: what the section holds right now. */
  summary?: string | null;
  /** Open at first render. Folded by default for the sections below the growth and profitability cards. */
  defaultOpen?: boolean;
  /** Children mount only after the section has been opened once (heavy charts, price fetches). */
  lazy?: boolean;
  className?: string;
  dataAttributes?: Record<string, string>;
  children?: ReactNode;
}

/**
 * A collapsible financials card on native details / summary: the title and a one-line summary stay visible
 * while folded. Opening and closing need no React state except the lazy mount.
 */
export function FoldCard({ title, summary, defaultOpen = false, lazy = false, className, dataAttributes, children }: FoldCardProps) {
  const [opened, setOpened] = useState(defaultOpen);
  return (
    <details
      className={`card fin-fold${className ? ` ${className}` : ''}`}
      open={defaultOpen}
      onToggle={(event) => {
        if (event.currentTarget.open) setOpened(true);
      }}
      {...dataAttributes}
    >
      <summary className="fin-card-head fin-fold-head">
        <h2>{title}</h2>
        {summary && <span className="fin-fold-summary">{summary}</span>}
        <ChevronDown size={18} className="fin-chevron" aria-hidden="true" />
      </summary>
      <div className="fin-fold-body">{!lazy || opened ? children : null}</div>
    </details>
  );
}
