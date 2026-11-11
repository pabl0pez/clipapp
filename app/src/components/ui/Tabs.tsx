/** WAI-ARIA tabs: arrow keys, Home and End move between tabs (automatic activation). */
import { type KeyboardEvent, type ReactNode, useId, useRef } from "react";
import { focusRing } from "./Button";

interface TabsProps<T extends string> {
  label: string;
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  /** Lock every tab (e.g. while the project is being prepared). */
  disabled?: boolean;
  /** Content of the selected tab. */
  children: ReactNode;
}

export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  disabled,
  children,
}: TabsProps<T>) {
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(
    0,
    tabs.findIndex((t) => t.id === value),
  );

  const onKeyDown = (e: KeyboardEvent) => {
    const last = tabs.length - 1;
    const moves: Record<string, number> = {
      ArrowRight: current === last ? 0 : current + 1,
      ArrowLeft: current === 0 ? last : current - 1,
      Home: 0,
      End: last,
    };
    const next = moves[e.key];
    const tab = next === undefined ? undefined : tabs[next];
    if (next === undefined || !tab) return;
    e.preventDefault();
    onChange(tab.id);
    refs.current[next]?.focus();
  };

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label={label}
        className="flex gap-1 border-b border-border"
        onKeyDown={onKeyDown}
      >
        {tabs.map((t, i) => {
          const selected = i === current;
          return (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`${base}-panel`}
              tabIndex={selected ? 0 : -1}
              disabled={disabled}
              onClick={() => {
                onChange(t.id);
              }}
              className={`-mb-px border-b-2 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${selected ? "border-accent text-text" : "border-transparent text-muted hover:text-text"} ${focusRing}`}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={`${base}-panel`}
        aria-labelledby={`${base}-tab-${tabs[current]?.id ?? ""}`}
        tabIndex={0}
        className={focusRing}
      >
        {children}
      </div>
    </div>
  );
}
