import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MoveTabMenu } from "./MoveTabMenu";
import { useBrowserStore } from "@/stores/browser-store";

// Mock process-store: openWindow isn't exercised in unit tests for MoveTabMenu
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (selector: (s: { openWindow: () => string }) => unknown) =>
    selector({ openWindow: () => "new-win-id" }),
}));

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
});

describe("MoveTabMenu", () => {
  it("renders a menu with destination windows + New window option", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");
    useBrowserStore.getState().createWindow("win-b", "personal");
    const tabId = useBrowserStore.getState().addTab("win-a", "https://x.test/");

    render(
      <MoveTabMenu
        fromWindowId="win-a"
        tabId={tabId}
        anchorRect={{ x: 0, y: 0 }}
        onClose={() => {}}
      />,
    );

    // Should NOT list the source window; should list win-b + New window
    const items = screen.getAllByRole("menuitem");
    expect(items.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/new window/i)).toBeTruthy();
  });

  it("clicking a destination calls moveTab with the right args + closes", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");
    useBrowserStore.getState().createWindow("win-b", "personal");
    const tabId = useBrowserStore.getState().addTab("win-a", "https://x.test/");

    const moveSpy = vi.spyOn(useBrowserStore.getState(), "moveTab");
    const onClose = vi.fn();

    render(
      <MoveTabMenu
        fromWindowId="win-a"
        tabId={tabId}
        anchorRect={{ x: 0, y: 0 }}
        onClose={onClose}
      />,
    );

    // Find the win-b destination by text content (shows profileId)
    const items = screen.getAllByRole("menuitem");
    const winBItem = items.find((i) => i.textContent?.includes("personal"));
    fireEvent.click(winBItem!);

    expect(moveSpy).toHaveBeenCalledWith("win-a", tabId, "win-b");
    expect(onClose).toHaveBeenCalled();
  });

  it("renders 'No other windows' when source is the only window", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");

    render(
      <MoveTabMenu
        fromWindowId="win-a"
        tabId={useBrowserStore.getState().getWindow("win-a")!.tabs[0].id}
        anchorRect={{ x: 0, y: 0 }}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText(/no other windows/i)).toBeTruthy();
  });
});
