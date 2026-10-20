import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Native modal supplies focus trapping, background isolation and focus restoration. */
export function ConsoleDialog({
  title,
  busy = false,
  onClose,
  children,
}: {
  title: string;
  busy?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
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
