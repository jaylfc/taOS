/**
 * CoPilotBanner — persistent banner shown while any pinned agent on the
 * active tab is in the "driving" state.
 *
 * - Pause: flips drivingState → idle locally (server-side drive session decay
 *   continues naturally; Task 10 will wire the server event).
 * - Take back: revokes the drive capability grant, unpins the agent, and
 *   resets the local driving state.
 */
import { revokeCapability } from "@/lib/browser-capability-api";
import { unpinAgent } from "@/lib/browser-agent-api";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";

export interface CoPilotBannerProps {
  windowId: string;
  tabId: string;
  profileId: string;
  agentId: string;
  agentName?: string;
  onTakeBack?(): void;
}

export function CoPilotBanner({
  windowId,
  tabId,
  profileId,
  agentId,
  agentName,
  onTakeBack,
}: CoPilotBannerProps) {
  const displayName = agentName ?? agentId;

  const handlePause = () => {
    useBrowserAgentStore.getState().setDrivingState(windowId, tabId, agentId, "idle");
  };

  const handleTakeBack = async () => {
    try {
      // 1. Revoke drive capability (idempotent on both "*" and the host wildcard)
      await revokeCapability(profileId, agentId, "*");

      // 2. Unpin the agent from the tab
      await unpinAgent(profileId, tabId, agentId);

      // 3. Remove from local pin state
      const browserStore = await import("@/stores/browser-store");
      browserStore.useBrowserStore.getState().removePinnedAgent(windowId, tabId, agentId);
    } finally {
      // Always flip driving state and notify parent, even if the API calls fail.
      useBrowserAgentStore.getState().setDrivingState(windowId, tabId, agentId, "idle");
      onTakeBack?.();
    }
  };

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 px-3 py-1 bg-green-500/15 text-green-200 border-b border-green-500/40 text-xs"
    >
      {/* Status text */}
      <span className="flex-1">
        <span className="font-semibold">{displayName}</span> is co-piloting this tab
      </span>

      {/* Actions */}
      <button
        type="button"
        aria-label="Pause co-pilot"
        onClick={handlePause}
        className="px-2 py-0.5 rounded border border-green-500/50 hover:bg-green-500/20 text-green-200"
      >
        Pause
      </button>
      <button
        type="button"
        aria-label="Take back control"
        onClick={handleTakeBack}
        className="px-2 py-0.5 rounded border border-green-500/50 hover:bg-green-500/20 text-green-200"
      >
        Take back
      </button>
    </div>
  );
}
