import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Dock } from "@/components/Dock";
import { useThemeStore } from "@/stores/theme-store";
import { useDockStore } from "@/stores/dock-store";

describe("Dock variant selection", () => {
  it("renders the macos-dock variant by default and shows pinned apps", () => {
    useDockStore.setState({ pinned: ["files"] } as never);
    useThemeStore.setState({ structure: {} } as never);
    render(<Dock onLaunchpadOpen={() => {}} />);
    expect(screen.getByTestId("dock-variant-macos-dock")).toBeInTheDocument();
  });
  it("renders the windows-taskbar variant when the theme selects it", () => {
    useThemeStore.setState({ structure: { dock: { variant: "windows-taskbar" } } } as never);
    render(<Dock onLaunchpadOpen={() => {}} />);
    expect(screen.getByTestId("dock-variant-windows-taskbar")).toBeInTheDocument();
  });
});

describe("Dock — Desktop & Dock settings flow to the rendered dock (#1603)", () => {
  it("applies the dock-store icon size to pinned app buttons", () => {
    useDockStore.setState({ pinned: ["files"], iconSize: "large", position: "bottom" } as never);
    useThemeStore.setState({ structure: {} } as never);
    render(<Dock onLaunchpadOpen={() => {}} />);
    const icon = screen.getByRole("button", { name: /open files/i });
    expect(icon.className).toContain("w-14");
    expect(icon.className).toContain("h-14");
  });

  it("moves the dock to the left edge when the dock-store position is 'left'", () => {
    useDockStore.setState({ pinned: ["files"], iconSize: "medium", position: "left" } as never);
    useThemeStore.setState({ structure: {} } as never);
    render(<Dock onLaunchpadOpen={() => {}} />);
    const dock = screen.getByTestId("dock-variant-macos-dock");
    expect(dock.className).toContain("left-3");
    expect(dock.className).not.toContain("bottom-3");
  });
});
