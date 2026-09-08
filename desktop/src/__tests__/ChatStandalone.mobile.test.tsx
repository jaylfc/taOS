import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Mock the heavy MessagesApp lazy import so the test only exercises the
// ChatStandalone shell (header, tabs, route) rather than the full chat
// surface, which has its own suite.
vi.mock("../apps/MessagesApp", () => ({
  MessagesApp: ({ windowId }: { windowId: string }) => (
    <div data-testid="messages-app-stub" data-window-id={windowId} />
  ),
}));

vi.mock("../shell/InstallPromptBanner", () => ({
  InstallPromptBanner: () => null,
}));

vi.mock("../hooks/use-is-mobile", () => ({
  useIsMobile: vi.fn(),
}));

import { useIsMobile } from "../hooks/use-is-mobile";
import { ChatStandalone } from "../ChatStandalone";

describe("ChatStandalone mobile layout", () => {
  beforeEach(() => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReset();
  });

  it("renders the bottom tabs on mobile with Chats active by default", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(<ChatStandalone />);
    const chatsTab = screen.getByTestId("mobile-chat-tab-chats");
    expect(chatsTab).toHaveAttribute("aria-current", "page");
    for (const id of ["chats", "projects", "decisions", "agents"]) {
      expect(screen.getByTestId(`mobile-chat-tab-${id}`)).toBeInTheDocument();
    }
  });

  it("hides the bottom tabs on desktop viewports", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<ChatStandalone />);
    expect(screen.queryByTestId("mobile-chat-tabs")).not.toBeInTheDocument();
  });

  it("routes to the desktop shell with the correct app id when a sibling tab is tapped", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    // Stub window.location.href so the redirect can be asserted; jsdom treats
    // assignments as a no-op without a real navigation, and we want to verify
    // the deep-link URL is built correctly.
    const originalLocation = window.location;
    let capturedHref = "";
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, get href() { return capturedHref; }, set href(v: string) { capturedHref = v; } } as unknown as Location,
    });
    try {
      render(<ChatStandalone />);
      fireEvent.click(screen.getByTestId("mobile-chat-tab-projects"));
      expect(capturedHref).toBe("/desktop?app=projects");
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
    }
  });

  it("keeps the Chats tab active when the Chats tab is tapped (no deep-link redirect)", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(<ChatStandalone />);
    fireEvent.click(screen.getByTestId("mobile-chat-tab-chats"));
    expect(screen.getByTestId("mobile-chat-tab-chats")).toHaveAttribute("aria-current", "page");
  });
});
