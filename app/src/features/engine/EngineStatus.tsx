/** Engine connection indicator of the top bar, and the full screen shown while offline. */
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { type Connection, useUiStore } from "../../stores/ui";

const STATUS: Record<Connection, { label: string; dot: string }> = {
  connecting: { label: "Conectando…", dot: "bg-warning" },
  connected: { label: "Motor conectado", dot: "bg-success" },
  reconnecting: { label: "Reconectando…", dot: "bg-warning animate-pulse" },
  offline: { label: "Sin conexión", dot: "bg-danger" },
};

export function EngineStatus() {
  const { label, dot } = STATUS[useUiStore((s) => s.connection)];
  return (
    <span role="status" className="flex items-center gap-2 text-xs text-muted">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

export function EngineOffline({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto max-w-xl p-8">
      <EmptyState
        title="No hay conexión con el motor"
        description={
          <>
            <p>La app sigue intentándolo sola. En desarrollo, arranca el motor desde engine/:</p>
            <code className="mt-2 block rounded bg-surface-2 px-2 py-1 text-xs text-text">
              uv run clipapp-engine serve --port 8765 --token dev
            </code>
          </>
        }
        action={
          <Button variant="primary" onClick={onRetry}>
            Reintentar
          </Button>
        }
      />
    </div>
  );
}
