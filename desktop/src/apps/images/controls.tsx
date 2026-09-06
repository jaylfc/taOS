import { ChevronDown, RefreshCw } from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Images Studio — small shared control components                    */
/*                                                                     */
/*  Theme rule: semantic tokens + auto-inverting white/N utilities      */
/*  only. No hex / rgb() literals.                                      */
/* ------------------------------------------------------------------ */

/** Pill-shaped segmented control (Single/Batch, All/FLUX/SDXL, …). */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: readonly {
    value: T;
    label: string;
    /** Disable this option (e.g. a tier whose backend isn't installed). */
    disabled?: boolean;
    /** Tooltip shown on the disabled option. */
    title?: string;
  }[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="flex rounded-full border border-shell-border bg-shell-surface p-[3px]"
    >
      {options.map((opt) => {
        const on = opt.value === value;
        const disabled = opt.disabled ?? false;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={on}
            // aria-disabled (not the native `disabled` attribute) so the
            // element still receives pointer/focus events and its `title`
            // tooltip actually surfaces — a truly disabled/pointer-events-none
            // control never shows its native tooltip. The click is guarded
            // below instead.
            aria-disabled={disabled || undefined}
            title={opt.title}
            onClick={() => {
              if (!disabled) onChange(opt.value);
            }}
            className={`rounded-full px-3 py-[5px] text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
              disabled
                ? "cursor-not-allowed text-shell-text-secondary opacity-40"
                : on
                  ? "bg-shell-surface-active text-shell-text"
                  : "text-shell-text-secondary hover:text-shell-text"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/** Labelled slider. Renders a real <input type=range> (keyboard accessible)
 *  with a styled track painted via accent-color. */
export function Slider({
  id,
  label,
  value,
  min,
  max,
  step = 1,
  display,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  display: string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-[11.5px]">
        <label
          htmlFor={id}
          className="text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary"
        >
          {label}
        </label>
        <b className="font-bold tabular-nums text-shell-text">{display}</b>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        aria-label={label}
        className="w-full accent-accent"
      />
    </div>
  );
}

/** Rounded chip toggle used for Style and Mode rows. */
export function Chip({
  label,
  on,
  onClick,
  ariaPressed = true,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
  ariaPressed?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={ariaPressed ? on : undefined}
      onClick={onClick}
      className={`rounded-full border px-3 py-1.5 text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
        on
          ? "border-accent bg-accent/15 text-accent"
          : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-white/10"
      }`}
    >
      {label}
    </button>
  );
}

/** Model selector pill — shows the active model name + meta, opens the
 *  catalog/browser when clicked. */
export function ModelPill({
  name,
  meta,
  onClick,
}: {
  name: string;
  meta: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Change model"
      className="flex w-full items-center gap-2.5 rounded-xl border border-shell-border bg-shell-surface px-3 py-2.5 text-left transition-colors hover:border-shell-border-strong hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
    >
      <span className="h-6 w-6 shrink-0 rounded-lg bg-gradient-to-br from-accent to-accent/60" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] font-semibold text-shell-text">
          {name}
        </span>
        <span className="block truncate text-[10.5px] text-shell-text-tertiary">
          {meta}
        </span>
      </span>
      <ChevronDown size={14} className="shrink-0 text-shell-text-tertiary" />
    </button>
  );
}

/** Seed pill with a re-roll button. */
export function SeedPill({
  seed,
  onReroll,
}: {
  seed: string;
  onReroll: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-shell-border bg-shell-surface px-3 py-2.5">
      <span className="flex-1 truncate text-[12.5px] font-semibold tabular-nums text-shell-text">
        {seed || "Random"}
      </span>
      <button
        type="button"
        onClick={onReroll}
        aria-label="Re-roll seed"
        className="rounded-md p-1 text-shell-text-tertiary transition-colors hover:bg-white/10 hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      >
        <RefreshCw size={14} />
      </button>
    </div>
  );
}

/** Uppercase control-group label. */
export function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-2.5 block text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
      {children}
    </span>
  );
}
