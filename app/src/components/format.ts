/** Display formatting shared by the screens. */

/** `125.4` → `"2:05"`, `3725` → `"1:02:05"`. */
export function formatDuration(seconds: number): string {
  const s = Math.floor(seconds);
  const [h, m, sec] = [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60];
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${String(h)}:${pad(m)}:${pad(sec)}` : `${String(m)}:${pad(sec)}`;
}

/** Engine timestamp (ISO, UTC) in the local date and time. */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("es", { dateStyle: "medium", timeStyle: "short" });
}

/** Bytes as GB with one decimal. */
export function formatGb(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}
