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
