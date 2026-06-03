import { describe, it, expect, beforeEach } from "vitest";
import { useThemeStore, setWallpaperForActiveTheme, resolveWallpaper } from "../theme-store";

beforeEach(() => useThemeStore.setState({ activeThemeId: "matrix", wallpaperByTheme: {}, themeDefaultWallpaper: { matrix: "linear-gradient(#000,#020)" } } as never));

describe("wallpaper (decoupled, per-theme)", () => {
  it("resolves to the theme default when the user hasn't chosen", () => {
    expect(resolveWallpaper()).toBe("linear-gradient(#000,#020)");
  });
  it("remembers a per-theme user choice", () => {
    setWallpaperForActiveTheme("url('/x.png')");
    expect(resolveWallpaper()).toBe("url('/x.png')");
    useThemeStore.setState({ activeThemeId: "default" } as never);
    useThemeStore.setState({ activeThemeId: "matrix" } as never); // switch away + back
    expect(resolveWallpaper()).toBe("url('/x.png')");
  });
});
