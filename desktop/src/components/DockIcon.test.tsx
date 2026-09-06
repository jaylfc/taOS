import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      windows: [],
      focusWindow: vi.fn(),
      restoreWindow: vi.fn(),
      minimizeWindow: vi.fn(),
      maximizeWindow: vi.fn(),
      recenterWindow: vi.fn(),
      closeWindow: vi.fn(),
    }),
}));

vi.mock("@/stores/dock-store", () => ({
  useDockStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      pinned: [],
      pin: vi.fn(),
      unpin: vi.fn(),
    }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) => ({
    id,
    name: id === "messages" ? "Messages" : "Test App",
    icon: "message-circle",
    category: "platform",
    defaultSize: { w: 900, h: 600 },
    minSize: { w: 400, h: 300 },
    singleton: true,
    pinned: true,
    launchpadOrder: 1,
  }),
  prefetchApp: vi.fn(),
  // No redirects in this fixture: a pin id is its own app id and carries no
  // section. Mirrors the real contract rather than stubbing it away (#2677).
  APP_REDIRECTS: {},
  resolvePinnedId: (id: string) => ({ id }),
  pinnedAppId: (id: string) => id,
  pinnedLaunchProps: () => undefined,
}));

import { DockIcon } from "./DockIcon";

describe("DockIcon", () => {
  it("renders the app name as accessible label and title", () => {
    render(<DockIcon appId="messages" isRunning={false} onClick={vi.fn()} />);
    const button = screen.getByRole("button", { name: /open messages/i });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute("title", "Messages");
  });

  it("shows the running indicator when isRunning is true", () => {
    render(<DockIcon appId="messages" isRunning={true} onClick={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /open messages/i }).querySelector(".bg-accent")
    ).toBeInTheDocument();
  });

  it("does not show the running indicator when isRunning is false", () => {
    render(<DockIcon appId="messages" isRunning={false} onClick={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /open messages/i }).querySelector(".bg-accent")
    ).toBeNull();
  });

  it("calls onClick when the dock icon is clicked", () => {
    const onClick = vi.fn();
    render(<DockIcon appId="messages" isRunning={false} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: /open messages/i }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
