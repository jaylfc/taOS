import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { TabStrip } from "./TabStrip";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
});

describe("TabStrip — basic rendering", () => {
  it("renders one tab when window has only the default new-tab", () => {
    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBe(1);
  });

  it("renders one button per additional tab", () => {
    useBrowserStore.getState().addTab(TEST_WINDOW_ID, "https://a.test/");
    useBrowserStore.getState().addTab(TEST_WINDOW_ID, "https://b.test/");
    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    expect(screen.getAllByRole("tab").length).toBe(3);
  });

  it("marks the active tab with aria-selected", () => {
    const tabId = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://a.test/",
    );
    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    const active = tabs.find(
      (t) => t.getAttribute("aria-selected") === "true",
    );
    expect(active).toBeTruthy();
    expect(active?.getAttribute("data-tab-id")).toBe(tabId);
  });
});

describe("TabStrip — interactions", () => {
  it("clicking a tab calls setActiveTab", () => {
    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://a.test/",
    );
    const setActiveSpy = vi.spyOn(useBrowserStore.getState(), "setActiveTab");

    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    const targetTab = tabs.find((t) => t.getAttribute("data-tab-id") === tabA);
    fireEvent.click(targetTab!);

    expect(setActiveSpy).toHaveBeenCalledWith(TEST_WINDOW_ID, tabA);
  });

  it("close button on a tab calls closeTab", () => {
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://a.test/",
    );
    const closeSpy = vi.spyOn(useBrowserStore.getState(), "closeTab");

    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    const targetTab = tabs.find((t) => t.getAttribute("data-tab-id") === tabB);
    const closeBtn = within(targetTab!).getByLabelText(/close/i);
    fireEvent.click(closeBtn);

    expect(closeSpy).toHaveBeenCalledWith(TEST_WINDOW_ID, tabB);
  });

  it("clicking the + button calls addTab", () => {
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addTab");
    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const newTabBtn = screen.getByLabelText(/new tab/i);
    fireEvent.click(newTabBtn);
    expect(addSpy).toHaveBeenCalledWith(TEST_WINDOW_ID);
  });
});

describe("TabStrip — pinned tabs", () => {
  it("pinned tabs render with favicon-only width (no close button visible)", () => {
    const tabId = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://gh.test/",
    );
    useBrowserStore.getState().pinTab(TEST_WINDOW_ID, tabId);

    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    const pinned = tabs.find((t) => t.getAttribute("data-tab-id") === tabId);
    expect(pinned).toBeTruthy();
    expect(pinned?.getAttribute("data-pinned")).toBe("true");

    // Close button should NOT exist on pinned tabs
    expect(within(pinned!).queryByLabelText(/close/i)).toBeNull();
  });

  it("pinned tabs render before unpinned tabs", () => {
    const tabA = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://a.test/",
    );
    const tabB = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://b.test/",
    );
    useBrowserStore.getState().pinTab(TEST_WINDOW_ID, tabB);

    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    // tabB is pinned and should appear first
    expect(tabs[0].getAttribute("data-tab-id")).toBe(tabB);
  });
});

describe("TabStrip — drag handle", () => {
  it("each tab body exposes a drag handle (data-drag-handle)", () => {
    render(<TabStrip windowId={TEST_WINDOW_ID} />);
    const tabs = screen.getAllByRole("tab");
    // Each tab must expose data-drag-handle for Task 11's drag-out logic
    for (const tab of tabs) {
      expect(tab.querySelector("[data-drag-handle]")).toBeTruthy();
    }
  });
});

describe("TabStrip — graceful handling", () => {
  it("renders nothing when window doesn't exist", () => {
    const { container } = render(<TabStrip windowId="missing" />);
    expect(container.querySelectorAll('[role="tab"]').length).toBe(0);
  });
});
