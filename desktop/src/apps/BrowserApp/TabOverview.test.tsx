import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TabOverview } from "./TabOverview";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
});

describe("TabOverview", () => {
  it("renders one card per tab", () => {
    useBrowserStore.getState().addTab(TEST_WINDOW_ID, "https://a.test/");
    useBrowserStore.getState().addTab(TEST_WINDOW_ID, "https://b.test/");

    render(
      <TabOverview
        windowId={TEST_WINDOW_ID}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getAllByRole("tab").length).toBe(3);
  });

  it("clicking a card calls onSelect with the tab id and onClose", () => {
    const tabId = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://a.test/",
    );
    const onSelect = vi.fn();
    const onClose = vi.fn();

    render(
      <TabOverview
        windowId={TEST_WINDOW_ID}
        onSelect={onSelect}
        onClose={onClose}
      />,
    );

    const cards = screen.getAllByRole("tab");
    const target = cards.find((c) => c.getAttribute("data-tab-id") === tabId);
    fireEvent.click(target!);
    expect(onSelect).toHaveBeenCalledWith(tabId);
    expect(onClose).toHaveBeenCalled();
  });

  it("close button on a card calls closeTab without firing onSelect", () => {
    const tabId = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://a.test/",
    );
    const onSelect = vi.fn();
    const closeSpy = vi.spyOn(useBrowserStore.getState(), "closeTab");

    render(
      <TabOverview
        windowId={TEST_WINDOW_ID}
        onSelect={onSelect}
        onClose={() => {}}
      />,
    );

    const closeBtns = screen.getAllByLabelText(/close (https|new)/i);
    fireEvent.click(closeBtns[0]);
    expect(closeSpy).toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("Pinned section renders separately from open tabs section", () => {
    const pinnedId = useBrowserStore.getState().addTab(
      TEST_WINDOW_ID,
      "https://pinned.test/",
    );
    useBrowserStore.getState().pinTab(TEST_WINDOW_ID, pinnedId);

    render(
      <TabOverview
        windowId={TEST_WINDOW_ID}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByLabelText("Pinned tabs")).toBeTruthy();
    expect(screen.getByLabelText("Open tabs")).toBeTruthy();
  });

  it("'+ New tab' button creates a tab + selects it", () => {
    const onSelect = vi.fn();
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addTab");

    render(
      <TabOverview
        windowId={TEST_WINDOW_ID}
        onSelect={onSelect}
        onClose={() => {}}
      />,
    );

    const newTabBtn = screen.getByLabelText("New tab");
    fireEvent.click(newTabBtn);
    expect(addSpy).toHaveBeenCalledWith(TEST_WINDOW_ID);
    expect(onSelect).toHaveBeenCalled();
  });
});
