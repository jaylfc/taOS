import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
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

describe("MessagesApp receipt EventSource guard", () => {
  const originalEventSource = globalThis.EventSource;

  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    FakeEventSource.instances = [];
  });

  afterEach(() => {
    cleanup();
    Object.defineProperty(globalThis, "EventSource", {
      value: originalEventSource,
      writable: true,
      configurable: true,
    });
  });

  it("mounts without throwing when EventSource is undefined and still renders", () => {
    Object.defineProperty(globalThis, "EventSource", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    expect(() => {
      render(<MessagesApp windowId="test" />);
    }).not.toThrow();

    expect(screen.getByTestId("channel-sidebar")).toBeInTheDocument();
  });

  it("continues delivering receipt messages after a transient error", async () => {
    Object.defineProperty(globalThis, "EventSource", {
      value: FakeEventSource,
      writable: true,
      configurable: true,
    });

    render(<MessagesApp windowId="test" />);

    const instance = FakeEventSource.instances[0];
    expect(instance).toBeDefined();

    // Simulate an error event from the stream
    instance.simulateError();

    // Simulate a receipt message event arriving after the error
    let receiptHandled = false;
    const originalOnMessage = instance.onmessage;
    instance.onmessage = ((ev: MessageEvent) => {
      receiptHandled = true;
      if (originalOnMessage) originalOnMessage(ev);
    });

    instance.simulateMessage(
      JSON.stringify({
        type: "receipt",
        message_id: "m1",
        agent_id: "peer",
        delivered_at: 1000,
        seen_at: null,
      }),
    );

    expect(receiptHandled).toBe(true);
  });
});
