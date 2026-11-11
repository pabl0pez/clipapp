/** Transient notifications: call `toast()` from anywhere and mount `<Toaster />` once. */
import { create } from "zustand";
import { IconButton } from "./Button";
import { CloseIcon } from "./icons";

type Kind = "info" | "success" | "error";
interface ToastItem {
  id: number;
  message: string;
  kind: Kind;
}

const DURATION_MS = 5000;
let nextId = 0;

const useToasts = create<{ items: ToastItem[]; dismiss: (id: number) => void }>()((set) => ({
  items: [],
  dismiss: (id) => {
    set((s) => ({ items: s.items.filter((t) => t.id !== id) }));
  },
}));

/** Show a message for a few seconds. */
export function toast(message: string, kind: Kind = "info"): void {
  const id = ++nextId;
  useToasts.setState((s) => ({ items: [...s.items, { id, message, kind }] }));
  setTimeout(() => {
    useToasts.getState().dismiss(id);
  }, DURATION_MS);
}

const BORDER: Record<Kind, string> = {
  info: "border-border",
  success: "border-success",
  error: "border-danger",
};

/** Stack of current toasts; errors are announced assertively, the rest politely. */
export function Toaster() {
  const { items, dismiss } = useToasts();
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-80 flex-col gap-2">
      {items.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className={`pointer-events-auto flex items-start gap-2 rounded-md border-l-4 bg-surface-2 p-3 text-sm shadow-lg ${BORDER[t.kind]}`}
        >
          <p className="flex-1 break-words">{t.message}</p>
          <IconButton
            label="Cerrar aviso"
            className="h-6 w-6"
            onClick={() => {
              dismiss(t.id);
            }}
          >
            <CloseIcon />
          </IconButton>
        </div>
      ))}
    </div>
  );
}
