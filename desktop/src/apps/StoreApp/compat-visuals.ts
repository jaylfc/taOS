// desktop/src/apps/StoreApp/compat-visuals.ts
import type { ResolveResponse } from "./resolver-types";

export interface CompatVisuals {
  /** Tailwind classes to append to the Card. Empty string when unclassified. */
  borderClass: string;
  /** Tooltip text shown on hover — explains *why* this colour was chosen. */
  tooltip: string;
}

const REASON_LABELS: Record<string, string> = {
  ram: "Not enough RAM",
  vram: "Not enough VRAM",
  disk: "Not enough disk space",
  target: "No matching hardware target",
  schema: "Variant has no requires.backends declaration",
};

/**
 * Map a /api/store/resolve response to a card-border treatment.
 *
 * - **green** — at least one variant runs accelerated on the user's cluster.
 * - **amber** — the model fits but only on CPU, or the backend isn't
 *   installed yet (action="install_chain").
 * - **red** — the model exceeds RAM/VRAM/disk on every available device, or
 *   no compatible hardware target exists.
 * - **undefined** — resolver hasn't classified this manifest yet (or it's
 *   not a model). No border treatment is applied.
 */
export function compatVisuals(resp: ResolveResponse | undefined): CompatVisuals {
  if (!resp || !("compat" in resp)) return { borderClass: "", tooltip: "" };

  if (resp.compat === "green") {
    const action = resp.result === "ok" ? resp.action : null;
    const tip = action === "install_chain"
      ? "Compatible — backend will be installed first"
      : "Compatible — runs accelerated on this cluster";
    return { borderClass: "border-l-4 border-l-emerald-400/70", tooltip: tip };
  }

  if (resp.compat === "amber") {
    if (resp.result === "err") {
      const why = REASON_LABELS[resp.near_miss.blocked_by ?? ""] ?? resp.reason;
      const shortBy = resp.near_miss.short_by_mb;
      const detail = shortBy ? ` (short by ${shortBy} MB)` : "";
      return {
        borderClass: "border-l-4 border-l-amber-400/70",
        tooltip: `Partial: ${why}${detail}`,
      };
    }
    return {
      borderClass: "border-l-4 border-l-amber-400/70",
      tooltip: "Runs CPU-only on this cluster",
    };
  }

  // red
  if (resp.result === "err") {
    const why = REASON_LABELS[resp.near_miss.blocked_by ?? ""] ?? resp.reason;
    const shortBy = resp.near_miss.short_by_mb;
    const detail = shortBy ? ` (short by ${shortBy} MB)` : "";
    return {
      borderClass: "border-l-4 border-l-red-400/70",
      tooltip: `Won't run: ${why}${detail}`,
    };
  }
  return {
    borderClass: "border-l-4 border-l-red-400/70",
    tooltip: "Won't run on this cluster",
  };
}
