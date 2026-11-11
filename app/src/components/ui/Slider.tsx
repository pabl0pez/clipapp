/** Labelled range slider (native input, so arrows/Home/End/PageUp work). */

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  format?: (value: number) => string;
  disabled?: boolean;
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  format = String,
  disabled,
}: SliderProps) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="flex justify-between">
        <span>{label}</span>
        <output className="text-muted tabular-nums">{format(value)}</output>
      </span>
      <input
        type="range"
        className="accent-accent disabled:opacity-50"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => {
          onChange(e.currentTarget.valueAsNumber);
        }}
      />
    </label>
  );
}
