import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AgentPresencePill } from "./AgentPresencePill";
import { useBrowserAgentStore, WATCHING_DECAY_MS } from "@/stores/browser-agent-store";
import * as browserAgentApi from "@/lib/browser-agent-api";

const WINDOW_ID = "win-1";
const TAB_ID = "tab-1";

const AGENTS = [
  { id: "agent-1", name: "Alice", emoji: "🤖", framework: "openclaw" },
  { id: "agent-2", name: "Bob", emoji: "🧪", framework: "smolagents" },
  { id: "agent-3", name: "Carol", emoji: "🔗", framework: "pocketflow" },
  { id: "agent-4", name: "Dave", emoji: "🌳", framework: "langroid" },
  { id: "agent-5", name: "Eve", emoji: "💬", framework: "openai-agents-sdk" },
];

beforeEach(() => {
  useBrowserAgentStore.setState({ panels: {}, lastEventAt: {} });
  vi.spyOn(browserAgentApi, "listAgents").mockResolvedValue(AGENTS);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("AgentPresencePill", () => {
  it("renders nothing when pinnedAgentIds is empty", () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={[]}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders one avatar when one agent pinned", async () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      expect(btn).toBeTruthy();
      // Should have exactly 1 avatar
      const avatars = btn.querySelectorAll("[data-testid='agent-avatar']");
      expect(avatars).toHaveLength(1);
    });
  });

  it("renders four stacked avatars when four agents pinned", async () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1", "agent-2", "agent-3", "agent-4"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      const avatars = btn.querySelectorAll("[data-testid='agent-avatar']");
      expect(avatars).toHaveLength(4);
    });
  });

  it("displays at most 4 avatars even if more passed", async () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1", "agent-2", "agent-3", "agent-4", "agent-5"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      const avatars = btn.querySelectorAll("[data-testid='agent-avatar']");
      expect(avatars).toHaveLength(4);
    });
  });

  it("aria-label lists pinned agent names", async () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1", "agent-2", "agent-3"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      const label = btn.getAttribute("aria-label");
      expect(label).toContain("3 agents pinned");
      expect(label).toContain("Alice");
      expect(label).toContain("Bob");
      expect(label).toContain("Carol");
    });
  });

  it("click toggles the panel via togglePanel store action", async () => {
    const toggleSpy = vi.spyOn(useBrowserAgentStore.getState(), "togglePanel");
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1", "agent-2"]}
      />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button"));
    expect(toggleSpy).toHaveBeenCalledWith(WINDOW_ID, TAB_ID, "agent-1");
  });

  it("shows the active background tint when panel is open", async () => {
    useBrowserAgentStore.setState({
      panels: {
        [`${WINDOW_ID}:${TAB_ID}`]: { isOpen: true, activeAgentId: "agent-1", width: 280 },
      },
      lastEventAt: {},
    });
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      expect(btn.className).toContain("bg-accent-glow");
    });
  });

  it("shows the watching pulse when any pinned agent is in watching state", async () => {
    // Bump an event for agent-2 so it's "watching"
    useBrowserAgentStore.getState().bumpEventAt(WINDOW_ID, TAB_ID, "agent-2");
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1", "agent-2"]}
      />,
    );
    await waitFor(() => {
      const dot = screen.getByTestId("presence-dot");
      expect(dot.className).toContain("animate-pulse");
    });
  });

  it("does NOT show the pulse when no agent is watching", async () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1"]}
      />,
    );
    await waitFor(() => {
      const dot = screen.getByTestId("presence-dot");
      expect(dot.className).not.toContain("animate-pulse");
    });
  });

  it("watching pulse decays after WATCHING_DECAY_MS", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const now = 1_000_000;
    vi.setSystemTime(now);

    useBrowserAgentStore.getState().bumpEventAt(WINDOW_ID, TAB_ID, "agent-1", now);

    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1"]}
      />,
    );

    // Let the async listAgents mock resolve and initial effects run
    await act(async () => {
      await Promise.resolve();
    });

    const dot = screen.getByTestId("presence-dot");
    expect(dot.className).toContain("animate-pulse");

    // Advance system time past decay window and fire the decay timer
    vi.setSystemTime(now + WATCHING_DECAY_MS + 100);
    await act(async () => {
      vi.runAllTimers();
    });

    const dotAfter = screen.getByTestId("presence-dot");
    expect(dotAfter.className).not.toContain("animate-pulse");
  });

  it("aria-haspopup=dialog and aria-expanded reflects panel state", async () => {
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      expect(btn.getAttribute("aria-haspopup")).toBe("dialog");
      expect(btn.getAttribute("aria-expanded")).toBe("false");
    });

    // Open the panel
    act(() => {
      useBrowserAgentStore.setState({
        panels: {
          [`${WINDOW_ID}:${TAB_ID}`]: { isOpen: true, activeAgentId: "agent-1", width: 280 },
        },
        lastEventAt: {},
      });
    });

    await waitFor(() => {
      const btn = screen.getByRole("button");
      expect(btn.getAttribute("aria-expanded")).toBe("true");
    });
  });

  it("falls back to placeholder rendering if listAgents returns []", async () => {
    vi.spyOn(browserAgentApi, "listAgents").mockResolvedValue([]);
    render(
      <AgentPresencePill
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        pinnedAgentIds={["agent-1", "agent-2"]}
      />,
    );
    await waitFor(() => {
      const btn = screen.getByRole("button");
      // Fallback: should still render 2 avatars (one per pinned id)
      const avatars = btn.querySelectorAll("[data-testid='agent-avatar']");
      expect(avatars).toHaveLength(2);
    });
  });
});
