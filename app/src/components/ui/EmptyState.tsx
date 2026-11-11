/** Centered placeholder for screens or panels with nothing to show yet. */
import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <h2 className="text-base font-semibold">{title}</h2>
      {description && <div className="max-w-md text-sm text-muted">{description}</div>}
      {action}
    </div>
  );
}
