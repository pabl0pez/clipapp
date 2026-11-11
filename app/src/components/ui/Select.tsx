/** Labelled native select that keeps option values typed (strings or numbers). */
import { focusRing } from "./Button";

interface SelectProps<T extends string | number> {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}

export function Select<T extends string | number>({
  label,
  value,
  options,
  onChange,
  disabled,
}: SelectProps<T>) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span>{label}</span>
      <select
        className={`rounded-md border border-border bg-surface-2 px-2 py-1.5 disabled:opacity-50 ${focusRing}`}
        value={String(value)}
        disabled={disabled}
        onChange={(e) => {
          const option = options[e.currentTarget.selectedIndex];
          if (option) onChange(option.value);
        }}
      >
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
