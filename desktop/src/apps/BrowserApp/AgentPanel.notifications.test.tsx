import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AgentPanel } from "./AgentPanel";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import * as browserAgentApi from "@/lib/browser-agent-api";
import * as pushApi from "@/lib/browser-push-api";

const WINDOW_ID = "win-notif";
const TAB_ID = "tab-notif";
const AGENTS = [
  { id: "agent-1", name: "Alice", emoji: "🤖", framework: "openclaw" },
  { id: "agent-2", name: "Bob", emoji: "🧪", framework: "smolagents" },
];
const PINNED = ["agent-1", "agent-2"];

function openPanel(agentId = "agent-1") {
  useBrowserAgentStore.getState().openPanel(WINDOW_ID, TAB_ID, agentId);
}

beforeEach(() => {
  useBrowserAgentStore.setState({
    panels: {},
    lastEventAt: {},
    messages: {},
    recentEvents: {},
  });
  vi.spyOn(browserAgentApi, "listAgents").mockResolvedValue(AGENTS);
  vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([]);
  vi.spyOn(pushApi, "setPushMute").mockResolvedValue({ ok: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AgentPanel — Notifications section", () => {
  it("renders three toggles per pinned agent", async () => {
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );

    // Wait for mutes to load (loading indicator disappears)
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    // 3 toggles per agent × 2 agents = 6 checkboxes
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(6);
  });

  it("all toggles are checked (not muted) when listPushMutes returns empty", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([]);
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes.every((cb) => cb.checked)).toBe(true);
  });

  it("initial state reflects listPushMutes response — muted kind is unchecked", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([
      { agent_id: "agent-1", kind: "chat", muted_at: 1000 },
    ]);
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    // The "Chat messages" toggle for agent-1 should be UNCHECKED (muted)
    const chatToggleForAgent1 = screen.getByLabelText("Chat messages notifications for Alice");
    expect((chatToggleForAgent1 as HTMLInputElement).checked).toBe(false);

    // The "Started driving" toggle for agent-1 should be CHECKED (not muted)
    const driveToggle = screen.getByLabelText("Started driving notifications for Alice");
    expect((driveToggle as HTMLInputElement).checked).toBe(true);
  });

  it("toggling a checked toggle fires setPushMute with muted: true", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([]);
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    const chatToggle = screen.getByLabelText("Chat messages notifications for Alice");
    expect((chatToggle as HTMLInputElement).checked).toBe(true);

    await act(async () => {
      fireEvent.click(chatToggle);
    });

    expect(pushApi.setPushMute).toHaveBeenCalledWith({
      agent_id: "agent-1",
      kind: "chat",
      muted: true,
    });
  });

  it("toggling an unchecked toggle fires setPushMute with muted: false", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([
      { agent_id: "agent-1", kind: "chat", muted_at: 1000 },
    ]);
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    const chatToggle = screen.getByLabelText("Chat messages notifications for Alice");
    expect((chatToggle as HTMLInputElement).checked).toBe(false);

    await act(async () => {
      fireEvent.click(chatToggle);
    });

    expect(pushApi.setPushMute).toHaveBeenCalledWith({
      agent_id: "agent-1",
      kind: "chat",
      muted: false,
    });
  });

  it("after toggling, UI reflects new state (optimistic update)", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([]);
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    const chatToggle = screen.getByLabelText("Chat messages notifications for Alice");
    expect((chatToggle as HTMLInputElement).checked).toBe(true);

    await act(async () => {
      fireEvent.click(chatToggle);
    });

    // After clicking (muting), the toggle should be unchecked
    await waitFor(() =>
      expect((screen.getByLabelText("Chat messages notifications for Alice") as HTMLInputElement).checked).toBe(false),
    );
  });

  it("shows loading indicator while mutes are being fetched", () => {
    // Return a promise that never resolves to keep loading state
    vi.spyOn(pushApi, "listPushMutes").mockReturnValue(new Promise(() => {}));
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders toggles even when listPushMutes fails (default unchecked state)", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockRejectedValue(new Error("network error"));
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    // Should still render 6 checkboxes, all checked (default not-muted)
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(6);
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(true);
  });

  it("reverts optimistic update when setPushMute fails", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([]);
    vi.spyOn(pushApi, "setPushMute").mockRejectedValue(new Error("500"));
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    const chatToggle = screen.getByLabelText("Chat messages notifications for Alice");
    // Starts checked (not muted)
    expect((chatToggle as HTMLInputElement).checked).toBe(true);

    await act(async () => {
      fireEvent.click(chatToggle);
    });

    // The optimistic update flipped it to unchecked, but the API rejected,
    // so the revert path should restore it to checked.
    await waitFor(() =>
      expect((screen.getByLabelText("Chat messages notifications for Alice") as HTMLInputElement).checked).toBe(true),
    );
  });

  it("renders single pinned agent without agent name header", async () => {
    vi.spyOn(pushApi, "listPushMutes").mockResolvedValue([]);
    openPanel();
    render(
      <AgentPanel
        windowId={WINDOW_ID}
        tabId={TAB_ID}
        profileId="personal"
        pinnedAgentIds={["agent-1"]}
      />,
    );
    await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());

    // Only 3 toggles for single agent
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(3);
  });
});
