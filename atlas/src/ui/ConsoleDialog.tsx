import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { usePageActive } from '../app/pageActivity';

type ConsoleDialogProps = { title: string; busy?: boolean; onClose: () => void; children: ReactNode };

/** A dialog of a kept page that is not on screen closes; it opens again when the page returns. */
export function ConsoleDialog(props: ConsoleDialogProps) {
  return usePageActive() ? <OpenConsoleDialog {...props} /> : null;
}

/** Native modal supplies focus trapping, background isolation and focus restoration. */
function OpenConsoleDialog({
  title,
  busy = false,
  onClose,
  children,
}: ConsoleDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return createPortal(
    <dialog
      ref={ref}
      className="parameter-dialog"
      aria-labelledby={titleId}
      onKeyDown={(event) => event.stopPropagation()}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
    </dialog>,
    document.fullscreenElement ?? document.body,
  );
}
