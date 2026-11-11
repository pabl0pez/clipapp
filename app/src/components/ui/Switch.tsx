/** On/off toggle (`role="switch"`; Space and Enter toggle it as a native button). */
import { focusRing } from "./Button";

interface SwitchProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

export function Switch({ label, checked, onChange, disabled }: SwitchProps) {
  return (
    <label className="inline-flex items-center gap-2 text-sm">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => {
          onChange(!checked);
        }}
        className={`relative h-5 w-9 rounded-full transition-colors disabled:opacity-50 ${checked ? "bg-accent" : "bg-border"} ${focusRing}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform ${checked ? "translate-x-4" : ""}`}
        />
      </button>
      <span>{label}</span>
    </label>
  );
}
