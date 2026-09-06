/**
 * PageContextMenu — right-click context menu for the proxied browser page.
 *
 * Shows one "Send to <agentName>" entry per agent (via listAgents) and a
 * "Pin to Memory" entry. Clicking an agent entry:
 *   1. Calls extractReadable to get the Readability extract of the page.
 *   2. Dispatches `taos:open-messages` with channelId + prefillCard so that
 *      MessagesApp (PR 7) can open the chat pre-filled with the page card.
 *
 * Clicking "Pin to Memory" dispatches `taos:open-memory` similarly.
 * Neither event has a receiving listener yet — that's PR 7 scope.
 *
 * PR 6 limitation: the menu is triggered by right-clicking the iframe wrapper,
 * not the iframe content itself. The iframe is sandboxed (no allow-same-origin)
 * so its own contextmenu events never reach the parent. PR 7 will add a
 * copilot.js → parent postMessage forwarding so right-click anywhere on the
 * page reaches the parent's onContextMenu handler.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { listAgents, type AgentDto } from "@/lib/browser-agent-api";
import { extractReadable } from "@/lib/browser-extract-api";

export interface PageContextMenuProps {
  windowId: string;
  tabId: string;
  /** Profile ID — needed for extractReadable */
  profileId: string;
  /** URL of the proxied page (from the active tab) */
  url: string;
  /** Page title from the tab */
  title: string;
  /**
   * User's text selection if any. In PR 6 this is always null because the
   * iframe is sandboxed and we cannot read iframe selection from the parent.
   * PR 7 will forward the selection via copilot.js postMessage.
   */
  selection: string | null;
  /** Coordinates where the menu opens (relative to viewport) */
  x: number;
  y: number;
  onClose(): void;
}

export function PageContextMenu({
  windowId: _windowId,
  tabId: _tabId,
  profileId,
  url,
  title,
  selection: _selection,
  x,
  y,
  onClose,
}: PageContextMenuProps) {
  const [agents, setAgents] = useState<AgentDto[]>([]);
  const [focusedIdx, setFocusedIdx] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  // Load agents on mount
  useEffect(() => {
    let cancelled = false;
    listAgents().then((list) => {
      if (!cancelled) setAgents(list);
    });
    return () => { cancelled = true; };
  }, []);

  // Reset focus when agents load
  useEffect(() => {
    setFocusedIdx(0);
  }, [agents.length]);

  // Click-outside to close (deferred so the triggering click doesn't close immediately)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const timer = setTimeout(() => {
      document.addEventListener("mousedown", handler);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("mousedown", handler);
    };
  }, [onClose]);

  // Build the item list: [Send to <agent>, ...] + [Pin to Memory]
  const totalItems = agents.length + 1; // +1 for Pin to Memory
  const PIN_IDX = agents.length;

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIdx((i) => (i + 1) % totalItems);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIdx((i) => (i - 1 + totalItems) % totalItems);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        triggerItem(focusedIdx);
        return;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [focusedIdx, totalItems, agents],
  );

  async function triggerItem(idx: number) {
    if (idx === PIN_IDX) {
      await handlePinToMemory();
    } else {
      const agent = agents[idx];
      if (agent) await handleSendToAgent(agent);
    }
  }

  async function handleSendToAgent(agent: AgentDto) {
    const result = await extractReadable(profileId, url);
    if (!mountedRef.current) return;
    window.dispatchEvent(
      new CustomEvent("taos:open-messages", {
        detail: {
          channelId: agent.name,
          prefillCard: {
            title,
            url,
            extract: result?.text ?? "",
          },
        },
      }),
    );
    onClose();
  }

  async function handlePinToMemory() {
    const result = await extractReadable(profileId, url);
    if (!mountedRef.current) return;
    window.dispatchEvent(
      new CustomEvent("taos:open-memory", {
        detail: {
          prefillCard: {
            title,
            url,
            extract: result?.text ?? "",
          },
        },
      }),
    );
    onClose();
  }

  // Clamp position to stay within viewport
  const MENU_WIDTH = 220;
  const MENU_ITEM_HEIGHT = 36;
  const MENU_HEIGHT = (totalItems + 1) * MENU_ITEM_HEIGHT; // rough estimate
  const left = Math.min(x, Math.max(0, window.innerWidth - MENU_WIDTH));
  const top = Math.min(y, Math.max(0, window.innerHeight - MENU_HEIGHT));

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label="Page actions"
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      style={{
        position: "fixed",
        left,
        top,
        width: MENU_WIDTH,
        zIndex: 9999,
      }}
      className="bg-shell-surface border border-shell-border-subtle rounded-lg shadow-lg py-1 outline-none"
    >
      {agents.map((agent, idx) => (
        <button
          key={agent.id}
          role="menuitem"
          aria-current={focusedIdx === idx ? "true" : undefined}
          type="button"
          onClick={() => handleSendToAgent(agent)}
          onMouseEnter={() => setFocusedIdx(idx)}
          className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 cursor-pointer ${
            focusedIdx === idx
              ? "bg-shell-hover text-shell-text"
              : "text-shell-text-secondary hover:bg-shell-hover hover:text-shell-text"
          }`}
        >
          {agent.emoji && <span aria-hidden="true">{agent.emoji}</span>}
          <span className="flex-1 min-w-0 truncate">Send to {agent.name}</span>
        </button>
      ))}

      {/* Divider before Pin to Memory */}
      {agents.length > 0 && (
        <div className="border-t border-shell-border-subtle my-1" />
      )}

      <button
        role="menuitem"
        aria-current={focusedIdx === PIN_IDX ? "true" : undefined}
        type="button"
        onClick={() => handlePinToMemory()}
        onMouseEnter={() => setFocusedIdx(PIN_IDX)}
        className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 cursor-pointer ${
          focusedIdx === PIN_IDX
            ? "bg-shell-hover text-shell-text"
            : "text-shell-text-secondary hover:bg-shell-hover hover:text-shell-text"
        }`}
      >
        <span aria-hidden="true">📌</span>
        <span>Pin to Memory</span>
      </button>
    </div>
  );
}
