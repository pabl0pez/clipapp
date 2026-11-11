/** Modal dialog on the native `<dialog>`: focus trap, Esc to close and backdrop come for free. */
import { type ReactNode, useEffect, useId, useRef } from "react";
import { IconButton } from "./Button";
import { CloseIcon } from "./icons";

interface ModalProps {
  open: boolean;
  title: string;
  /** Called on Esc, the close button, or when `open` turns false. Must be idempotent. */
  onClose: () => void;
  /** Rendered only while open, so forms inside start fresh each time. */
  children: ReactNode;
}

export function Modal({ open, title, onClose, children }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      className="m-auto w-full max-w-lg rounded-lg border border-border bg-surface p-0 text-text shadow-2xl backdrop:bg-black/60"
    >
      {open && (
        <div className="flex flex-col gap-4 p-5">
          <div className="flex items-start justify-between gap-4">
            <h2 id={titleId} className="text-lg font-semibold">
              {title}
            </h2>
            <IconButton label="Cerrar" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}
