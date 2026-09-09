import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import React from "react";

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  private listeners: { [type: string]: ((event: Event) => void)[] } = {};

  constructor(url: string, protocols?: unknown, options?: unknown) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(code?: number, reason?: string) {
    if (this.readyState === MockWebSocket.CLOSED || this.readyState === MockWebSocket.CLOSING) {
      return;
    }
    this.readyState = MockWebSocket.CLOSING;
    const event = { code: code ?? 1000, reason: reason ?? "" } as CloseEvent;
    this._emit("close", event);
    if (this.onclose) this.onclose();
    this.readyState = MockWebSocket.CLOSED;
  }

  send() {}

  addEventListener(type: string, listener: (event: Event) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: (event: Event) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  private _emit(type: string, event: Event) {
    (this.listeners[type] || []).forEach((l) => l(event));
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this._emit("open", {} as Event);
    if (this.onopen) this.onopen();
  }

  simulateClose() {
    this.close();
  }
}

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
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

vi.mock("@/components/mobile/MobileSplitView", () => ({
  MobileSplitView: () => <div data-testid="mobile-split-view" />,
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

global.fetch = vi.fn(() =>
  Promise.resolve({
    ok: true,
    json: () =>
      Promise.resolve({
        channels: [],
        unread: {},
        agents: [],
        archived: [],
      }),
    headers: {
      get: (_name: string) => "application/json",
    },
  } as unknown as Response)
) as typeof fetch;

import { MessagesApp } from "../MessagesApp";

describe("MessagesApp WebSocket", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("unmounting cancels the pending reconnect", async () => {
    const { unmount } = render(<MessagesApp windowId="test" />);
    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    // Simulate the first WebSocket opening so partysocket tracks uptime
    MockWebSocket.instances[0].simulateOpen();

    expect(MockWebSocket.instances).toHaveLength(1);

    unmount();

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("reconnect delay is jittered", async () => {
    const randoms = [0.1, 0.2, 0.3, 0.4, 0.5];
    let randomIndex = 0;
    const originalRandom = Math.random;
    Math.random = () => randoms[randomIndex++ % randoms.length];

    const { unmount: unmount1 } = render(<MessagesApp windowId="test1" />);
    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    MockWebSocket.instances[0].simulateOpen();

    const { unmount: unmount2 } = render(<MessagesApp windowId="test2" />);
    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    MockWebSocket.instances[1].simulateOpen();

    expect(MockWebSocket.instances).toHaveLength(2);

    unmount1();
    unmount2();

    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    // With jitter, delays should differ: 1400, 1800, 2200, 2600, 3000...
    // Advance by 1500ms: only the first (1400) should reconnect
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });

    expect(MockWebSocket.instances.length).toBeGreaterThanOrEqual(2);
    expect(MockWebSocket.instances.length).toBeLessThan(4);

    Math.random = originalRandom;
  });

  it("a remount after a zombie opens a fresh socket", async () => {
    const { unmount } = render(<MessagesApp windowId="test" />);
    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    expect(MockWebSocket.instances).toHaveLength(1);
    const firstInstance = MockWebSocket.instances[0];

    unmount();

    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    render(<MessagesApp windowId="test" />);
    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1]).not.toBe(firstInstance);
  });
});
