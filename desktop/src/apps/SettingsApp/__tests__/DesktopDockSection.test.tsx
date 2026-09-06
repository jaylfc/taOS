import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopDockSection } from "@/apps/SettingsApp";
import { useDockStore } from "@/stores/dock-store";

beforeEach(() => {
  useDockStore.setState({
    pinned: ["messages", "agents", "files", "store", "settings"],
    iconSize: "medium",
    position: "bottom",
  });
});

describe("DesktopDockSection — writes to the dock-store (#1603)", () => {
  it("updates the dock-store icon size instead of an orphaned localStorage key", () => {
    render(<DesktopDockSection />);

    fireEvent.click(screen.getByRole("button", { name: "Large" }));

    expect(useDockStore.getState().iconSize).toBe("large");
    expect(localStorage.getItem("taos-dock-size")).toBeNull();
  });

  it("updates the dock-store position instead of an orphaned localStorage key", () => {
    render(<DesktopDockSection />);

    fireEvent.click(screen.getByRole("button", { name: "Left" }));

    expect(useDockStore.getState().position).toBe("left");
    expect(localStorage.getItem("taos-dock-position")).toBeNull();
  });

  it("reflects the current dock-store values as pressed", () => {
    useDockStore.setState({ iconSize: "small", position: "left" });
    render(<DesktopDockSection />);

    expect(screen.getByRole("button", { name: "Small" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Left" })).toHaveAttribute("aria-pressed", "true");
  });
});
