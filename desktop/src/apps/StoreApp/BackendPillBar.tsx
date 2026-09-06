// desktop/src/apps/StoreApp/BackendPillBar.tsx
import { useMemo } from "react";
import { backendMeta } from "./backends";

interface Props {
  /** Backends available given the currently selected devices. */
  available: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** True when no devices are selected — bar should not render at all. */
  disabled?: boolean;
}

export function BackendPillBar({
  available,
  selected,
  onChange,
  disabled,
}: Props) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  if (disabled || available.length === 0) return null;

  const toggle = (name: string) => {
    const next = selectedSet.has(name)
      ? selected.filter((n) => n !== name)
      : [...selected, name];
    onChange(next);
  };

  return (
    <div
      className="flex gap-2 overflow-x-auto py-1.5 items-center"
      role="group"
      aria-label="Filter by backend"
    >
      <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mr-1 shrink-0">
        Backend
      </span>
      {available.map((b) => {
        const meta = backendMeta(b);
        const isOn = selectedSet.has(b);
        return (
          <button
            key={b}
            type="button"
            aria-pressed={isOn}
            onClick={() => toggle(b)}
            className={`shrink-0 inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full border text-[11px] whitespace-nowrap transition-colors ${
              isOn
                ? meta.classes
                : "bg-transparent text-shell-text-secondary border-shell-border hover:bg-shell-border/40"
            }`}
          >
            <span aria-hidden="true">{meta.icon}</span>
            <span>{meta.label}</span>
          </button>
        );
      })}
    </div>
  );
}
