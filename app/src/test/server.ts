/** MSW server standing in for the engine, plus a scripted WebSocket and fixtures. */
import { setupServer } from "msw/node";
import { ENGINE_URL, type Job, type Project } from "../api/client";

export const server = setupServer();

/** Absolute engine URL of an API path (MSW matches full URLs). */
export const url = (path: string) => `${ENGINE_URL}${path}`;

export function makeProject(fields: Partial<Project> = {}): Project {
  return {
    id: 1,
    name: "partida ñ",
    created_at: "2026-11-11T10:00:00.000Z",
    updated_at: "2026-11-11T10:00:00.000Z",
    media: {
      path: "C:\\Grabaciones\\partida ñ.mkv",
      file_hash: "abc",
      duration_s: 3725,
      fps: 60,
      width: 1920,
      height: 1080,
      audio_tracks: ["Mix", "Mic", "Game"].map((title, index) => ({
        index,
        stream_index: index + 1,
        codec: "aac",
        channels: 2,
        sample_rate: 48000,
        title,
        language: null,
      })),
      mic_track: 1,
      game_track: 2,
      cache_dir: "C:\\cache\\abc",
      prepare_status: "running",
    },
    ...fields,
  };
}

export function makeJob(fields: Partial<Job> = {}): Job {
  return {
    id: 1,
    kind: "prepare",
    target_id: 1,
    status: "running",
    progress: 0,
    output_path: null,
    error: null,
    created_at: "2026-11-11T10:00:00.000Z",
    finished_at: null,
    ...fields,
  };
}

/** WebSocket double: tests drive `open()`, `send()` and `close()` from the server side. */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  static get last(): FakeWebSocket {
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    if (!ws) throw new Error("no WebSocket was opened");
    return ws;
  }

  open() {
    this.onopen?.();
  }

  send(event: object) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }

  /** Close from the client (what the hook calls). */
  close() {
    this.closed = true;
  }

  /** Drop the connection from the engine side. */
  drop() {
    this.onclose?.();
  }
}
