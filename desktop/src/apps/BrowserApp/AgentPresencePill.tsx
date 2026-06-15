/**
 * AgentPresencePill — stacked agent avatars + idle/watching state indicator.
 * Renders when at least one agent is pinned to the tab. Clicking it toggles
 * the agent panel (Task 9 implements the panel itself).
 */
import React, { useEffect, useRef, useState } from "react";
import { listAgents, type AgentDto } from "@/lib/browser-agent-api";
import { useBrowserAgentStore, WATCHING_DECAY_MS } from "@/stores/browser-agent-store";

const MAX_AVATARS = 4;

/** Deterministic hue from an agent id string. */
function agentColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  return `hsl(${hue}, 55%, 45%)`;
}

export interface AgentPresencePillProps {
  windowId: string;
  tabId: string;
  pinnedAgentIds: string[];
  /** Optional ref so click-outside / focus restore upstream can target it */
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
}

export function AgentPresencePill({
  windowId,
  tabId,
  pinnedAgentIds,
  triggerRef,
}: AgentPresencePillProps) {
  const [agents, setAgents] = useState<AgentDto[]>([]);

  // Subscribe to lastEventAt + panels so Zustand re-renders on changes
  const lastEventAt = useBrowserAgentStore((s) => s.lastEventAt);
  const panels = useBrowserAgentStore((s) => s.panels);
  const togglePanel = useBrowserAgentStore((s) => s.togglePanel);
  const isWatching = useBrowserAgentStore((s) => s.isWatching);

  // Subscribe to drivingState so the pill reflects driving visual
  const isDriving = useBrowserAgentStore((s) =>
    pinnedAgentIds.some((aid) => s.drivingState[`${windowId}:${tabId}:${aid}`] === "driving"),
  );

  const panelKey = `${windowId}:${tabId}`;
  const panelIsOpen = panels[panelKey]?.isOpen ?? false;

  // Load agents once on mount
  useEffect(() => {
    let cancelled = false;
    listAgents().then((list) => {
      if (!cancelled) setAgents(list);
    });
    return () => { cancelled = true; };
  }, []);

  // Compute watching state from the subscribed lastEventAt
  // (lastEventAt in the selector triggers re-render on each bumpEventAt call)
  void lastEventAt; // ensure selector is referenced so Zustand tracks it
  const anyWatching = pinnedAgentIds.some((id) => isWatching(windowId, tabId, id));

  // Decay timer: when watching, schedule a re-render after WATCHING_DECAY_MS
  // so the pulse disappears even without a new event.
  const decayTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!anyWatching) {
      if (decayTimerRef.current) {
        clearTimeout(decayTimerRef.current);
        decayTimerRef.current = null;
      }
      return;
    }
    // Find the latest event ts for pinned agents to compute remaining decay time
    const store = useBrowserAgentStore.getState();
    const latestTs = pinnedAgentIds.reduce<number>((max, id) => {
      const ts = store.lastEventAt[`${windowId}:${tabId}:${id}`] ?? 0;
      return ts > max ? ts : max;
    }, 0);
    const elapsed = Date.now() - latestTs;
    const remaining = Math.max(0, WATCHING_DECAY_MS - elapsed);

    decayTimerRef.current = setTimeout(() => {
      // Force re-render by bumping a dummy state change; we re-read isWatching in render
      setAgents((prev) => [...prev]);
    }, remaining + 50);

    return () => {
      if (decayTimerRef.current) {
        clearTimeout(decayTimerRef.current);
        decayTimerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyWatching]);

  if (pinnedAgentIds.length === 0) return null;

  // Build avatar data: limit to first 4 pinned ids
  const displayIds = pinnedAgentIds.slice(0, MAX_AVATARS);

  // Map ids to agent objects; fall back to placeholder if not found in loaded list
  const displayAgents = displayIds.map((id) => {
    const found = agents.find((a) => a.id === id);
    return found ?? { id, name: id };
  });

  // Build label from resolved agent names
  const resolvedNames = displayAgents.map((a) => a.name).join(", ");
  const count = pinnedAgentIds.length;
  const label = `${count} agent${count !== 1 ? "s" : ""} pinned: ${resolvedNames} — click to toggle agent panel`;

  const firstAgentId = pinnedAgentIds[0] ?? "";

  return (
    <button
      ref={triggerRef as React.RefObject<HTMLButtonElement> | undefined}
      type="button"
      aria-label={label}
      title={label}
      aria-haspopup="dialog"
      aria-expanded={panelIsOpen}
      onClick={() => togglePanel(windowId, tabId, firstAgentId)}
      className={[
        "flex items-center relative h-[32px] px-1.5 rounded-full border border-accent-line transition-colors",
        panelIsOpen ? "bg-accent-glow" : "bg-accent-soft hover:bg-accent-glow",
      ].join(" ")}
    >
      {/* Stacked avatars */}
      <div className="flex items-center">
        {displayAgents.map((agent, i) => (
          <span
            key={agent.id}
            data-testid="agent-avatar"
            aria-hidden="true"
            className={[
              "inline-flex items-center justify-center",
              "w-4 h-4 rounded-full text-[9px] font-semibold text-white",
              "ring-1 ring-shell-surface",
              i > 0 ? "-ml-1" : "",
            ].join(" ")}
            style={{ backgroundColor: agentColor(agent.id), zIndex: MAX_AVATARS - i }}
          >
            {agent.name.charAt(0).toUpperCase()}
          </span>
        ))}
      </div>

      {/* Presence dot — three states:
           driving: brighter green + faster pulse (animate-ping approximates fast)
           watching: standard green + pulse
           idle: dark green, static */}
      <span
        data-testid="presence-dot"
        aria-hidden="true"
        className={[
          "absolute -top-0.5 -right-0.5",
          "w-2 h-2 rounded-full ring-1 ring-shell-surface",
          isDriving
            ? "bg-green-400 animate-ping"
            : anyWatching
            ? "bg-green-400 animate-pulse"
            : "bg-green-600",
        ].join(" ")}
      />
    </button>
  );
}
