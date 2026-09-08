// desktop/src/apps/MessagesApp/MobileMessages.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { MobileMessagesToolbar } from "./MobileMessages";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("MobileMessagesToolbar", () => {
  it("renders the desktop 'Messages' wordmark and a New channel button on desktop", () => {
    render(
      <MobileMessagesToolbar
        isMobile={false}
        hasSelectedChannel={false}
        onNewChannel={vi.fn()}
      />,
    );
    expect(screen.getByText("Messages")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New channel" })).toBeInTheDocument();
  });

  it("hides the 'Messages' wordmark on mobile but still renders the toolbar strip", () => {
    render(
      <MobileMessagesToolbar
        isMobile={true}
        hasSelectedChannel={false}
        onNewChannel={vi.fn()}
      />,
    );
    expect(screen.queryByText("Messages")).toBeNull();
    // The new-channel button is the only action on the strip and still has to
    // be tappable when the user is sitting on the chat list pane.
    expect(screen.getByRole("button", { name: "New channel" })).toBeInTheDocument();
  });

  it("hides the entire toolbar on mobile once a channel is selected", () => {
    const { container } = render(
      <MobileMessagesToolbar
        isMobile={true}
        hasSelectedChannel={true}
        onNewChannel={vi.fn()}
      />,
    );
    // The toolbar's wrapping div is gated by showToolbar; on mobile with a
    // channel selected nothing renders at all.
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole("button", { name: "New channel" })).toBeNull();
  });

  it("keeps the toolbar visible on desktop even when a channel is selected", () => {
    render(
      <MobileMessagesToolbar
        isMobile={false}
        hasSelectedChannel={true}
        onNewChannel={vi.fn()}
      />,
    );
    expect(screen.getByText("Messages")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New channel" })).toBeInTheDocument();
  });

  it("forwards clicks on the new-channel button to onNewChannel", () => {
    const onNewChannel = vi.fn();
    render(
      <MobileMessagesToolbar
        isMobile={false}
        hasSelectedChannel={false}
        onNewChannel={onNewChannel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New channel" }));
    expect(onNewChannel).toHaveBeenCalledTimes(1);
  });

  it("uses the window title as the centered wordmark when one is supplied", () => {
    render(
      <MobileMessagesToolbar
        isMobile={false}
        hasSelectedChannel={false}
        title="taOS talk"
        onNewChannel={vi.fn()}
      />,
    );
    // Title overrides the generic "Messages" label.
    expect(screen.getByText("taOS talk")).toBeInTheDocument();
    expect(screen.queryByText("Messages")).toBeNull();
    expect(screen.getByRole("button", { name: "New channel" })).toBeInTheDocument();
  });

  it("honors the title on mobile too (standalone chat PWA header)", () => {
    render(
      <MobileMessagesToolbar
        isMobile={true}
        hasSelectedChannel={false}
        title="taOS talk"
        onNewChannel={vi.fn()}
      />,
    );
    expect(screen.getByText("taOS talk")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New channel" })).toBeInTheDocument();
  });
});