import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";
import * as a2aSelection from "../MessagesApp.a2aSelection";

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
  MessageList: ({ hideHeader, pinnedPopoverOpen }: { hideHeader?: boolean; pinnedPopoverOpen?: boolean }) => (
    <div data-testid="message-list" data-hide-header={String(!!hideHeader)} data-pin-open={String(!!pinnedPopoverOpen)} />
  ),
}));

vi.mock("../chat/MessageInput", () => ({
  MessageInput: ({
    onToggleSearch,
    onTogglePinned,
    onSend,
    onFileUpload,
  }: {
    onToggleSearch?: () => void;
    onTogglePinned?: () => void;
    onSend?: () => void;
    onFileUpload?: () => void;
  }) => (
    <div data-testid="message-input">
      <button type="button" onClick={onToggleSearch}>mock-search</button>
      <button type="button" onClick={onTogglePinned}>mock-pinned</button>
      <button type="button" onClick={onFileUpload}>mock-upload</button>
      <button type="button" onClick={onSend}>mock-send</button>
    </div>
  ),
}));

// Surface the listTitle so the test can verify the production code actually
// routed through this component (vs a desktop-branch element wearing the
// same testid).
vi.mock("@/components/mobile/MobileSplitView", () => ({
  MobileSplitView: ({ listTitle, list, detail, hideDetailNav }: { listTitle?: string; list?: React.ReactNode; detail?: React.ReactNode; hideDetailNav?: boolean }) => (
    <div data-testid="mobile-split-view" data-list-title={listTitle} data-hide-detail-nav={String(!!hideDetailNav)}>
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

vi.mock("@/shell/file-picker-api", () => ({
  openFilePicker: vi.fn().mockResolvedValue([
    { source: "disk", file: new File([], "a.png") },
  ]),
}));

vi.mock("../MessagesApp.stallWatch", () => ({
  useStallWatch: () => ({ stallInfo: null }),
  computeStallInfo: () => null,
  pickWatchAgent: vi.fn(),
}));

vi.mock("../MessagesApp.a2aSelection", () => ({
  selectInitialBusChannel: vi.fn(),
  selectFirstBoundChannel: vi.fn(),
  readLastChannel: vi.fn(),
  findA2aChannelId: vi.fn(),
  writeLastChannel: vi.fn(),
}));

vi.mock("../chat/ChannelSidebar", () => ({
  ChannelSidebar: ({
    liveAgents,
    agentRosterReady,
    onSelectChannel,
  }: {
    liveAgents: unknown;
    agentRosterReady: unknown;
    onSelectChannel?: (id: string) => void;
  }) => (
    <div
      data-testid="channel-sidebar"
      data-live-agents={JSON.stringify(liveAgents)}
      data-agent-roster-ready={String(agentRosterReady)}
    >
      <button type="button" onClick={() => onSelectChannel?.("ch-g")}>open-group</button>
      <button type="button" onClick={() => onSelectChannel?.("ch-dm")}>open-archived-dm</button>
    </div>
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

describe("MessagesApp mobile thread header", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      const ok = (body: unknown) =>
        Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(body),
        } as unknown as Response);
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages")) {
        return ok({
          channels: [
            { id: "ch-g", name: "design", type: "group", members: ["user", "tom", "lin"] },
          ],
          unread: {},
        });
      }
      if (urlStr.includes("/api/chat/unread")) return ok({ unread: {} });
      if (urlStr.includes("/api/agents")) return ok([]);
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  async function openGroup() {
    render(<MessagesApp windowId="test" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "open-group" })).toBeInTheDocument());
    expect(screen.getByTestId("mobile-split-view")).toHaveAttribute("data-hide-detail-nav", "false");
    fireEvent.click(screen.getByRole("button", { name: "open-group" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());
  }

  it("shows one header row: a Back to chats button, the title and the member count", async () => {
    await openGroup();
    const header = screen.getByRole("banner");
    expect(header).toHaveTextContent("design");
    expect(header).toHaveTextContent("3 members");
    // The old text-link back row ("< Messages") is not rendered.
    expect(screen.queryByRole("button", { name: /^Back to Messages$/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    // The split view's own detail nav bar (the "< Messages" row) is suppressed.
    expect(screen.getByTestId("mobile-split-view")).toHaveAttribute("data-hide-detail-nav", "true");
  });

  it("wires the composer search action to the search panel", async () => {
    await openGroup();
    fireEvent.click(screen.getByRole("button", { name: "mock-search" }));
    await waitFor(() => expect(document.getElementById("search-panel")).toBeInTheDocument());
  });

  it("hides the list header on mobile and the Pinned messages action opens the pinned popover", async () => {
    await openGroup();
    const list = screen.getByTestId("message-list");
    expect(list).toHaveAttribute("data-hide-header", "true");
    expect(list).toHaveAttribute("data-pin-open", "false");
    fireEvent.click(screen.getByRole("button", { name: "mock-pinned" }));
    await waitFor(() => expect(screen.getByTestId("message-list")).toHaveAttribute("data-pin-open", "true"));
  });

  it("Back to chats returns to the list", async () => {
    await openGroup();
    fireEvent.click(screen.getByRole("button", { name: "Back to chats" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Back to chats" })).not.toBeInTheDocument());
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
    await waitFor(() => {
      const sidebar = screen.getByTestId("channel-sidebar");
      const liveAgents = JSON.parse(sidebar.getAttribute("data-live-agents") || "[]");
      expect(liveAgents).toHaveLength(1);
      expect(liveAgents[0].name).toBe("some-agent");
      expect(sidebar.getAttribute("data-agent-roster-ready")).toBe("false");
    });
  });

  it("populates liveAgents and archivedAgents when both endpoints return ok without content-type header", async () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("/api/agents") && urlStr.includes("include_guests=1")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => null },
          json: () => Promise.resolve([{ name: "live-agent", display_name: "Live Agent", status: "running" }]),
        } as unknown as Response);
      }
      if (urlStr.includes("/api/agents/archived")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => null },
          json: () => Promise.resolve([]),
        } as unknown as Response);
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

    render(<MessagesApp windowId="test" />);

    await waitFor(() => {
      const sidebar = screen.getByTestId("channel-sidebar");
      const liveAgents = JSON.parse(sidebar.getAttribute("data-live-agents") || "[]");
      expect(liveAgents).toHaveLength(1);
      expect(liveAgents[0].name).toBe("live-agent");
    });

    await waitFor(() => {
      const sidebar = screen.getByTestId("channel-sidebar");
      expect(sidebar.getAttribute("data-agent-roster-ready")).toBe("true");
    });
  });
});

describe("MessagesApp mobile archived DM header", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      const ok = (body: unknown) =>
        Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(body),
        } as unknown as Response);
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages")) {
        return ok({
          channels: [
            {
              id: "ch-dm",
              name: "old-bot",
              type: "dm",
              members: ["user", "old-bot"],
              settings: { archived_agent_id: "a1", archived: true },
            },
          ],
          unread: {},
        });
      }
      if (urlStr.includes("/api/chat/unread")) return ok({ unread: {} });
      if (urlStr.includes("/api/agents?include_guests=1")) return ok([]);
      if (urlStr.includes("/api/agents/archived")) {
        return ok([
          { id: "a1", archived_slug: "old-bot-x", original: { name: "old-bot", display_name: "Old Bot" } },
        ]);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("shows 'Archived agent' subtitle for an archived DM", async () => {
    render(<MessagesApp windowId="test" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "open-archived-dm" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "open-archived-dm" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("banner")).toHaveTextContent("Old Bot"));
    await waitFor(() => expect(screen.getByRole("banner")).toHaveTextContent("Archived agent"));
  });
});

