/** Settings: for now only the engine's diagnostics (`/system`) and version. */
import { useHealth, useSystem } from "../../api/queries";
import { formatGb } from "../../components/format";
import { Button } from "../../components/ui/Button";

export function SettingsPage() {
  const system = useSystem();
  const health = useHealth();
  const report = system.data;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Ajustes</h1>
        <Button onClick={() => void system.refetch()} disabled={system.isFetching}>
          {system.isFetching ? "Analizando…" : "Volver a analizar"}
        </Button>
      </div>
      {system.isPending && (
        <p className="text-muted">Analizando el equipo (tarda unos segundos)…</p>
      )}
      {system.isError && <p className="text-danger">{system.error.message}</p>}
      {report && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 rounded-lg bg-surface p-4 text-sm">
          <dt className="text-muted">Motor</dt>
          <dd>{health.data?.version ?? "…"}</dd>
          <dt className="text-muted">FFmpeg</dt>
          <dd>{report.ffmpeg_version}</dd>
          <dt className="text-muted">FFprobe</dt>
          <dd>{report.ffprobe_version}</dd>
          <dt className="text-muted">GPU</dt>
          <dd>{report.gpus.length ? report.gpus.join(", ") : "Ninguna detectada"}</dd>
          <dt className="text-muted">CUDA</dt>
          <dd>{report.cuda}</dd>
          <dt className="text-muted">Codificadores</dt>
          <dd>
            <ul>
              {report.encoders.map((e) => (
                <li key={e.name} title={e.error ?? undefined}>
                  <span className={e.usable ? "text-success" : "text-muted"}>
                    {e.usable ? "✓" : "✗"}
                  </span>{" "}
                  {e.name}
                </li>
              ))}
            </ul>
          </dd>
          <dt className="text-muted">Datos</dt>
          <dd className="break-all">{report.data_dir}</dd>
          <dt className="text-muted">Espacio libre</dt>
          <dd>{formatGb(report.disk_free_bytes)}</dd>
        </dl>
      )}
    </div>
  );
}
