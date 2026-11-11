/** Choose the mic and game tracks of a project, with a 5 s preview of every track. */
import { useEffect, useRef, useState } from "react";
import { type AudioTrack, mediaUrl, type Project } from "../../api/client";
import { usePatchProject } from "../../api/queries";
import { Button, IconButton } from "../../components/ui/Button";
import { PlayIcon, StopIcon } from "../../components/ui/icons";
import { Modal } from "../../components/ui/Modal";
import { Select } from "../../components/ui/Select";
import { toast } from "../../components/ui/Toast";

/** "Pista 2 · Mic": 1-based like OBS, plus the title OBS wrote. */
export function trackLabel(track: AudioTrack): string {
  return `Pista ${String(track.index + 1)}${track.title ? ` · ${track.title}` : ""}`;
}

function TrackForm({ project, onDone }: { project: Project; onDone: () => void }) {
  const { media } = project;
  const [mic, setMic] = useState(media.mic_track);
  const [game, setGame] = useState(media.game_track);
  const [playing, setPlaying] = useState<number | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const { mutate, isPending } = usePatchProject(project.id);

  useEffect(() => () => audio.current?.pause(), []);

  const toggle = (track: number) => {
    audio.current?.pause();
    if (playing === track) {
      setPlaying(null);
      return;
    }
    const failed = () => {
      setPlaying(null);
      toast(`No se pudo reproducir la pista ${String(track + 1)}`, "error");
    };
    const el = new Audio(
      mediaUrl(`/projects/${String(project.id)}/media/tracks/${String(track)}/preview`),
    );
    el.onended = () => {
      setPlaying(null);
    };
    el.onerror = failed;
    audio.current = el;
    setPlaying(track);
    el.play().catch(failed);
  };

  const save = () => {
    if (mic === media.mic_track && game === media.game_track) onDone();
    else mutate({ mic_track: mic, game_track: game }, { onSuccess: onDone });
  };

  const options = media.audio_tracks.map((t) => ({ value: t.index, label: trackLabel(t) }));
  return (
    <>
      <p className="text-sm text-muted">
        OBS suele grabar la mezcla en la pista 1, el micrófono en la 2 y el juego en la 3. Cambiar
        el micrófono vuelve a preparar el proyecto.
      </p>
      <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
        {media.audio_tracks.map((t) => (
          <li key={t.index} className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="text-sm">
              {trackLabel(t)}
              <span className="ml-2 text-xs text-muted">
                {t.codec} · {t.channels} canales
              </span>
            </span>
            <IconButton
              label={
                playing === t.index ? "Detener" : `Escuchar 5 s de la pista ${String(t.index + 1)}`
              }
              onClick={() => {
                toggle(t.index);
              }}
            >
              {playing === t.index ? <StopIcon /> : <PlayIcon />}
            </IconButton>
          </li>
        ))}
      </ul>
      <div className="grid grid-cols-2 gap-3">
        <Select label="Micrófono" value={mic} options={options} onChange={setMic} />
        <Select label="Juego" value={game} options={options} onChange={setGame} />
      </div>
      {mic === game && (
        <p className="text-sm text-warning">El micrófono y el juego son la misma pista.</p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onDone}>Cancelar</Button>
        <Button variant="primary" onClick={save} disabled={isPending}>
          Guardar
        </Button>
      </div>
    </>
  );
}

export function TrackSelector({
  project,
  open,
  onClose,
}: {
  project: Project;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Modal open={open} title="Pistas de audio" onClose={onClose}>
      <TrackForm project={project} onDone={onClose} />
    </Modal>
  );
}
