/** Home: recent projects, import button and project deletion. */
import { useState } from "react";
import { Link } from "react-router";
import { mediaUrl, type Project } from "../../api/client";
import { useDeleteProject, useProjects, useThumbIndex } from "../../api/queries";
import { formatDate, formatDuration } from "../../components/format";
import { Button, focusRing, IconButton } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { TrashIcon } from "../../components/ui/icons";
import { Modal } from "../../components/ui/Modal";
import { toast } from "../../components/ui/Toast";
import { useImportVideo } from "./useImportVideo";

/** Middle frame of the project's thumbnail sprite (the first one is often black). */
function Thumbnail({ project }: { project: Project }) {
  const index = useThumbIndex(project.id, project.media.prepare_status === "done").data;
  let style;
  if (index) {
    const i = Math.floor(index.count / 2);
    const [col, row] = [i % index.columns, Math.floor(i / index.columns)];
    const pct = (n: number, of: number) => `${String(of > 1 ? (n / (of - 1)) * 100 : 0)}%`;
    style = {
      backgroundImage: `url("${mediaUrl(`/projects/${String(project.id)}/media/thumbnails`)}")`,
      backgroundSize: `${String(index.columns * 100)}% auto`,
      backgroundPosition: `${pct(col, index.columns)} ${pct(row, index.rows)}`,
    };
  }
  return <div aria-hidden="true" className="aspect-video bg-surface-2" style={style} />;
}

function DeleteProjectModal({
  project,
  onClose,
}: {
  project: Project | null;
  onClose: () => void;
}) {
  const { mutate, isPending } = useDeleteProject();
  const confirm = () => {
    if (!project) return;
    mutate(project.id, {
      onSuccess: () => {
        toast("Proyecto borrado", "success");
        onClose();
      },
    });
  };
  return (
    <Modal open={project !== null} title="¿Borrar el proyecto?" onClose={onClose}>
      <p className="text-sm">
        Se borrarán «{project?.name}», sus clips, subtítulos y archivos de caché.
      </p>
      <p className="text-sm text-muted">
        El video original no se borra y sigue en{" "}
        <span className="break-all text-text">{project?.media.path}</span>.
      </p>
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="danger" onClick={confirm} disabled={isPending}>
          Borrar proyecto
        </Button>
      </div>
    </Modal>
  );
}

export function HomePage() {
  const projects = useProjects();
  const { pickAndImport, pending } = useImportVideo();
  const [toDelete, setToDelete] = useState<Project | null>(null);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Proyectos recientes</h1>
        <Button variant="primary" onClick={() => void pickAndImport()} disabled={pending}>
          {pending ? "Importando…" : "Importar video"}
        </Button>
      </div>
      {projects.isPending && <p className="text-muted">Cargando…</p>}
      {projects.isError && <p className="text-danger">{projects.error.message}</p>}
      {projects.data?.length === 0 && (
        <EmptyState
          title="Todavía no hay proyectos"
          description="Importa una grabación MP4, MKV o MOV, o arrástrala sobre la ventana."
        />
      )}
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
        {projects.data?.map((p) => (
          <li
            key={p.id}
            className="relative overflow-hidden rounded-lg border border-border bg-surface"
          >
            <Link to={`/projects/${String(p.id)}`} className={`block rounded-lg ${focusRing}`}>
              <Thumbnail project={p} />
              <div className="p-3">
                <p className="truncate font-medium">{p.name}</p>
                <p className="text-xs text-muted">
                  {formatDuration(p.media.duration_s)} · {formatDate(p.updated_at)}
                </p>
              </div>
            </Link>
            <IconButton
              label={`Borrar ${p.name}`}
              variant="secondary"
              className="absolute top-2 right-2"
              onClick={() => {
                setToDelete(p);
              }}
            >
              <TrashIcon />
            </IconButton>
          </li>
        ))}
      </ul>
      <DeleteProjectModal
        project={toDelete}
        onClose={() => {
          setToDelete(null);
        }}
      />
    </div>
  );
}
