import { Sparkles } from "lucide-react";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { useThemeStore } from "@/stores/theme-store";

/**
 * SafetyFloor — the system-owned assistant button of last resort.
 *
 * The standard assistant trigger lives in the top bar (TopBar). When the
 * active theme hides that bar, the user would lose all access to the
 * assistant — so this fallback is mounted in a guaranteed top layer
 * (z-index 10000, above the effects layer and all windows) and outside any
 * themeable region. It is the un-overridable escape hatch enforcing the
 * `requires: ["assistant"]` safety contract.
 *
 * It renders ONLY when the active theme hides the top bar; otherwise it
 * would duplicate the existing top-bar button (e.g. on the default theme).
 *
 * It opens the SAME assistant panel as every other trigger by calling
 * the shared taos-agent-store — it does not own its own panel state.
 */
export function SafetyFloor() {
  const openPanel = useTaosAgentStore((s) => s.openPanel);
  const topBarHidden = useThemeStore((s) => s.structure?.topBar?.variant === "hidden");

  // Standard chrome present → the top-bar assistant button already covers it.
  if (!topBarHidden) return null;

  return (
    <div style={{ position: "fixed", zIndex: 10000, top: 4, right: 8, pointerEvents: "auto" }}>
      <button
        aria-label="taOS agent"
        onClick={openPanel}
        className="rounded-full p-2 bg-shell-surface-active hover:brightness-110 transition-[filter]"
      >
        <Sparkles className="w-4 h-4" />
      </button>
    </div>
  );
}
