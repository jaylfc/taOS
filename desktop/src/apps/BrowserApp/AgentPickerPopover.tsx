/**
 * AgentPickerPopover — lets the user search for and pin an agent to the active tab.
 * Opened by clicking "+ agent" chip in Chrome or via Cmd+Shift+A.
 */
import { useEffect, useRef, useState } from "react";
import { listAgents, pinAgent, type AgentDto } from "@/lib/browser-agent-api";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";

const MAX_PINS = 4;

export interface AgentPickerPopoverProps {
  windowId: string;
  tabId: string;
  profileId: string;
  pinnedAgentIds: string[];
  onClose: () => void;
}

export function AgentPickerPopover({
  windowId,
  tabId,
  profileId,
  pinnedAgentIds,
  onClose,
}: AgentPickerPopoverProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [agents, setAgents] = useState<AgentDto[] | null>(null);
  const [query, setQuery] = useState("");
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Load agents on mount
  useEffect(() => {
    let cancelled = false;
    listAgents().then((list) => {
      if (!cancelled) {
        setAgents(list);
      }
    });
    return () => { cancelled = true; };
  }, []);

  // Auto-focus search on mount
  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  // Click-outside dismiss (deferred like ProfileSwitcher)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      window.removeEventListener("mousedown", handler);
    };
  }, [onClose]);

  const pinnedCount = pinnedAgentIds.length;
  const atMax = pinnedCount >= MAX_PINS;

  const filtered = (agents ?? []).filter((a) =>
    a.name.toLowerCase().includes(query.toLowerCase()),
  );

  // Build enabled/disabled list for keyboard navigation
  const isDisabled = (a: AgentDto) => atMax || pinnedAgentIds.includes(a.id);
  const enabledIndices = filtered.reduce<number[]>((acc, a, i) => {
    if (!isDisabled(a)) acc.push(i);
    return acc;
  }, []);

  async function handlePin(agent: AgentDto) {
    if (isDisabled(agent)) return;
    setError(null);
    const result = await pinAgent(profileId, tabId, agent.id);
    if (result === null) {
      setError("Network error — please try again");
      return;
    }
    if ("error" in result) {
      setError(result.error);
      return;
    }
    // { pinned: true } or { pinned: false } (race / already pinned) — both call addPinnedAgent
    useBrowserStore.getState().addPinnedAgent(windowId, tabId, agent.id);
    useBrowserAgentStore.getState().openPanel(windowId, tabId, agent.id);
    onClose();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (enabledIndices.length === 0) return;
      const currentPos = enabledIndices.indexOf(focusedIndex);
      const next = enabledIndices[(currentPos + 1) % enabledIndices.length] ?? enabledIndices[0] ?? 0;
      setFocusedIndex(next);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (enabledIndices.length === 0) return;
      const currentPos = enabledIndices.indexOf(focusedIndex);
      const prev =
        currentPos <= 0
          ? (enabledIndices[enabledIndices.length - 1] ?? 0)
          : (enabledIndices[currentPos - 1] ?? 0);
      setFocusedIndex(prev);
      return;
    }
    if (e.key === "Enter") {
      const agent = filtered[focusedIndex];
      if (agent && !isDisabled(agent)) {
        handlePin(agent);
      }
    }
  }

  // Reset focus index when filtered list changes
  useEffect(() => {
    setFocusedIndex(enabledIndices[0] ?? 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, agents?.length]);

  return (
    <div
      ref={ref}
      className="absolute z-[70] right-0 top-full mt-1 w-80 rounded-md bg-shell-surface border border-shell-border-subtle shadow-lg text-xs text-shell-text"
      onKeyDown={handleKeyDown}
    >
      {/* Error banner */}
      {error && (
        <div
          role="alert"
          className="px-3 py-2 bg-red-500/10 text-red-400 border-b border-shell-border-subtle"
        >
          {error}
        </div>
      )}

      {/* Header + count */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-shell-border-subtle">
        <span className="text-shell-text-tertiary uppercase tracking-wide text-[10px]">
          Pin an agent
        </span>
        <span className="text-shell-text-tertiary text-[10px]">
          {pinnedCount} / {MAX_PINS} pinned
        </span>
      </div>

      {/* Search */}
      <div className="px-3 py-2 border-b border-shell-border-subtle">
        <label htmlFor="agent-picker-search" className="sr-only">
          Search agents
        </label>
        <input
          id="agent-picker-search"
          ref={searchRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search…"
          className="w-full bg-shell-bg-deep border border-shell-border-subtle rounded px-2 py-1 text-xs outline-none focus:border-accent"
        />
      </div>

      {/* Agent list */}
      <div
        role="listbox"
        aria-label="Pick an agent to pin"
        className="max-h-64 overflow-y-auto"
      >
        {agents === null ? (
          <div className="px-3 py-3 opacity-60 italic">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="px-3 py-3 opacity-60 italic">No agents found</div>
        ) : (
          filtered.map((agent, i) => {
            const disabled = isDisabled(agent);
            const alreadyPinned = pinnedAgentIds.includes(agent.id);
            const focused = i === focusedIndex && !disabled;
            const emoji = resolveAgentEmoji(
              agent.emoji as string | undefined,
              agent.framework as string | undefined,
            );
            return (
              <div
                key={agent.id}
                role="option"
                aria-selected={focused}
                aria-disabled={disabled ? "true" : undefined}
                title={alreadyPinned ? "Already pinned" : undefined}
                onClick={() => !disabled && handlePin(agent)}
                onMouseEnter={() => !disabled && setFocusedIndex(i)}
                className={[
                  "flex items-center gap-2 px-3 py-2 cursor-pointer select-none",
                  disabled
                    ? "opacity-50 cursor-not-allowed"
                    : focused
                    ? "bg-shell-hover"
                    : "hover:bg-shell-hover",
                ].join(" ")}
              >
                <span
                  className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-sm bg-shell-bg-deep"
                  aria-hidden="true"
                >
                  {emoji}
                </span>
                <span className="flex-1 truncate">{agent.name}</span>
                {alreadyPinned && (
                  <span className="text-shell-text-tertiary text-[10px]">pinned</span>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Footer when at max */}
      {atMax && (
        <div className="px-3 py-2 text-center text-shell-text-tertiary border-t border-shell-border-subtle">
          Maximum agents reached for this tab
        </div>
      )}
    </div>
  );
}
