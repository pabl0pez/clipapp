/** Project screen: editable header, track selector, prepare progress and the (empty) tabs. */
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import type { Job, Project } from "../../api/client";
import { usePatchProject, usePrepareJob, useProject, useRetryJob } from "../../api/queries";
import { formatDuration } from "../../components/format";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ProgressBar } from "../../components/ui/ProgressBar";
import { Tabs } from "../../components/ui/Tabs";
import { type ProjectTab, useUiStore } from "../../stores/ui";
import { TrackSelector, trackLabel } from "../tracks/TrackSelector";
import { prepareSteps } from "./prepareSteps";

const TABS: { id: ProjectTab; label: string }[] = [
  { id: "silences", label: "Silencios" },
  { id: "clips", label: "Clips" },
  { id: "export", label: "Exportar" },
];

function NameField({ project }: { project: Project }) {
  const { mutate } = usePatchProject(project.id);
  const [draft, setDraft] = useState(project.name);
  const [shown, setShown] = useState(project.name);
  if (project.name !== shown) {
    setShown(project.name);
    setDraft(project.name);
  }
  const commit = () => {
    const name = draft.trim();
    if (!name) setDraft(project.name);
    else if (name !== project.name) mutate({ name });
  };
  return (
    <input
      aria-label="Nombre del proyecto"
      value={draft}
      onChange={(e) => {
        setDraft(e.currentTarget.value);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setDraft(project.name);
      }}
      className="w-full rounded-md border border-transparent bg-transparent px-1 text-xl font-semibold hover:border-border focus:border-accent focus:outline-none"
    />
  );
}

function PrepareStatus({ job, preparing }: { job: Job | undefined; preparing: boolean }) {
  const retry = useRetryJob();
  if (preparing) {
    const progress = job?.status === "running" ? job.progress : 0;
    return (
      <section aria-label="Preparación" className="flex flex-col gap-3 rounded-lg bg-surface p-4">
        <h2 className="font-medium">
          {job?.status === "queued" ? "Preparación en cola…" : "Preparando el proyecto…"}
        </h2>
        {prepareSteps(progress).map((s) => (
          <ProgressBar key={s.label} label={s.label} value={s.value} />
        ))}
        {progress >= 1 && <p className="text-sm text-muted">Detectando silencios…</p>}
      </section>
    );
  }
  return (
    <section
      role="alert"
      className="flex items-center justify-between gap-4 rounded-lg border border-danger bg-surface p-4"
    >
      <p className="text-sm">La preparación falló{job?.error ? `: ${job.error}` : "."}</p>
      {job && (
        <Button
          onClick={() => {
            retry.mutate(job.id);
          }}
          disabled={retry.isPending}
        >
          Reintentar
        </Button>
      )}
    </section>
  );
}

export function ProjectPage() {
  const id = Number(useParams().id);
  const project = useProject(id);
  const job = usePrepareJob(id);
  const [search, setSearch] = useSearchParams();
  const tab = useUiStore((s) => s.tab);
  const setTab = useUiStore((s) => s.setTab);
  const openProject = useUiStore((s) => s.openProject);

  useEffect(() => {
    openProject(id);
  }, [id, openProject]);

  if (project.isPending) return <p className="p-6 text-muted">Cargando…</p>;
  if (project.isError) {
    return (
      <div className="p-6">
        <EmptyState
          title="No se pudo abrir el proyecto"
          description={project.error.message}
          action={<Link to="/">Volver al inicio</Link>}
        />
      </div>
    );
  }

  const p = project.data;
  const { media } = p;
  const preparing = job
    ? job.status === "queued" || job.status === "running"
    : media.prepare_status === "pending" || media.prepare_status === "running";
  const failed = !preparing && media.prepare_status === "failed";
  const trackName = (i: number) => {
    const t = media.audio_tracks[i];
    return t ? trackLabel(t) : `Pista ${String(i + 1)}`;
  };
  const setTracksOpen = (open: boolean) => {
    setSearch(open ? { tracks: "1" } : {}, { replace: true });
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-col gap-2">
        <NameField project={p} />
        <p className="text-sm text-muted">
          {formatDuration(media.duration_s)} · {media.width}×{media.height} · {media.fps.toFixed(2)}{" "}
          fps · {media.audio_tracks.length} pistas de audio
        </p>
        <p className="truncate text-xs text-muted" title={media.path}>
          {media.path}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span>Micrófono: {trackName(media.mic_track)}</span>
          <span>Juego: {trackName(media.game_track)}</span>
          <Button
            onClick={() => {
              setTracksOpen(true);
            }}
          >
            Cambiar pistas
          </Button>
        </div>
      </header>
      {(preparing || failed) && <PrepareStatus job={job} preparing={preparing} />}
      <Tabs
        label="Secciones del proyecto"
        tabs={TABS}
        value={tab}
        onChange={setTab}
        disabled={preparing || failed}
      >
        <EmptyState
          title={TABS.find((t) => t.id === tab)?.label ?? ""}
          description={
            preparing
              ? "Disponible cuando termine la preparación."
              : "Esta sección llega en una próxima etapa."
          }
        />
      </Tabs>
      <TrackSelector
        project={p}
        open={search.get("tracks") === "1"}
        onClose={() => {
          setTracksOpen(false);
        }}
      />
    </div>
  );
}
