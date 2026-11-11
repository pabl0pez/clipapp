/** Labelled determinate progress bar; `value` is a fraction in [0, 1]. */

export function ProgressBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="flex flex-col gap-1 text-sm">
      <div className="flex justify-between">
        <span>{label}</span>
        <span className="text-muted tabular-nums">{pct}%</span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="h-2 overflow-hidden rounded-full bg-surface-2"
      >
        <div className="h-full bg-accent transition-[width]" style={{ width: `${String(pct)}%` }} />
      </div>
    </div>
  );
}
