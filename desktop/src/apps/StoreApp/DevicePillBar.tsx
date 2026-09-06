// desktop/src/apps/StoreApp/DevicePillBar.tsx
import { useMemo } from "react";
import { X } from "lucide-react";
import type { InstallTarget } from "./types";

/** Banner rendered above the catalog when one or more selected devices have unknown hardware. */
export function UnknownHardwareBanner({ devices }: { devices: InstallTarget[] }) {
  const unknownNames = devices
    .filter((d) => d.hardware_known === false)
    .map((d) => d.friendly_name ?? d.label);
  if (unknownNames.length === 0) return null;
  const names = unknownNames.join(", ");
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300 mb-2"
    >
      <span aria-hidden="true">?</span>
      <span>
        Selected {unknownNames.length === 1 ? "device" : "devices"}{" "}
        <strong>{names}</strong>{" "}
        {unknownNames.length === 1 ? "has" : "have"} unknown hardware — register{" "}
        {unknownNames.length === 1 ? "it" : "them"} via the Cluster app to filter
        accurately. Showing all models for now.
      </span>
    </div>
  );
}

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
        const hardwareUnknown = d.hardware_known === false;
        const tierBadge = hardwareUnknown
          ? "?"
          : (d.tier_id?.replace(/^arm-|^x86-|^apple-/, "") ?? "");
        const tooltip = hardwareUnknown
          ? "Hardware unknown — register this worker via the Cluster app to see compatibility"
          : undefined;
        return (
          <button
            key={d.name}
            type="button"
            aria-pressed={isOn}
            title={tooltip}
            onClick={() => toggle(d.name)}
            className={`shrink-0 inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs whitespace-nowrap transition-colors ${
              isOn
                ? "bg-accent/15 text-accent border-accent/30"
                : "bg-transparent text-shell-text-secondary border-shell-border hover:bg-shell-border/40"
            }`}
          >
            <span>{d.friendly_name ?? d.label}</span>
            {tierBadge && (
              <span
                className={`text-[10px] uppercase tracking-wide ${hardwareUnknown ? "opacity-50" : "opacity-70"}`}
                aria-label={hardwareUnknown ? "hardware unknown" : undefined}
              >
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
