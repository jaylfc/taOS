import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";

// Stub the heavy data hooks / stores MessagesApp transitively imports so the
// test only cares about the mobile shell structure. Anything that would fire
// a network request or open a WebSocket is replaced with a no-op.
vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: vi.fn(),
}));

vi.mock("@/hooks/use-visual-viewport", () => ({
  useVisualViewport: () => ({ height: 800, keyboardInset: 0 }),
}));

vi.mock("@/hooks/use-chat-notifications", () => ({
  useChatNotifications: () => ({ notify: vi.fn() }),
}));

vi.mock("@/hooks/use-thread-panel", () => ({
  useThreadPanel: () => ({
    openThread: null,
    openThreadFor: vi.fn(),
    closeThread: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-bus-channels", () => ({
  useBusChannels: () => ({ channels: [], loading: false }),
}));

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ openWindow: vi.fn() }),
}));

vi.mock("@/hooks/use-drop-target", () => ({
  useDropTarget: () => ({
    isOver: false,
    isValidTarget: false,
    dropHandlers: {
      onDragEnter: vi.fn(),
      onDragOver: vi.fn(),
      onDragLeave: vi.fn(),
      onDrop: vi.fn(),
    },
  }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: () => undefined,
}));

vi.mock("../chat/MessageList", () => ({
  MessageList: () => <div data-testid="message-list" />,
}));

vi.mock("../chat/MessageInput", () => ({
  MessageInput: () => <div data-testid="message-input" />,
}));

// Surface the listTitle so the test can verify the production code actually
// routed through this component (vs a desktop-branch element wearing the
// same testid).
vi.mock("@/components/mobile/MobileSplitView", () => ({
  MobileSplitView: ({ listTitle, list, detail }: { listTitle?: string; list?: React.ReactNode; detail?: React.ReactNode }) => (
    <div data-testid="mobile-split-view" data-list-title={listTitle}>
      <div data-testid="msv-list">{list}</div>
      <div data-testid="msv-detail">{detail}</div>
    </div>
  ),
}));

vi.mock("@/lib/api", () => ({
  attachmentFromPath: vi.fn(),
  uploadDiskFile: vi.fn(),
  uploadFileAttachment: vi.fn(),
}));

vi.mock("../MessagesApp.stallWatch", () => ({
  useStallWatch: () => ({ stallInfo: null }),
  computeStallInfo: () => null,
}));

vi.mock("../MessagesApp.a2aSelection", () => ({
  selectInitialBusChannel: vi.fn(),
  selectFirstBoundChannel: vi.fn(),
}));

vi.mock("../chat/ChannelSidebar", () => ({
  ChannelSidebar: ({ liveAgents, agentRosterReady }: { liveAgents: unknown; agentRosterReady: unknown }) => (
    <div
      data-testid="channel-sidebar"
      data-live-agents={JSON.stringify(liveAgents)}
      data-agent-roster-ready={String(agentRosterReady)}
    />
  ),
}));

vi.mock("../chat/A2aBusPanel", () => ({
  A2aBusMessageView: () => <div data-testid="a2a-bus-view" />,
  useBusChannels: () => ({ channels: [], loading: false }),
}));

import { useIsMobile } from "@/hooks/use-is-mobile";
import { MessagesApp } from "../MessagesApp";

describe("MessagesApp mobile shell", () => {
  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
  });

  it("renders the mobile split-view with a Chats list pane when isMobile=true", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(<MessagesApp windowId="test" />);
    const splitView = screen.getByTestId("mobile-split-view");
    expect(splitView).toBeInTheDocument();
    expect(splitView).toHaveAttribute("data-list-title", "Messages");
    // The list pane wraps the channel sidebar.
    expect(screen.getByTestId("channel-sidebar")).toBeInTheDocument();
  });

  it("renders the channel sidebar on desktop too (mobile shell is gated on isMobile)", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<MessagesApp windowId="test" />);
    // The channel sidebar still renders in the desktop side-by-side layout
    // (it is the same list, just inside a different split-view variant).
    expect(screen.getByTestId("channel-sidebar")).toBeInTheDocument();
  });
});

describe("MessagesApp fetchAgentLists - archived fetch failure", () => {
  let originalFetch: typeof global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function setupFetchMock(archivedBehavior: "500" | "reject" | "bad-json" | "neverSettle") {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/api/agents") && urlStr.includes("include_guests=1")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([{ name: "some-agent", display_name: "Some Agent", status: "running" }]),
        } as unknown as Response);
      }
      if (urlStr.includes("/api/agents/archived")) {
        if (archivedBehavior === "500") {
          return Promise.resolve({
            ok: false,
            status: 500,
            headers: { get: () => "application/json" },
            json: () => Promise.resolve({ error: "internal error" }),
          } as unknown as Response);
        }
        if (archivedBehavior === "reject") {
          return Promise.reject(new TypeError("network"));
        }
        if (archivedBehavior === "bad-json") {
          return Promise.resolve({
            ok: true,
            headers: { get: () => "application/json" },
            json: () => Promise.reject(new SyntaxError("bad json")),
          } as unknown as Response);
        }
        if (archivedBehavior === "neverSettle") {
          return new Promise(() => {}); // Never resolves
        }
      }
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ channels: [], unread: {} }),
        } as unknown as Response);
      }
      if (urlStr.includes("/api/chat/unread")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ unread: {} }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({ error: "not found" }),
      } as unknown as Response);
    });
  }

  async function runCommonAssertions() {
    await waitFor(() => {
      expect(screen.getByTestId("mobile-split-view")).toBeInTheDocument();
      expect(screen.getByTestId("channel-sidebar")).toBeInTheDocument();
    });

    await waitFor(() => {
      const sidebar = screen.getByTestId("channel-sidebar");
      const liveAgents = JSON.parse(sidebar.getAttribute("data-live-agents") || "[]");
      expect(liveAgents).toHaveLength(1);
      expect(liveAgents[0].name).toBe("some-agent");
    });

    await waitFor(() => {
      const sidebar = screen.getByTestId("channel-sidebar");
      expect(sidebar.getAttribute("data-agent-roster-ready")).toBe("false");
    });
  }

  it("renders without error when archived fetch fails (500) and live agent fetch succeeds", async () => {
    setupFetchMock("500");
    render(<MessagesApp windowId="test" />);
    await runCommonAssertions();
  });

  it("populates liveAgents when archived fetch rejects with network error", async () => {
    setupFetchMock("reject");
    render(<MessagesApp windowId="test" />);
    await runCommonAssertions();
  });

  it("populates liveAgents when archived fetch returns ok but json() throws", async () => {
    setupFetchMock("bad-json");
    render(<MessagesApp windowId="test" />);
    await runCommonAssertions();
  });

  it("populates liveAgents when archived fetch never settles", async () => {
    setupFetchMock("neverSettle");
    render(<MessagesApp windowId="test" />);
    await runCommonAssertions();
  });
});
