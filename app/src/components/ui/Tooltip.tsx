/** Text tooltip shown on hover and keyboard focus, linked with `aria-describedby`. */
import { cloneElement, type ReactElement, useId } from "react";

interface TooltipProps {
  text: string;
  /** A single focusable element (button, link…). */
  children: ReactElement<{ "aria-describedby"?: string }>;
}

export function Tooltip({ text, children }: TooltipProps) {
  const id = useId();
  return (
    <span className="group relative inline-flex">
      {cloneElement(children, { "aria-describedby": id })}
      <span
        role="tooltip"
        id={id}
        className="pointer-events-none invisible absolute top-full left-1/2 z-40 mt-1 -translate-x-1/2 rounded bg-surface-2 px-2 py-1 text-xs whitespace-nowrap text-text shadow group-focus-within:visible group-hover:visible"
      >
        {text}
      </span>
    </span>
  );
}
