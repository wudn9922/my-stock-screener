import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Explanatory copy belongs after the current page, before storage/data health. */
export function PageFooterNote({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useEffect(() => setTarget(document.getElementById("site-page-notes")), []);
  return target
    ? createPortal(<div className="page-footer-note">{children}</div>, target)
    : null;
}
