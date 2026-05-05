/**
 * AgentPanel — slide-in side panel per (tab, agent).
 *
 * Shows a chat thread, recent page-change events, and suggested actions.
 * Chat messages are stored locally in browser-agent-store for PR 6;
 * PR 7 will wire real persistence and agent routing.
 */
import React, { useEffect, useRef, useState, useCallback, KeyboardEvent } from "react";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import type { AgentMessage, AgentEvent } from "@/stores/browser-agent-store";
import { useBrowserStore } from "@/stores/browser-store";
import { listAgents, unpinAgent, type AgentDto } from "@/lib/browser-agent-api";
import { listPushMutes, setPushMute, type PushMute } from "@/lib/browser-push-api";

export interface AgentPanelProps {
  windowId: string;
  tabId: string;
  /** Profile id needed for unpin requests. */
  profileId: string;
  pinnedAgentIds: string[];
}

// Stable empty arrays to avoid selector returning new references on every render
const EMPTY_MESSAGES: AgentMessage[] = [];
const EMPTY_EVENTS: AgentEvent[] = [];

/** Deterministic hue from an agent id string — mirrors AgentPresencePill. */
function agentColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  return `hsl(${hue}, 55%, 45%)`;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function eventIcon(kind: AgentEvent["kind"]): string {
  if (kind === "page-changed") return "🌐";
  if (kind === "url-changed") return "🔗";
  return "↕";
}

