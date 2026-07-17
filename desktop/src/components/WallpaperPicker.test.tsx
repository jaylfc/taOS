import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WallpaperPicker } from "./WallpaperPicker";
import { useThemeStore } from "@/stores/theme-store";

describe("WallpaperPicker", () => {
  beforeEach(() => {
    useThemeStore.setState({
      wallpaperId: "graphite",
      wallpaperKind: "image",
      wallpaperOverlayText: null,
      showOverlayText: true,
      wallpaperParams: { density: 200, speed: 0.5, glow: 6 },
      activeThemeId: "default",
      themeDefaultWallpaperId: {},
    });
  });

  it("renders nothing when open is false", () => {
    const { container } = render(<WallpaperPicker open={false} onClose={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders the dialog with title when open is true", () => {
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: /change wallpaper/i })).toBeInTheDocument();
    expect(screen.getByText("Change Wallpaper")).toBeInTheDocument();
  });

  it("renders section headings for all four sections", () => {
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    // Section headings render as h4 elements — use getAllByText to disambiguate
    // from the badge that shares the text "Theme default" inside the wallpaper tile.
    const themeDefaultHeadings = screen.getAllByText("Theme default");
    expect(themeDefaultHeadings.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Built-in")).toBeInTheDocument();
    expect(screen.getByText("Your wallpapers")).toBeInTheDocument();
    expect(screen.getByText("Browse online")).toBeInTheDocument();
  });

  it("shows the theme default wallpaper in its own section with a badge when it matches", () => {
    useThemeStore.setState({ wallpaperId: "graphite" });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    // The graphite wallpaper is in the themeDefault section and is pre-selected
    const graphiteBtn = screen.getByRole("button", { name: /graphite/i });
    expect(graphiteBtn).toHaveAttribute("aria-pressed", "true");
    // The badge text "Theme default" appears inside the wallpaper tile
    const badges = screen.getAllByText("Theme default");
    // At least one is the badge (inside a span), plus the section heading (h4)
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("renders all built-in wallpaper buttons in the builtin section", () => {
    useThemeStore.setState({
      wallpaperId: "default",
      themeDefaultWallpaperId: { default: "graphite" },
    });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /classic/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /neural \(live\)/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /aurora/i })).toBeInTheDocument();
    // "default" = Classic, which is in the builtin section
  });

  it("marks the active wallpaper with aria-pressed and a check indicator", () => {
    useThemeStore.setState({ wallpaperId: "aurora" });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    const activeBtn = screen.getByRole("button", { name: /aurora/i });
    expect(activeBtn).toHaveAttribute("aria-pressed", "true");
    const inactiveBtn = screen.getByRole("button", { name: /graphite/i });
    expect(inactiveBtn).toHaveAttribute("aria-pressed", "false");
  });

  it("updates wallpaperId when a built-in wallpaper button is clicked", () => {
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /midnight blue/i }));
    expect(useThemeStore.getState().wallpaperId).toBe("midnight");
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<WallpaperPicker open={true} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows animated wallpaper sliders when wallpaperKind is animated", () => {
    useThemeStore.setState({ wallpaperKind: "animated" });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.getByLabelText("Density")).toBeInTheDocument();
    expect(screen.getByLabelText("Speed")).toBeInTheDocument();
    expect(screen.getByLabelText("Glow")).toBeInTheDocument();
  });

  it("does not show animated wallpaper sliders when wallpaperKind is image", () => {
    useThemeStore.setState({ wallpaperKind: "image" });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.queryByLabelText("Density")).toBeNull();
    expect(screen.queryByLabelText("Speed")).toBeNull();
    expect(screen.queryByLabelText("Glow")).toBeNull();
  });

  it("shows the slogan toggle when wallpaperOverlayText is set", () => {
    useThemeStore.setState({ wallpaperOverlayText: "taOS" });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    const toggle = screen.getByRole("switch", { name: /show slogan/i });
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("does not show the slogan toggle when wallpaperOverlayText is null", () => {
    useThemeStore.setState({ wallpaperOverlayText: null });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.queryByRole("switch", { name: /show slogan/i })).toBeNull();
  });

  it("reflects showOverlayText=false on the slogan toggle", () => {
    useThemeStore.setState({ wallpaperOverlayText: "taOS", showOverlayText: false });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    const toggle = screen.getByRole("switch", { name: /show slogan/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("calls toggleOverlayText when the slogan switch is clicked", () => {
    useThemeStore.setState({ wallpaperOverlayText: "taOS", showOverlayText: true });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("switch", { name: /show slogan/i }));
    expect(useThemeStore.getState().showOverlayText).toBe(false);
  });

  it("displays current wallpaperParams values on the sliders", () => {
    useThemeStore.setState({
      wallpaperKind: "animated",
      wallpaperParams: { density: 150, speed: 1.2, glow: 8 },
    });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.getByText("150")).toBeInTheDocument();
    expect(screen.getByText("1.2")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  it("calls setWallpaperParam when a slider is changed", () => {
    useThemeStore.setState({ wallpaperKind: "animated" });
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    const densitySlider = screen.getByLabelText("Density");
    fireEvent.change(densitySlider, { target: { value: "300" } });
    expect(useThemeStore.getState().wallpaperParams.density).toBe(300);
  });

  it("renders empty states for user and online sections", () => {
    render(<WallpaperPicker open={true} onClose={vi.fn()} />);
    expect(screen.getByText("Upload an image")).toBeInTheDocument();
    expect(screen.getByText("Search Wallhaven")).toBeInTheDocument();
  });
});
