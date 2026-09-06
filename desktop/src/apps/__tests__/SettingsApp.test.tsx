import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, within, fireEvent, act } from "@testing-library/react";
import { SettingsApp, DesktopDockSection } from "../SettingsApp";
import { useThemeStore } from "@/stores/theme-store";

function mockAuthStatus(isAdmin: boolean) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input).includes("/auth/status")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            configured: true,
            authenticated: true,
            user: { username: "jay", is_admin: isAdmin },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    // Every other endpoint (system info, cloud account, memory, etc.) is
    // irrelevant to this test, so just answer with "not available" and let
    // the mounted section's own fetches settle quietly.
    return Promise.resolve(new Response(null, { status: 404 }));
  });
}

describe("SettingsApp admin-only sections", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hides admin-only sections from a non-admin user", async () => {
    mockAuthStatus(false);
    render(<SettingsApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });

    await waitFor(() => {
      expect(screen.queryByText("Updates")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("Advanced")).not.toBeInTheDocument();

    // Personal sections stay visible in the sidebar.
    expect(within(nav).getByText("Themes")).toBeInTheDocument();
    expect(within(nav).getByText("Account")).toBeInTheDocument();
  });

  it("shows admin-only sections to an admin user", async () => {
    mockAuthStatus(true);
    render(<SettingsApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });

    await waitFor(() => {
      expect(within(nav).getByText("Updates")).toBeInTheDocument();
    });
    expect(within(nav).getByText("Users")).toBeInTheDocument();
    expect(within(nav).getByText("Advanced")).toBeInTheDocument();

    expect(within(nav).getByText("Themes")).toBeInTheDocument();
    expect(within(nav).getByText("Account")).toBeInTheDocument();
  });
});

describe("DesktopDockSection wallpaper entry", () => {
  beforeEach(() => {
    useThemeStore.setState({
      wallpaperId: "graphite",
      wallpaperImage: "url('/static/wallpaper-graphite.png')",
      wallpaperFallback: "#141415",
      wallpaperKind: "image",
      wallpaperOverlayText: null,
      showOverlayText: true,
      wallpaperParams: { density: 200, speed: 0.5, glow: 6 },
    });
  });

  it("shows the current wallpaper label", () => {
    render(<DesktopDockSection />);
    expect(screen.getByText("Graphite")).toBeInTheDocument();
  });

  it("shows a \"Change…\" button", () => {
    render(<DesktopDockSection />);
    expect(
      screen.getByRole("button", { name: /change…/i }),
    ).toBeInTheDocument();
  });

  it("opens the WallpaperPicker when \"Change…\" is clicked", () => {
    render(<DesktopDockSection />);
    fireEvent.click(screen.getByRole("button", { name: /change…/i }));
    expect(
      screen.getByRole("dialog", { name: /change wallpaper/i }),
    ).toBeInTheDocument();
  });

  it("closes the WallpaperPicker when its close button is clicked", () => {
    render(<DesktopDockSection />);
    fireEvent.click(screen.getByRole("button", { name: /change…/i }));
    const dialog = screen.getByRole("dialog", { name: /change wallpaper/i });
    expect(dialog).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /close/i }));
    expect(
      screen.queryByRole("dialog", { name: /change wallpaper/i }),
    ).toBeNull();
  });

  it("updates the wallpaper label when the store wallpaperId changes", () => {
    const { rerender } = render(<DesktopDockSection />);
    expect(screen.getByText("Graphite")).toBeInTheDocument();
    act(() => {
      useThemeStore.setState({ wallpaperId: "aurora" });
    });
    rerender(<DesktopDockSection />);
    expect(screen.getByText("Aurora")).toBeInTheDocument();
  });

  it("retains dock icon size and position controls", () => {
    render(<DesktopDockSection />);
    expect(screen.getByText("Dock icon size")).toBeInTheDocument();
    expect(screen.getByText("Dock position")).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Dock icon size" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Dock position" }),
    ).toBeInTheDocument();
  });
});
