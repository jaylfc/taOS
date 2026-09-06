import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { AgentPanel } from "./AgentPanel";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";
import * as browserAgentApi from "@/lib/browser-agent-api";
import * as pushApi from "@/lib/browser-push-api";

const WINDOW_ID = "win-1";
const TAB_ID = "tab-1";
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

describe("AgentPanel", () => {
  it("renders nothing when panel is closed", () => {
    // Panel not open — store default has no entry
    const { container } = render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when pinnedAgentIds is empty", () => {
    openPanel();
    const { container } = render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders panel when isOpen is true", () => {
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    expect(screen.getByRole("complementary")).toBeInTheDocument();
  });

  it("renders one tab per pinned agent", () => {
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(PINNED.length);
  });

  it("active tab highlighted with aria-selected=true", () => {
    openPanel("agent-1");
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const tabs = screen.getAllByRole("tab");
    // agent-1 is active
    const activeTab = tabs.find((t) => t.getAttribute("aria-selected") === "true");
    expect(activeTab).toBeTruthy();
    const inactiveTabs = tabs.filter((t) => t.getAttribute("aria-selected") === "false");
    expect(inactiveTabs.length).toBe(PINNED.length - 1);
  });

  it("clicking a tab calls setActiveAgent", () => {
    openPanel("agent-1");
    const setActiveAgentSpy = vi.spyOn(useBrowserAgentStore.getState(), "setActiveAgent");
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const tabs = screen.getAllByRole("tab");
    // Click the second tab (agent-2)
    fireEvent.click(tabs[1]);
    expect(setActiveAgentSpy).toHaveBeenCalledWith(WINDOW_ID, TAB_ID, "agent-2");
  });

  it("close button calls closePanel", () => {
    openPanel();
    const closePanelSpy = vi.spyOn(useBrowserAgentStore.getState(), "closePanel");
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    fireEvent.click(screen.getByLabelText("Close agent panel"));
    expect(closePanelSpy).toHaveBeenCalledWith(WINDOW_ID, TAB_ID);
  });

  it("renders recent events from store", () => {
    openPanel("agent-1");
    useBrowserAgentStore.getState().appendEvent(WINDOW_ID, TAB_ID, "agent-1", {
      kind: "page-changed",
      title: "Example Page",
      url: "https://example.com",
      timestamp: Date.now(),
    });
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    expect(screen.getByText(/Example Page/)).toBeInTheDocument();
  });

  it("Summarise this page button appends a user message with the page extract", () => {
    openPanel("agent-1");
    const ts = Date.now();
    useBrowserAgentStore.getState().appendEvent(WINDOW_ID, TAB_ID, "agent-1", {
      kind: "page-changed",
      title: "My Article",
      url: "https://news.test/article",
      timestamp: ts,
    });
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    fireEvent.click(screen.getByText("Summarise this page"));
    const msgs = useBrowserAgentStore.getState().messages["win-1:tab-1:agent-1"];
    expect(msgs).toHaveLength(1);
    expect(msgs[0].author).toBe("user");
    expect(msgs[0].content).toContain("summarise");
    expect(msgs[0].content).toContain("My Article");
  });

  it("chat textarea Enter sends a user message; Shift+Enter inserts newline", () => {
    openPanel("agent-1");
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const textarea = screen.getByRole("textbox");

    // Type a message
    fireEvent.change(textarea, { target: { value: "Hello agent" } });

    // Shift+Enter should NOT send
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(useBrowserAgentStore.getState().messages["win-1:tab-1:agent-1"] ?? []).toHaveLength(0);

    // Enter should send
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    const msgs = useBrowserAgentStore.getState().messages["win-1:tab-1:agent-1"];
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toBe("Hello agent");
    expect(msgs[0].author).toBe("user");
  });

  it("sent message appears in the thread", () => {
    openPanel("agent-1");
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "Visible message" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(screen.getByText("Visible message")).toBeInTheDocument();
  });

  it("drag handle updates setPanelWidth (mock mousemove)", () => {
    openPanel("agent-1");
    const setPanelWidthSpy = vi.spyOn(useBrowserAgentStore.getState(), "setPanelWidth");
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const handle = screen.getByRole("separator");

    // Start drag
    fireEvent.mouseDown(handle, { clientX: 1000 });
    // Move left by 200px — panel width = window.innerWidth - clientX = 1024 - 800 = 224 (clamped to 240)
    fireEvent.mouseMove(window, { clientX: 800 });
    fireEvent.mouseUp(window);

    // setPanelWidth should have been called with a number
    expect(setPanelWidthSpy).toHaveBeenCalledWith(
      WINDOW_ID,
      TAB_ID,
      expect.any(Number),
    );
  });

  it("removes drag listeners on unmount during active drag (no leak)", () => {
    openPanel("agent-1");
    const setPanelWidthSpy = vi.spyOn(useBrowserAgentStore.getState(), "setPanelWidth");
    const { unmount } = render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const handle = screen.getByRole("separator");

    // Start drag but do NOT fire mouseup
    fireEvent.mouseDown(handle, { clientX: 1000 });

    // Capture call count after mousedown (before mousemove)
    const callsBefore = setPanelWidthSpy.mock.calls.length;

    // Unmount mid-drag — listeners should be cleaned up
    unmount();

    // Fire a mousemove on window — should NOT trigger setPanelWidth
    fireEvent.mouseMove(window, { clientX: 700 });

    expect(setPanelWidthSpy.mock.calls.length).toBe(callsBefore);
  });

  it("two rapid Summarise clicks produce two distinct messages", () => {
    openPanel("agent-1");
    useBrowserAgentStore.getState().appendEvent(WINDOW_ID, TAB_ID, "agent-1", {
      kind: "page-changed",
      title: "Some Article",
      url: "https://some.test/article",
      timestamp: Date.now(),
    });
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const btn = screen.getByText("Summarise this page");

    // Click twice in the same render cycle
    fireEvent.click(btn);
    fireEvent.click(btn);

    const msgs = useBrowserAgentStore.getState().messages["win-1:tab-1:agent-1"];
    expect(msgs).toHaveLength(2);
    // IDs must be distinct
    expect(msgs[0].id).not.toBe(msgs[1].id);
  });

  it("role='complementary' + aria-label='Agent panel'", () => {
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    const panel = screen.getByRole("complementary");
    expect(panel.getAttribute("aria-label")).toBe("Agent panel");
  });

  it("tablist + role='tab' + role='tabpanel' present", () => {
    openPanel();
    render(
      <AgentPanel windowId={WINDOW_ID} tabId={TAB_ID} profileId="personal" pinnedAgentIds={PINNED} />,
    );
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").length).toBeGreaterThan(0);
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
  });
});
