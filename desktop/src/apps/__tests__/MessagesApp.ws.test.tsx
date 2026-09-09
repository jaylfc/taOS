import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import React from "react";

// ─── WebSocket mock ──────────────────────────────────────────────────────────

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

// ─── Lightweight stubs for heavy deps ────────────────────────────────────────

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
}));
vi.mock("../chat/ChannelSidebar", () => ({
  ChannelSidebar: () => <div data-testid="channel-sidebar" />,
}));
vi.mock("../chat/MessageList", () => ({
  MessageList: () => <div data-testid="message-list" />,
}));
vi.mock("../chat/MessageInput", () => ({
  MessageInput: () => <div data-testid="message-input" />,
}));
vi.mock("../chat/A2aBusPanel", () => ({
  A2aBusMessageView: () => <div data-testid="a2a-bus-view" />,
  useBusChannels: () => ({ channels: [], loading: false }),
}));

import { useIsMobile } from "@/hooks/use-is-mobile";
import { MessagesApp } from "../MessagesApp";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function reset() {
  MockWebSocket.instances = [];
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

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("MessagesApp WebSocket reconnect (tsk-qg7evy)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    reset();
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    cleanup();
  });

  it("unmounting cancels the pending reconnect", async () => {
    render(<MessagesApp windowId="test" />);
    await openFirstSocket();

    // Simulate backend restart: socket closes and arms reconnect timer.
    MockWebSocket.instances[0].close();
    await act(async () => {});

    // Unmount before the timer fires.
    cleanup();

    // Fast-forward past the default 1s reconnect window.
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("reconnect delay is jittered across instances", async () => {
    const delays: number[] = [];

    const originalSetTimeout = globalThis.setTimeout;
    vi.spyOn(globalThis, "setTimeout").mockImplementation((fn: TimerHandler, ms: number) => {
      delays.push(ms);
      return originalSetTimeout(fn, ms);
    });

    render(<MessagesApp windowId="test" />);
    await openFirstSocket();
    MockWebSocket.instances[0].close();
    await act(async () => {});

    render(<MessagesApp windowId="test-2" />);
    await openFirstSocket();
    MockWebSocket.instances[1].close();
    await act(async () => {});

    // Both instances should not reconnect with the exact same delay.
    const unique = new Set(delays.filter((d) => d > 0));
    expect(unique.size).toBeGreaterThan(1);
  });

  it("a remount after a zombie opens a fresh socket", async () => {
    render(<MessagesApp windowId="test" />);
    await openFirstSocket();

    // Backend restart: onclose arms the reconnect timer.
    MockWebSocket.instances[0].close();
    await act(async () => {});

    // Unmount without cancelling the timer (current bug).
    cleanup();

    // Let the timer fire: a zombie socket opens for the unmounted component.
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });

    // Remount: after the fix, no zombie should have been created, so a fresh
    // socket is the only additional one (total = 2: initial + remount).
    render(<MessagesApp windowId="test" />);
    await act(async () => {});

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toContain("/ws/chat");
  });
});
