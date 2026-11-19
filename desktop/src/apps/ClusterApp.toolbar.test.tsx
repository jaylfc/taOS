import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClusterApp } from "./ClusterApp";

// jsdom has no layout engine, so the app's mobile breakpoint is driven off
// container.clientWidth. This test pins the narrow-width (540 px) behaviour:
// the tab list must scroll horizontally inside its own container (overflow-x
// auto, no wrap) and the "Add device" control must live outside the tab list,
// not overlaid on top of the "Resources" tab.
describe("ClusterApp narrow toolbar", () => {
  it("moves Add device out of the tab list and makes the tab list horizontally scrollable at 540 px", () => {
    const { container } = render(<ClusterApp windowId="w1" />);
    Object.defineProperty(container, "clientWidth", { value: 540, configurable: true });

    // The Resources tab is the one that used to be overlapped by Add device.
    const resourcesTab = screen.getByRole("tab", { name: /show cluster resources/i });
    const tabList = resourcesTab.parentElement!;
    expect(tabList).not.toBeNull();

    // The tab list scrolls horizontally and never wraps its buttons onto a
    // second row, so they cannot overlap the Add device control.
    expect(tabList).toHaveClass("overflow-x-auto");
    expect(tabList).toHaveClass("flex-nowrap");

    // Add device sits in the toolbar, outside the tab list element.
    const addDevice = screen.getByRole("button", { name: "Add a device" });
    expect(tabList.contains(addDevice)).toBe(false);
  });
});