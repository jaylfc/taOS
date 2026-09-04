import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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

import { useIsMobile } from "@/hooks/use-is-mobile";
import { MessagesApp } from "../MessagesApp";

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
