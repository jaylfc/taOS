import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
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
  ChannelSidebar: ({ emptyNote }: { emptyNote?: { title: string; detail: string; tone: string } }) => (
    <div data-testid="channel-sidebar" data-tone={emptyNote?.tone ?? ""}>{emptyNote ? `${emptyNote.title}|${emptyNote.detail}` : ""}</div>
  ),
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

const PROMPTS = ["Explain what a piece of code does.", "Help me plan my week."];
const DM = { id: "dm1", name: "Assistant", type: "dm", members: ["user", "assistant-2"] };

/** Channels and the DM's messages as given; everything else answers empty. */
function serve(channels: unknown[], messages: unknown[] = [], deployStatus: unknown = { status: "deploying" }) {
  globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
    const u = String(url);
    const body = u.includes("/api/chat/channels/dm1/messages")
      ? { messages }
      : u.startsWith("/api/chat/channels")
        ? { channels }
        : u.endsWith("/deploy-status")
          ? deployStatus
        : u.startsWith("/api/agents")
          ? []
          : {};
    return { ok: true, headers: new Headers({ "content-type": "application/json" }), json: async () => body } as Response;
  }) as typeof fetch;
}

describe("MessagesApp first chat from setup", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("says the assistant is being set up while its DM does not exist yet", async () => {
    serve([{ id: "ch9", name: "general", type: "topic", members: ["user", "peer"] }]);
    render(<MessagesApp windowId="w" firstChatAgent="assistant-2" suggestedPrompts={PROMPTS} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Setting up your assistant.");
  });

  it("hands the channel list a setup note while the DM does not exist yet", async () => {
    serve([]);
    render(<MessagesApp windowId="w" firstChatAgent="assistant-2" suggestedPrompts={PROMPTS} />);
    const sidebar = await screen.findByTestId("channel-sidebar");
    await waitFor(() => expect(sidebar).toHaveAttribute("data-tone", "busy"));
    expect(sidebar).toHaveTextContent("Setting up your assistant|Your first chat opens here when it's ready.");
  });

  it("hands the channel list the deploy's error once it failed", async () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    serve([], [], { status: "failed", error: "No room for another agent" });
    render(<MessagesApp windowId="w" firstChatAgent="assistant-2" suggestedPrompts={PROMPTS} />);
    const sidebar = await screen.findByTestId("channel-sidebar");
    await waitFor(() => expect(sidebar).toHaveAttribute("data-tone", "error"), { timeout: 6000 });
    expect(sidebar).toHaveTextContent("No room for another agent. You can check on it, or try again, in the Agents app.");
  }, 10000);

  it("an ordinary open hands the channel list no note", async () => {
    serve([]);
    render(<MessagesApp windowId="w" />);
    const sidebar = await screen.findByTestId("channel-sidebar");
    await screen.findByText("Pick a channel or start a DM");
    expect(sidebar).toHaveAttribute("data-tone", "");
  });

  it("opens the assistant's DM and offers the prompts while it is empty", async () => {
    serve([DM]);
    render(<MessagesApp windowId="w" firstChatAgent="assistant-2" suggestedPrompts={PROMPTS} />);
    const group = await screen.findByRole("group", { name: "Suggested prompts" });
    expect(screen.getByRole("button", { name: PROMPTS[0] })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: PROMPTS[1] })).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/chat/channels/dm1/messages?limit=50");
    // A picked prompt fills the input, and the chips step aside for it.
    fireEvent.click(screen.getByRole("button", { name: PROMPTS[0] }));
    await waitFor(() => expect(group).not.toBeInTheDocument());
  });

  it("no prompts once the DM has a message", async () => {
    serve([DM], [{ id: "m1", channel_id: "dm1", author_id: "assistant-2", author_type: "agent", content: "Hi", content_type: "text", created_at: 1 }]);
    render(<MessagesApp windowId="w" firstChatAgent="assistant-2" suggestedPrompts={PROMPTS} />);
    await screen.findByText("Hi");
    expect(screen.queryByRole("group", { name: "Suggested prompts" })).not.toBeInTheDocument();
  });

  it("an ordinary open shows no setup text and no prompts", async () => {
    serve([DM]);
    render(<MessagesApp windowId="w" />);
    expect(await screen.findByText("Pick a channel or start a DM")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Suggested prompts" })).not.toBeInTheDocument();
  });
});