describe("MessagesApp mobile sendError clearing", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    let messagePosts = 0;
    originalFetch = global.fetch;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      const ok = (body: unknown) =>
        Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(body),
        } as unknown as Response);
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages")) {
        return ok({
          channels: [
            { id: "ch-g", name: "design", type: "group", members: ["user", "tom", "lin"] },
          ],
          unread: {},
        });
      }
      if (urlStr.includes("/api/chat/unread")) return ok({ unread: {} });
      if (urlStr.includes("/api/agents?include_guests=1")) return ok([]);
      if (urlStr.includes("/api/agents/archived")) return ok([]);
      if (urlStr.includes("/api/chat/upload"))
        return ok({ filename: "a.png", mime_type: "image/png", size: 1, url: "http://localhost/a.png" });
      if (urlStr.includes("/api/chat/messages")) {
        messagePosts += 1;
        // First attachment POST fails (an earlier error is on screen), the
        // retry succeeds -- the success path must clear sendError.
        if (messagePosts === 1) {
          return Promise.resolve({
            ok: false,
            status: 400,
            headers: { get: () => "application/json" },
            json: () => Promise.resolve({ error: "couldn't send message" }),
          } as unknown as Response);
        }
        return ok({});
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("clears sendError after a successful attachment send", async () => {
    render(<MessagesApp windowId="test" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "open-group" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "open-group" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());

    // Add a completed attachment so the attachment POST path is taken.
    fireEvent.click(screen.getByRole("button", { name: "mock-upload" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/chat/upload"), expect.anything()),
    );
    await waitFor(() => true); // flush the completed upload into the committed render

    // First attachment send fails -> an error is on screen.
    fireEvent.click(screen.getByRole("button", { name: "mock-send" }));
    await waitFor(() => expect(screen.getByText(/couldn't send message/)).toBeInTheDocument());

    // Retry: the attachment POST succeeds and the error is cleared.
    fireEvent.click(screen.getByRole("button", { name: "mock-send" }));
    await waitFor(() => expect(screen.queryByText(/couldn't send message/)).toBeNull());
  });
});

describe("MessagesApp mobile Back button sends leave frame", () => {
  let originalFetch: typeof global.fetch;
  let wsMock: { send: ReturnType<typeof vi.fn>; readyState: number; close: ReturnType<typeof vi.fn>; onopen: (() => void) | null; onmessage: ((e: MessageEvent) => void) | null; onclose: (() => void) | null };
  let WebSocketConstructor: typeof WebSocket;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    wsMock = {
      send: vi.fn(),
      readyState: 1, // WebSocket.OPEN
      close: vi.fn(),
      onopen: null,
      onmessage: null,
      onclose: null,
    };
    WebSocketConstructor = global.WebSocket;
    // Mock WebSocket constructor to return our mock - use a real constructor function
    function MockWebSocket(this: WebSocket, url: string) {
      (this as unknown as { _url: string })._url = url;
      return wsMock as unknown as WebSocket;
    }
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      const ok = (body: unknown) =>
        Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(body),
        } as unknown as Response);
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages")) {
        return ok({
          channels: [
            { id: "ch-g", name: "design", type: "group", members: ["user", "tom", "lin"] },
          ],
          unread: {},
        });
      }
      if (urlStr.includes("/api/chat/unread")) return ok({ unread: {} });
      if (urlStr.includes("/api/agents?include_guests=1")) return ok([]);
      if (urlStr.includes("/api/agents/archived")) return ok([]);
      if (urlStr.includes("/api/chat/channels/ch-g/messages")) {
        return ok({ messages: [] });
      }
      if (urlStr.includes("/api/chat/channels/ch-g/mark-read")) {
        return ok({});
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.WebSocket = WebSocketConstructor;
  });

  it("sends leave frame on Back tap", async () => {
    render(<MessagesApp windowId="test" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "open-group" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "open-group" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());

    // Click Back to chats - this should send a leave frame for ch-g
    fireEvent.click(screen.getByRole("button", { name: "Back to chats" }));

    await waitFor(() => {
      expect(wsMock.send).toHaveBeenCalledWith(JSON.stringify({ type: "leave", channel_id: "ch-g" }));
    });
  });

  it("sidebar select works after Back", async () => {
    render(<MessagesApp windowId="test" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "open-group" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "open-group" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());

    // Click Back to chats
    fireEvent.click(screen.getByRole("button", { name: "Back to chats" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Back to chats" })).not.toBeInTheDocument());

    // Sidebar should be visible again and selectable
    await waitFor(() => expect(screen.getByRole("button", { name: "open-group" })).toBeInTheDocument());
  });
});

describe("MessagesApp mobile Back-out guard resets on non-null selection paths", () => {
  let originalFetch: typeof global.fetch;
  let WebSocketConstructor: typeof WebSocket;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    WebSocketConstructor = global.WebSocket;
    const wsMock = {
      send: vi.fn(),
      readyState: 1,
      close: vi.fn(),
      onopen: null,
      onmessage: null,
      onclose: null,
    };
    function MockWebSocket(this: WebSocket, _url: string) {
      return wsMock as unknown as WebSocket;
    }
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.WebSocket = WebSocketConstructor;
  });

  it("reselects remembered/A2A channel after Back, open-messages select, then channel removed", async () => {
    vi.mocked(a2aSelection.findA2aChannelId).mockReturnValue("ch-a2a");
    let channels = [
      { id: "ch-a2a", name: "a2a", type: "group", members: ["user", "tom"], settings: { kind: "a2a" } },
      { id: "ch-del", name: "todelete", type: "group", members: ["user", "tom"] },
    ];
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      const ok = (body: unknown) =>
        Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(body),
        } as unknown as Response);
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages") && !urlStr.includes("archived=true") && !urlStr.includes("/mark-read")) {
        return ok({ channels, unread: {} });
      }
      if (urlStr.includes("archived=true")) return ok({ channels: [] });
      if (urlStr.includes("/api/chat/unread")) return ok({ unread: {} });
      if (urlStr.includes("/api/agents?include_guests=1")) return ok([]);
      if (urlStr.includes("/api/agents/archived")) return ok([]);
      if (urlStr.includes("/messages")) return ok({ messages: [] });
      if (urlStr.includes("/mark-read")) return ok({});
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });

    render(<MessagesApp windowId="test" scope={{ projectId: "proj1" }} />);
    await waitFor(() => expect(screen.getByTestId("channel-sidebar")).toBeInTheDocument());

    // Default selection should pick the A2A channel.
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());
    expect(screen.getByRole("banner")).toHaveTextContent("a2a");

    // Back out
    fireEvent.click(screen.getByRole("button", { name: "Back to chats" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Back to chats" })).not.toBeInTheDocument());

    // Select the channel-to-delete via the open-messages event (a non-null selection path)
    window.dispatchEvent(new CustomEvent("taos:open-messages", { detail: { channelId: "ch-del" } }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());
    expect(screen.getByRole("banner")).toHaveTextContent("todelete");

    // Remove that channel from the list and trigger a refresh
    channels = [
      { id: "ch-a2a", name: "a2a", type: "group", members: ["user", "tom"], settings: { kind: "a2a" } },
    ];
    window.dispatchEvent(new Event("focus"));

    // Default selection should re-pick the remembered/A2A channel again.
    await waitFor(() => expect(screen.getByRole("banner")).toHaveTextContent("a2a"), { timeout: 5000 });
  });
});

describe("MessagesApp mobile first-chat selection clears the Back-out guard", () => {
  let originalFetch: typeof global.fetch;
  let WebSocketConstructor: typeof WebSocket;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    originalFetch = global.fetch;
    WebSocketConstructor = global.WebSocket;
    const wsMock = {
      send: vi.fn(),
      readyState: 1,
      close: vi.fn(),
      onopen: null,
      onmessage: null,
      onclose: null,
    };
    function MockWebSocket(this: WebSocket, _url: string) {
      return wsMock as unknown as WebSocket;
    }
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.WebSocket = WebSocketConstructor;
  });

  it("clears the guard when the waiting first chat selects its DM, so a later refresh re-picks after the DM is deleted", async () => {
    vi.mocked(a2aSelection.findA2aChannelId).mockReturnValue("ch-a2a");
    let channels = [
      { id: "ch-a2a", name: "a2a", type: "group", members: ["user", "tom"], settings: { kind: "a2a" } },
    ];
    const agentDm = { id: "ch-dm", name: "deployed-agent", type: "dm", members: ["user", "deployed-agent"] };
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      const ok = (body: unknown) =>
        Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve(body),
        } as unknown as Response);
      if (urlStr.includes("/api/chat/channels") && !urlStr.includes("/messages") && !urlStr.includes("archived=true") && !urlStr.includes("/mark-read")) {
        return ok({ channels, unread: {} });
      }
      if (urlStr.includes("archived=true")) return ok({ channels: [] });
      if (urlStr.includes("/api/chat/unread")) return ok({ unread: {} });
      if (urlStr.includes("/api/agents?include_guests=1")) return ok([]);
      if (urlStr.includes("/api/agents/archived")) return ok([]);
      if (urlStr.includes("/messages")) return ok({ messages: [] });
      if (urlStr.includes("/mark-read")) return ok({});
      return Promise.resolve({
        ok: false,
        status: 404,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    });

    render(<MessagesApp windowId="test" scope={{ projectId: "proj1" }} firstChatAgent="deployed-agent" />);
    await waitFor(() => expect(screen.getByTestId("channel-sidebar")).toBeInTheDocument());

    // Open another channel, then Back: the guard is set.
    window.dispatchEvent(new CustomEvent("taos:open-messages", { detail: { channelId: "ch-a2a" } }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument());
    expect(screen.getByRole("banner")).toHaveTextContent("a2a");
    fireEvent.click(screen.getByRole("button", { name: "Back to chats" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Back to chats" })).not.toBeInTheDocument());

    // The deployed agent's DM arrives: the waiting first chat opens it and
    // must clear the Back-out guard.
    channels = [...channels, agentDm];
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Back to chats" })).toBeInTheDocument(), {
      timeout: 5000,
    });

    // The DM is deleted and the list refreshes: with the guard cleared by the
    // first-chat selection path, default-select re-picks the A2A channel
    // instead of keeping the deleted DM selected.
    channels = [
      { id: "ch-a2a", name: "a2a", type: "group", members: ["user", "tom"], settings: { kind: "a2a" } },
    ];
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(screen.getByRole("banner")).toHaveTextContent("a2a"), { timeout: 5000 });
  });
});
