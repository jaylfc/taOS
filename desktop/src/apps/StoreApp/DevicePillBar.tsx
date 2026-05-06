// desktop/src/apps/StoreApp/DevicePillBar.tsx
import { useMemo } from "react";
import { X } from "lucide-react";
import type { InstallTarget } from "./types";

interface Props {
  devices: InstallTarget[];
  selected: string[]; // device names
  onChange: (next: string[]) => void;
  loading?: boolean;
  /** When true, render skeleton pills (initial load). */
  showSkeleton?: boolean;
}

export function DevicePillBar({
  devices,
  selected,
  onChange,
  showSkeleton,
}: Props) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  if (showSkeleton) {
    return (
      <div className="flex gap-2 overflow-x-auto py-2" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-7 w-24 rounded-full bg-shell-border/40 animate-pulse shrink-0"
          />
        ))}
      </div>
    );
  }

  if (devices.length === 0) return null;

  const toggle = (name: string) => {
    const next = selectedSet.has(name)
      ? selected.filter((n) => n !== name)
      : [...selected, name];
    onChange(next);
  };

  const clear = () => onChange([]);

  return (
    <div
      className="flex gap-2 overflow-x-auto py-2 items-center"
      role="group"
      aria-label="Filter by device"
    >
      {devices.map((d) => {
        const isOn = selectedSet.has(d.name);
        const tierBadge = d.tier_id?.replace(/^arm-|^x86-|^apple-/, "") ?? "";
        return (
          <button
            key={d.name}
            type="button"
            aria-pressed={isOn}
            onClick={() => toggle(d.name)}
            className={`shrink-0 inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs whitespace-nowrap transition-colors ${
              isOn
                ? "bg-accent/15 text-accent border-accent/30"
                : "bg-transparent text-shell-text-secondary border-shell-border hover:bg-shell-border/40"
            }`}
          >
            <span>{d.friendly_name ?? d.label}</span>
            {tierBadge && (
              <span className="text-[10px] opacity-70 uppercase tracking-wide">
                {tierBadge}
              </span>
            )}
          </button>
        );
      })}
      {selected.length > 0 && (
        <button
          type="button"
          onClick={clear}
          aria-label="Clear device filter"
          className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] text-shell-text-tertiary hover:text-shell-text-primary"
        >
          <X size={12} />
          Clear
        </button>
      )}
    </div>
  );
}