export function AgentPanel({ windowId, tabId, profileId, pinnedAgentIds }: AgentPanelProps) {
  const panelKey = `${windowId}:${tabId}`;

  const panel = useBrowserAgentStore((s) => s.panels[panelKey]);
  const closePanel = useBrowserAgentStore((s) => s.closePanel);
  const setActiveAgent = useBrowserAgentStore((s) => s.setActiveAgent);
  const setPanelWidth = useBrowserAgentStore((s) => s.setPanelWidth);
  const appendMessage = useBrowserAgentStore((s) => s.appendMessage);

  const [agents, setAgents] = useState<AgentDto[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [mutes, setMutes] = useState<PushMute[]>([]);
  const [mutesLoading, setMutesLoading] = useState(true);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Load agent list once on mount
  useEffect(() => {
    let cancelled = false;
    listAgents().then((list) => {
      if (!cancelled) setAgents(list);
    });
    return () => { cancelled = true; };
  }, []);

  // Load mutes once on mount.
  useEffect(() => {
    let cancelled = false;
    setMutesLoading(true);
    listPushMutes().then((list) => {
      if (!cancelled) {
        setMutes(list);
        setMutesLoading(false);
      }
    }).catch(() => {
      if (!cancelled) setMutesLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  // Determine active agent id — fall back to first pinned if not set
  const activeAgentId = panel?.activeAgentId ?? pinnedAgentIds[0] ?? null;
  const agentKey = activeAgentId ? `${windowId}:${tabId}:${activeAgentId}` : null;

  const messagesForKey = useBrowserAgentStore((s) =>
    agentKey ? s.messages[agentKey] : undefined,
  );
  const recentEventsForKey = useBrowserAgentStore((s) =>
    agentKey ? s.recentEvents[agentKey] : undefined,
  );
  const messages: AgentMessage[] = messagesForKey ?? EMPTY_MESSAGES;
  const recentEvents: AgentEvent[] = recentEventsForKey ?? EMPTY_EVENTS;

  // Scroll chat to bottom whenever messages change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  // Drag handle logic
  const isDraggingRef = useRef(false);
  const onMoveRef = useRef<((ev: MouseEvent) => void) | null>(null);
  const onUpRef = useRef<(() => void) | null>(null);

  // Remove any lingering drag listeners on unmount (e.g. panel closed mid-drag)
  useEffect(() => {
    return () => {
      isDraggingRef.current = false;
      if (onMoveRef.current) window.removeEventListener("mousemove", onMoveRef.current);
      if (onUpRef.current) window.removeEventListener("mouseup", onUpRef.current);
    };
  }, []);

  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDraggingRef.current = true;

      const onMove = (ev: MouseEvent) => {
        if (!isDraggingRef.current) return;
        // Panel is on the right; its width = viewport width - cursor X
        const newWidth = window.innerWidth - ev.clientX;
        setPanelWidth(windowId, tabId, newWidth);
      };

      const onUp = () => {
        isDraggingRef.current = false;
        if (onMoveRef.current) window.removeEventListener("mousemove", onMoveRef.current);
        if (onUpRef.current) window.removeEventListener("mouseup", onUpRef.current);
        onMoveRef.current = null;
        onUpRef.current = null;
      };

      onMoveRef.current = onMove;
      onUpRef.current = onUp;
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [windowId, tabId, setPanelWidth],
  );

  // Don't render when there's nothing to show
  if (pinnedAgentIds.length === 0) return null;
  if (!panel?.isOpen) return null;

  const panelWidth = panel.width;

  function getAgentName(id: string): string {
    return agents.find((a) => a.id === id)?.name ?? id;
  }

  function handleTabClick(agentId: string) {
    setActiveAgent(windowId, tabId, agentId);
  }

  function handleClose() {
    closePanel(windowId, tabId);
  }

  async function handleUnpin(agentId: string) {
    const ok = await unpinAgent(profileId, tabId, agentId);
    if (!ok) return;
    useBrowserStore.getState().removePinnedAgent(windowId, tabId, agentId);
    // If that was the last pinned agent, close the panel — there's nothing
    // to show in the body.
    const remaining = pinnedAgentIds.filter((a) => a !== agentId);
    if (remaining.length === 0) {
      closePanel(windowId, tabId);
      return;
    }
    // If we just unpinned the active agent, switch to the first remaining.
    const next = remaining[0];
    if (agentId === activeAgentId && next) {
      setActiveAgent(windowId, tabId, next);
    }
  }

  function sendMessage() {
    if (!inputValue.trim() || !activeAgentId) return;
    appendMessage(windowId, tabId, activeAgentId, {
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      author: "user",
      content: inputValue.trim(),
      timestamp: Date.now(),
    });
    setInputValue("");
    textareaRef.current?.focus();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function handleSummarisePage() {
    if (!activeAgentId) return;
    const lastPageEvent = [...recentEvents]
      .reverse()
      .find((ev) => ev.kind === "page-changed");
    const context = lastPageEvent
      ? ` (page: ${lastPageEvent.title ?? lastPageEvent.url ?? "unknown"})`
      : "";
    appendMessage(windowId, tabId, activeAgentId, {
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      author: "user",
      content: `Please summarise this page${context}.`,
      timestamp: Date.now(),
    });
  }

  const activeAgentName = activeAgentId ? getAgentName(activeAgentId) : "Agent";

  const MUTE_KINDS: Array<{ kind: PushMute["kind"]; label: string }> = [
    { kind: "chat", label: "Chat messages" },
    { kind: "drive-started", label: "Started driving" },
    { kind: "download-finished", label: "Download finished" },
  ];

  function isMuted(agentId: string, kind: PushMute["kind"]): boolean {
    return mutes.some((m) => m.agent_id === agentId && m.kind === kind);
  }

  async function handleToggleMute(agentId: string, kind: PushMute["kind"]) {
    const currentlyMuted = isMuted(agentId, kind);
    const nextMuted = !currentlyMuted;
    // Optimistic update
    setMutes((prev) => {
      const without = prev.filter((m) => !(m.agent_id === agentId && m.kind === kind));
      if (nextMuted) {
        return [...without, { agent_id: agentId, kind, muted_at: Date.now() }];
      }
      return without;
    });
    try {
      await setPushMute({ agent_id: agentId, kind, muted: nextMuted });
    } catch {
      // Revert on failure
      setMutes((prev) => {
        const without = prev.filter((m) => !(m.agent_id === agentId && m.kind === kind));
        if (currentlyMuted) {
          return [...without, { agent_id: agentId, kind, muted_at: Date.now() }];
        }
        return without;
      });
    }
  }

  return (
    <div
      role="complementary"
      aria-label="Agent panel"
      style={{ width: panelWidth, minWidth: panelWidth, maxWidth: panelWidth }}
      className="relative flex flex-col bg-shell-surface border-l border-shell-border-subtle h-full overflow-hidden flex-shrink-0"
    >
      {/* Drag handle */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={panelWidth}
        aria-valuemin={240}
        aria-valuemax={480}
        aria-label="Resize agent panel"
        onMouseDown={handleDragStart}
        className="absolute left-0 top-0 bottom-0 w-3 cursor-col-resize z-10 hover:bg-shell-border-subtle/30"
        style={{ cursor: "col-resize" }}
      />

      {/* Tab bar */}
      <div
        role="tablist"
        aria-label="Pinned agents"
        className="flex items-center gap-1 px-3 pt-2 pb-1 border-b border-shell-border-subtle overflow-x-auto flex-shrink-0"
      >
        {pinnedAgentIds.map((agentId) => {
          const isActive = agentId === activeAgentId;
          const name = getAgentName(agentId);
          const tabId_ = `agent-tab-${windowId}-${tabId}-${agentId}`;
          return (
            <div
              key={agentId}
              className={[
                "group flex items-center gap-1 px-2 py-1 rounded text-xs whitespace-nowrap transition-colors",
                isActive
                  ? "bg-shell-hover text-shell-text font-medium"
                  : "text-shell-text-secondary hover:bg-shell-hover hover:text-shell-text",
              ].join(" ")}
            >
              <button
                id={tabId_}
                role="tab"
                aria-selected={isActive}
                onClick={() => handleTabClick(agentId)}
                title={name}
                className="flex items-center gap-1 outline-none"
              >
                <span
                  aria-hidden="true"
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: agentColor(agentId) }}
                />
                <span className="max-w-[80px] truncate">{name}</span>
              </button>
              <button
                type="button"
                aria-label={`Unpin ${name}`}
                title={`Unpin ${name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  handleUnpin(agentId);
                }}
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 ml-0.5 text-[0.65rem] leading-none rounded hover:bg-shell-bg-deep px-1 py-0.5 transition-opacity"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-shell-border-subtle flex-shrink-0">
        <span className="text-xs font-medium text-shell-text truncate">{activeAgentName}</span>
        <button
          type="button"
          aria-label="Close agent panel"
          onClick={handleClose}
          className="text-shell-text-secondary hover:text-shell-text hover:bg-shell-hover rounded p-0.5 text-xs leading-none flex-shrink-0"
        >
          ✕
        </button>
      </div>

      {/* Tab panel body */}
      <div
        role="tabpanel"
        aria-labelledby={
          activeAgentId
            ? `agent-tab-${windowId}-${tabId}-${activeAgentId}`
            : undefined
        }
        className="flex flex-col flex-1 overflow-hidden"
      >
        {/* Recent events rail */}
        {recentEvents.length > 0 && (
          <div
            aria-label="Recent page events"
            className="flex gap-2 px-3 py-2 overflow-x-auto border-b border-shell-border-subtle flex-shrink-0"
          >
            {recentEvents.map((ev, i) => (
              <div
                key={i}
                title={ev.title ?? ev.url ?? ev.kind}
                className="flex items-center gap-1 bg-shell-bg-deep border border-shell-border-subtle rounded-full px-2 py-0.5 text-[10px] text-shell-text-secondary whitespace-nowrap flex-shrink-0"
              >
                <span aria-hidden="true">{eventIcon(ev.kind)}</span>
                <span className="max-w-[100px] truncate">
                  {ev.title ?? ev.url ?? ev.kind}
                </span>
                <span className="opacity-60 text-[9px]">{formatTime(ev.timestamp)}</span>
              </div>
            ))}
          </div>
        )}

        {/* Suggested actions */}
        <div className="flex flex-col gap-1 px-3 py-2 border-b border-shell-border-subtle flex-shrink-0">
          <span className="text-[10px] uppercase tracking-wide text-shell-text-secondary mb-1">
            Suggested
          </span>
          <button
            type="button"
            onClick={handleSummarisePage}
            className="text-left text-xs px-2 py-1 rounded hover:bg-shell-hover text-shell-text-secondary hover:text-shell-text transition-colors"
          >
            Summarise this page
          </button>
          <button
            type="button"
            title="Coming in PR 7"
            disabled
            className="text-left text-xs px-2 py-1 rounded text-shell-text-secondary opacity-50 cursor-not-allowed"
          >
            Send to {activeAgentName}
          </button>
          <button
            type="button"
            title="Coming in PR 7"
            disabled
            className="text-left text-xs px-2 py-1 rounded text-shell-text-secondary opacity-50 cursor-not-allowed"
          >
            Pin to Memory
          </button>
        </div>

        {/* Notifications section */}
        <div className="flex flex-col gap-1 px-3 py-2 border-b border-shell-border-subtle flex-shrink-0">
          <span className="text-[10px] uppercase tracking-wide text-shell-text-secondary mb-1">
            Notifications
          </span>
          {mutesLoading ? (
            <span className="text-[10px] text-shell-text-secondary italic">Loading…</span>
          ) : (
            pinnedAgentIds.map((agentId) => (
              <div key={agentId} className="flex flex-col gap-1">
                {pinnedAgentIds.length > 1 && (
                  <span className="text-[10px] font-medium text-shell-text-secondary truncate">
                    {getAgentName(agentId)}
                  </span>
                )}
                {MUTE_KINDS.map(({ kind, label }) => {
                  const checked = !isMuted(agentId, kind);
                  const toggleId = `notif-${windowId}-${tabId}-${agentId}-${kind}`;
                  return (
                    <label
                      key={kind}
                      htmlFor={toggleId}
                      className="flex items-center gap-2 cursor-pointer text-xs text-shell-text-secondary hover:text-shell-text"
                    >
                      <input
                        id={toggleId}
                        type="checkbox"
                        checked={checked}
                        onChange={() => handleToggleMute(agentId, kind)}
                        className="accent-accent"
                        aria-label={`${label} notifications for ${getAgentName(agentId)}`}
                      />
                      <span>{label}</span>
                    </label>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* Chat thread */}
        <div
          aria-label="Chat thread"
          className="flex-1 overflow-y-auto px-3 py-2 flex flex-col gap-2"
        >
          {messages.length === 0 && (
            <p className="text-[11px] text-shell-text-secondary italic text-center mt-4">
              No messages yet. Start a conversation below.
            </p>
          )}
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={[
                "flex flex-col max-w-[85%] gap-0.5",
                msg.author === "user" ? "self-end items-end" : "self-start items-start",
              ].join(" ")}
            >
              <div
                className={[
                  "rounded-lg px-2.5 py-1.5 text-xs break-words",
                  msg.author === "user"
                    ? "bg-shell-hover text-shell-text"
                    : "bg-shell-bg-deep text-shell-text border border-shell-border-subtle",
                ].join(" ")}
              >
                {msg.content}
              </div>
              <span className="text-[9px] text-shell-text-secondary opacity-70">
                {formatTime(msg.timestamp)}
              </span>
            </div>
          ))}
          <div ref={chatEndRef} />
        </div>

        {/* Chat input */}
        <div className="flex-shrink-0 border-t border-shell-border-subtle px-3 py-2">
          <label htmlFor={`agent-panel-input-${windowId}-${tabId}`} className="sr-only">
            Message {activeAgentName}
          </label>
          <textarea
            id={`agent-panel-input-${windowId}-${tabId}`}
            ref={textareaRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message ${activeAgentName}…`}
            rows={2}
            className="w-full bg-shell-bg-deep border border-shell-border-subtle rounded px-2 py-1.5 text-xs text-shell-text resize-none outline-none focus:border-accent placeholder:text-shell-text-secondary"
          />
          <div className="text-[9px] text-shell-text-secondary mt-0.5 opacity-60">
            Enter to send · Shift+Enter for newline
          </div>
        </div>
      </div>
    </div>
  );
}
