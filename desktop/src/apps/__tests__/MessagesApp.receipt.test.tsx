import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import React from "react";

vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: vi.fn() }));
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
  useProcessStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ openWindow: vi.fn() }),
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
vi.mock("../chat/ChannelSidebar", () => ({
  ChannelSidebar: () => <div data-testid="channel-sidebar" />,
}));
// vi.mock("../chat/MessageList", () => ({
//   MessageList: () => <div data-testid="message-list" />,
// }));
vi.mock("../chat/MessageInput", () => ({
  MessageInput: () => <div data-testid="message-input" />,
}));
vi.mock("../chat/MessageAvatar", () => ({
  MessageAvatar: ({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div {...props}>{children}</div>,
}));
vi.mock("../chat/MessageHoverActions", () => ({
  MessageHoverActions: () => <div data-testid="message-hover-actions" />,
}));
vi.mock("../chat/MessageEditor", () => ({
  MessageEditor: () => <div data-testid="message-editor" />,
}));
vi.mock("../chat/MessageTombstone", () => ({
  MessageTombstone: () => <div data-testid="message-tombstone" />,
}));
vi.mock("../chat/ThreadIndicator", () => ({
  ThreadIndicator: () => <div data-testid="thread-indicator" />,
}));
vi.mock("../chat/AttachmentGallery", () => ({
  AttachmentGallery: () => <div data-testid="attachment-gallery" />,
}));
vi.mock("../chat/PinBadge", () => ({
  PinBadge: () => <div data-testid="pin-badge" />,
}));
vi.mock("../chat/PinnedMessagesPopover", () => ({
  PinnedMessagesPopover: () => <div data-testid="pinned-messages-popover" />,
}));
vi.mock("../chat/TypingFooter", () => ({
  TypingFooter: () => <div data-testid="typing-footer" />,
}));
vi.mock("../chat/PinRequestAffordance", () => ({
  PinRequestAffordance: () => <div data-testid="pin-request-affordance" />,
}));
vi.mock("../chat/ReactionBar", () => ({
  ReactionBar: () => <div data-testid="reaction-bar" />,
}));
vi.mock("../chat/A2aBusPanel", () => ({
  A2aBusMessageView: () => <div data-testid="a2a-bus-view" />,
  useBusChannels: () => ({ channels: [], loading: false }),
}));
vi.mock("@/components/mobile/MobileSplitView", () => ({
  MobileSplitView: ({ listTitle, list, detail }: {
    listTitle?: string;
    list?: React.ReactNode;
    detail?: React.ReactNode;
  }) => (
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
  readLastChannel: vi.fn(),
  writeLastChannel: vi.fn(),
  findA2aChannelId: vi.fn(() => "ch1"),
}));

import { useIsMobile } from "@/hooks/use-is-mobile";
import { MessagesApp } from "../MessagesApp";

/* ------------------------------------------------------------------ */
/*  Fake EventSource that mirrors real browser close() semantics       */
/* ------------------------------------------------------------------ */

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private _closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this._closed = true;
    this.readyState = 2;
  }

  simulateMessage(data: string) {
    if (this._closed) return;
    if (this.onmessage) {
      this.onmessage({ data } as MessageEvent);
    }
  }

  simulateError() {
    if (this.onerror) {
      this.onerror();
    }
  }
}

/* ------------------------------------------------------------------ */
/*  MockWebSocket mirroring MessagesApp.ws.test.tsx                   */
/* ------------------------------------------------------------------ */

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  readyState: number;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    MockWebSocket.instances.push(this);
  }

  send(_data: unknown) {}
  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function reset() {
  MockWebSocket.instances = [];
  FakeEventSource.instances = [];
  vi.clearAllTimers();
  vi.clearAllMocks();
  (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
}

async function openFirstSocket() {
  const ws = MockWebSocket.instances[0];
  ws.readyState = MockWebSocket.OPEN;
  ws.onopen?.();
  await act(async () => {});
}

describe("MessagesApp receipts over the chat websocket", () => {
  const originalFetch = globalThis.fetch;
  const originalEventSource = globalThis.EventSource;
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    reset();
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("opens no EventSource", async () => {
    vi.useFakeTimers();
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ channels: [], unread: {} }),
    }) as typeof fetch;

    render(<MessagesApp windowId="test" />);

    await act(async () => {});

    expect(FakeEventSource.instances).toHaveLength(0);
    vi.useRealTimers();
  });

  it("a websocket receipt event marks the message Seen", async () => {
    let fetchCallCount = 0;
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const u = typeof url === "string" ? url : url.toString();
      fetchCallCount++;

      if (u.includes("/auth/me")) {
        return {
          ok: true,
          json: async () => ({ user: { id: "me", username: "me" } }),
        } as Response;
      }

      if (u.includes("/api/chat/channels") && !u.includes("/messages")) {
        return {
          ok: true,
          json: async () => ({
            channels: [
              {
                id: "ch1",
                name: "Test Channel",
                type: "topic",
                members: ["me", "peer"],
              },
            ],
            unread: {},
          }),
        } as Response;
      }

      if (u.includes("/api/agents") && !u.includes("/archived")) {
        return {
          ok: true,
          headers: new Headers({ "content-type": "application/json" }),
          json: async () => [],
        } as Response;
      }

      if (u.includes("/api/agents/archived")) {
        return {
          ok: true,
          headers: new Headers({ "content-type": "application/json" }),
          json: async () => [],
        } as Response;
      }

      if (u.includes("/api/chat/channels/ch1/messages")) {
        return {
          ok: true,
          json: async () => ({
            messages: [
              {
                id: "msg1",
                channel_id: "ch1",
                author_id: "me",
                author_type: "user",
                content: "hello",
                created_at: Date.now() / 1000,
                state: "complete",
              },
            ],
          }),
        } as Response;
      }

      if (u.includes("/api/chat/messages/msg1/receipts")) {
        return {
          ok: true,
          json: async () => ({ receipts: [] }),
        } as Response;
      }

      if (u.includes("/api/chat/receipts")) {
        return {
          ok: true,
          json: async () => ({}),
        } as Response;
      }

      if (u.includes("/api/frameworks/slash-commands")) {
        return {
          ok: true,
          json: async () => ({}),
        } as Response;
      }

      if (u.includes("/api/chat/unread")) {
        return {
          ok: true,
          json: async () => ({ unread: {} }),
        } as Response;
      }

      if (u.includes("/api/chat/pins")) {
        return {
          ok: true,
          json: async () => [],
        } as Response;
      }

      return {
        ok: false,
        status: 404,
        json: async () => ({ error: "not found" }),
      } as Response;
    });
    globalThis.fetch = fetchMock;

    render(<MessagesApp windowId="test" scope={{ projectId: "proj1" }} />);

    await act(async () => {});
    await openFirstSocket();

    await act(async () => {});
    await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument(), { timeout: 10000 });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/chat/messages/msg1/receipts"),
      ),
    );
    await act(async () => {});

    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.onmessage?.({ data: JSON.stringify({
        type: "receipt",
        message_id: "msg1",
        agent_id: "peer",
        reader_type: "agent",
        delivered_at: 1000,
        seen_at: 2000,
      }) });
    });
    await act(async () => {});

    await screen.findByLabelText("Seen", { timeout: 10000 });
  }, 20000);
});