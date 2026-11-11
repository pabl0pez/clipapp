/** Buttons: text `Button` and square `IconButton` (which needs an accessible label). */
import type { ButtonHTMLAttributes } from "react";

/** Keyboard focus ring shared by every interactive component. */
export const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  secondary: "bg-surface-2 text-text hover:bg-border",
  danger: "bg-danger text-white hover:brightness-110",
  ghost: "text-muted hover:bg-surface-2 hover:text-text",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

/** Button with the app's look; `type` defaults to "button" so it never submits by accident. */
export function Button({ variant = "secondary", className = "", type, ...props }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${focusRing} ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

/** Square button holding only an icon; `label` becomes its accessible name and native tooltip. */
export function IconButton({
  label,
  variant = "ghost",
  className = "",
  ...props
}: ButtonProps & { label: string }) {
  return (
    <Button
      aria-label={label}
      title={label}
      variant={variant}
      className={`h-8 w-8 p-0 ${className}`}
      {...props}
    />
  );
}
