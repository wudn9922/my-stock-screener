import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { usePageActive } from "../app/pageActivity";

/** Explanatory copy belongs after the current page, before storage/data health. */
export function PageFooterNote({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  // A kept page that is not on screen shows no footer copy.
  const active = usePageActive();
  useEffect(() => setTarget(document.getElementById("site-page-notes")), []);
  return target && active
    ? createPortal(<div className="page-footer-note">{children}</div>, target)
    : null;
}
