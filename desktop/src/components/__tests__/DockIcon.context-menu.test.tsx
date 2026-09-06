import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const openWindow = vi.fn(() => "win-new");

// The pin-resolving half of this mock mirrors the real registry rather than
// stubbing identity: a mock that stopped matching the module it stands in for
// is how a dock restore that dropped every pin still looked green (#2677).
// It all lives inside the factory because vi.mock is hoisted above the
// module's own top-level bindings.
vi.mock("@/registry/app-registry", () => {
  const mockApps: Record<string, { id: string; name: string; icon: string; singleton: boolean; defaultSize: { w: number; h: number } }> = {
    browser: { id: "browser", name: "Browser", icon: "globe", singleton: false, defaultSize: { w: 1024, h: 700 } },
    messages: { id: "messages", name: "Messages", icon: "message-circle", singleton: true, defaultSize: { w: 900, h: 600 } },
    projects: { id: "projects", name: "Projects", icon: "folder-kanban", singleton: false, defaultSize: { w: 1100, h: 720 } },
    notifications: { id: "notifications", name: "Notifications", icon: "bell", singleton: true, defaultSize: { w: 900, h: 600 } },
  };
  const mockRedirects: Record<string, { appId: string; section?: string }> = {
    "notification-archive": { appId: "notifications", section: "archive" },
  };
  const resolve = (id: string) => {
    const redirect = mockRedirects[id];
    const targetId = redirect?.appId ?? id;
    if (!mockApps[targetId]) return undefined;
    return { id: targetId, ...(redirect?.section ? { section: redirect.section } : {}) };
  };

  return {
    prefetchApp: vi.fn(),
    getApp: (id: string) => mockApps[id] ?? null,
    APP_REDIRECTS: mockRedirects,
    resolvePinnedId: resolve,
    pinnedAppId: (id: string) => resolve(id)?.id ?? id,
    pinnedLaunchProps: (id: string) => {
      const section = resolve(id)?.section;
      return section ? { section } : undefined;
    },
  };
});

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (selector: (s: { windows: Array<{ id: string; appId: string; minimized: boolean; maximized: boolean }>; openWindow: typeof openWindow; focusWindow: () => void; restoreWindow: () => void; minimizeWindow: () => void; maximizeWindow: () => void; recenterWindow: () => void; closeWindow: () => void }) => unknown) =>
    selector({
      windows: [
        { id: "win-1", appId: "browser", minimized: false, maximized: false },
        { id: "win-2", appId: "messages", minimized: false, maximized: false },
        { id: "win-3", appId: "projects", minimized: false, maximized: false },
      ],
      openWindow,
      focusWindow: vi.fn(),
      restoreWindow: vi.fn(),
      minimizeWindow: vi.fn(),
      maximizeWindow: vi.fn(),
      recenterWindow: vi.fn(),
      closeWindow: vi.fn(),
    }),
}));

vi.mock("@/stores/dock-store", () => ({
  useDockStore: (selector: (s: { pinned: string[]; pin: () => void; unpin: () => void }) => unknown) =>
    selector({ pinned: [], pin: vi.fn(), unpin: vi.fn() }),
}));

import { DockIcon } from "../DockIcon";

describe("DockIcon context menu New Window", () => {
  beforeEach(() => openWindow.mockClear());

  it("shows New Window for a singleton:false app (browser)", () => {
    render(<DockIcon appId="browser" isRunning={true} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Browser" });
    fireEvent.contextMenu(btn);
    expect(screen.getByText("New Window")).toBeInTheDocument();
  });

  it("does NOT show New Window for a singleton:true app (messages)", () => {
    render(<DockIcon appId="messages" isRunning={true} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Messages" });
    fireEvent.contextMenu(btn);
    expect(screen.queryByText("New Window")).not.toBeInTheDocument();
  });

  it("shows New Window for projects (singleton:false)", () => {
    render(<DockIcon appId="projects" isRunning={true} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Projects" });
    fireEvent.contextMenu(btn);
    expect(screen.getByText("New Window")).toBeInTheDocument();
  });

  it("New Window calls openWindow with forceNew for a running multi-window app", () => {
    render(<DockIcon appId="browser" isRunning={true} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Browser" });
    fireEvent.contextMenu(btn);
    fireEvent.click(screen.getByText("New Window"));
    expect(openWindow).toHaveBeenCalledWith("browser", { w: 1024, h: 700 }, undefined, { forceNew: true });
  });

  it("does not show New Window for a not-running singleton:true app", () => {
    render(<DockIcon appId="messages" isRunning={false} onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: "Open Messages" });
    fireEvent.contextMenu(btn);
    expect(screen.queryByText("New Window")).not.toBeInTheDocument();
  });
});

describe("DockIcon legacy pin ids (#2677)", () => {
  beforeEach(() => openWindow.mockClear());

  it("renders the app a legacy pin launches, not a blank slot", () => {
    render(<DockIcon appId="notification-archive" isRunning={false} onClick={() => {}} />);
    expect(screen.getByRole("button", { name: "Open Notifications" })).toBeInTheDocument();
  });
});
