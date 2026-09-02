import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

let mockPinned = ["messages", "agents", "files", "store", "settings"];
let mockWindows: Array<{
  id: string;
  appId: string;
  minimized: boolean;
  focused: boolean;
  zIndex: number;
  position: { x: number; y: number };
  size: { w: number; h: number };
  maximized: boolean;
  snapped: null;
  launchNonce: number;
}> = [];
let mockStructure: Record<
  string,
  { variant?: string } & Record<string, unknown>
> = {};

const mockOpenWindow = vi.fn();
const mockFocusWindow = vi.fn();
const mockRestoreWindow = vi.fn();

vi.mock("@/stores/dock-store", () => ({
  useDockStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      pinned: mockPinned,
      pin: vi.fn(),
      unpin: vi.fn(),
    }),
}));

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (
    selOrUndefined?: ((s: Record<string, unknown>) => unknown) | Record<string, unknown>
  ) => {
    const state = {
      windows: mockWindows,
      openWindow: mockOpenWindow,
      focusWindow: mockFocusWindow,
      restoreWindow: mockRestoreWindow,
      minimizeWindow: vi.fn(),
      maximizeWindow: vi.fn(),
      recenterWindow: vi.fn(),
      closeWindow: vi.fn(),
    };
    if (typeof selOrUndefined === "function") {
      return selOrUndefined(state);
    }
    return state;
  },
}));

vi.mock("@/stores/theme-store", () => ({
  useThemeStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      structure: mockStructure,
    }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) => ({
    id,
    name:
      id === "messages"
        ? "Messages"
        : id === "agents"
          ? "Agents"
          : id === "files"
            ? "Files"
            : id === "store"
              ? "Store"
              : id === "settings"
                ? "Settings"
                : id === "browser"
                  ? "Browser"
                  : "Test App",
    icon: "message-circle",
    category: "platform",
    defaultSize: { w: 900, h: 600 },
    minSize: { w: 400, h: 300 },
    singleton: true,
    pinned: true,
    launchpadOrder: 1,
  }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

import { Dock } from "./Dock";

beforeEach(() => {
  mockPinned = ["messages", "agents", "files", "store", "settings"];
  mockWindows = [];
  mockStructure = {};
  vi.clearAllMocks();
});

describe("Dock", () => {
  it("renders pinned apps and the Launchpad button", () => {
    const onLaunchpadOpen = vi.fn();
    render(<Dock onLaunchpadOpen={onLaunchpadOpen} />);

    expect(
      screen.getByRole("button", { name: /launchpad/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open messages/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open agents/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open files/i })
    ).toBeInTheDocument();
  });

  it("renders with empty pinned list shows only Launchpad", () => {
    mockPinned = [];
    const onLaunchpadOpen = vi.fn();
    render(<Dock onLaunchpadOpen={onLaunchpadOpen} />);

    expect(
      screen.getByRole("button", { name: /launchpad/i })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /open messages/i })
    ).toBeNull();
  });

  it("calls onLaunchpadOpen when Launchpad button is clicked", () => {
    const onLaunchpadOpen = vi.fn();
    render(<Dock onLaunchpadOpen={onLaunchpadOpen} />);

    fireEvent.click(screen.getByRole("button", { name: /launchpad/i }));
    expect(onLaunchpadOpen).toHaveBeenCalledOnce();
  });

  it("renders running apps not in pinned after a separator", () => {
    mockWindows = [
      {
        id: "win-1",
        appId: "browser",
        minimized: false,
        focused: true,
        zIndex: 1,
        position: { x: 0, y: 0 },
        size: { w: 1024, h: 700 },
        maximized: false,
        snapped: null,
        launchNonce: 0,
      },
    ];
    const onLaunchpadOpen = vi.fn();
    render(<Dock onLaunchpadOpen={onLaunchpadOpen} />);

    expect(
      screen.getByRole("button", { name: /open browser/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open agents/i })
    ).toBeInTheDocument();
  });
});
