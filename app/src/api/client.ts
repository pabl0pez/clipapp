/** Typed HTTP client of the local engine: adds the session token and turns failures into EngineError. */
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Project = Schemas["Project"];
export type ProjectPatch = Schemas["ProjectPatch"];
export type AudioTrack = Schemas["AudioTrack"];
export type Job = Schemas["Job"];

/** Result of `GET /system` (the engine declares it as a plain object in OpenAPI). */
export interface SystemReport {
  ffmpeg_version: string;
  ffprobe_version: string;
  gpus: string[];
  encoders: { name: string; usable: boolean; error: string | null }[];
  cuda: string;
  data_dir: string;
  disk_free_bytes: number;
}

/** Layout of the thumbnail sprite (`GET /projects/{id}/media/thumbnails/index`). */
export interface ThumbIndex {
  interval_s: number;
  width: number;
  height: number;
  columns: number;
  rows: number;
  count: number;
}

// shortcut: fixed dev engine from .env.development; Stage 14 gets port and token from the sidecar.
export const ENGINE_URL = import.meta.env.VITE_ENGINE_URL ?? "http://127.0.0.1:8765";
export const ENGINE_TOKEN = import.meta.env.VITE_ENGINE_TOKEN ?? "dev";
export const TOKEN_HEADER = "X-ClipApp-Token";

/** A failed engine call; `status` is the HTTP code, or 0 when the engine could not be reached. */
export class EngineError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "EngineError";
  }
}

/** Raw openapi-fetch client; wrap its calls with `call` to get data or an EngineError. */
export const api = createClient<paths>({
  baseUrl: ENGINE_URL,
  headers: { [TOKEN_HEADER]: ENGINE_TOKEN },
  // look fetch up per call (openapi-fetch would capture it at import, before test mocks patch it)
  fetch: (request) => globalThis.fetch(request),
});

/** Turn the engine's `{"detail": ...}` body (a string, or FastAPI's validation list) into text. */
function detailOf(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { msg?: unknown }) => (typeof d.msg === "string" ? d.msg : JSON.stringify(d)))
      .join("; ");
  }
  return `El motor respondió con el error ${String(status)}`;
}

/**
 * Await an openapi-fetch request and return its data.
 *
 * @throws EngineError with the HTTP status and the engine's message, or status 0 on network failure.
 */
export async function call<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: Awaited<typeof request>;
  try {
    result = await request;
  } catch {
    throw new EngineError(0, "No se pudo conectar con el motor");
  }
  if (!result.response.ok) {
    throw new EngineError(result.response.status, detailOf(result.error, result.response.status));
  }
  return result.data as T;
}

/** URL of a media route for `<audio>`, `<video>` or CSS, which cannot send headers. */
export function mediaUrl(path: string): string {
  return `${ENGINE_URL}${path}?token=${encodeURIComponent(ENGINE_TOKEN)}`;
}
