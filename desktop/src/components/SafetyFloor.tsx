import { Sparkles } from "lucide-react";
import { useTaosAgentStore } from "@/stores/taos-agent-store";

/**
 * SafetyFloor — the system-owned, always-present assistant button.
 *
 * Mounted by the shell in a guaranteed top layer (z-index 10000, above
 * the effects layer and all windows) and outside any themeable region,
 * so no theme can ever hide it. This is the un-overridable escape hatch:
 * the user can always summon the taOS assistant to fix a broken theme.
 *
 * It opens the SAME assistant panel as every other trigger by calling
 * the shared taos-agent-store — it does not own its own panel state.
 */
export function SafetyFloor() {
  const openPanel = useTaosAgentStore((s) => s.openPanel);

  return (
    <div style={{ position: "fixed", zIndex: 10000, top: 4, right: 8, pointerEvents: "auto" }}>
      <button
        aria-label="taOS assistant"
        onClick={openPanel}
        className="rounded-full p-2 bg-shell-surface-active hover:brightness-110 transition-[filter]"
      >
        <Sparkles className="w-4 h-4" />
      </button>
    </div>
  );
}
