import { describe, it, expect, beforeEach } from "vitest";
import { useThemeStore, setWallpaperForActiveTheme, resolveWallpaper } from "../theme-store";

beforeEach(() => useThemeStore.setState({ activeThemeId: "matrix", wallpaperByTheme: {}, themeDefaultWallpaper: { matrix: "linear-gradient(#000,#020)" }, themeDefaultWallpaperId: {} } as never));

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

describe("getWallpapersBySection", () => {
  beforeEach(() => {
    useThemeStore.setState({
      activeThemeId: "default",
      themeDefaultWallpaperId: {},
    } as never);
  });

  it("returns 4 sections with correct ids and labels", () => {
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections).toHaveLength(4);
    expect(sections[0].id).toBe("themeDefault");
    expect(sections[0].label).toBe("Theme default");
    expect(sections[1].id).toBe("builtin");
    expect(sections[1].label).toBe("Built-in");
    expect(sections[2].id).toBe("user");
    expect(sections[2].label).toBe("Your wallpapers");
    expect(sections[3].id).toBe("online");
    expect(sections[3].label).toBe("Browse online");
  });

  it("falls back to graphite for themeDefault when no theme declares a default wallpaper id", () => {
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections[0].items).toHaveLength(1);
    expect(sections[0].items[0].id).toBe("graphite");
  });

  it("deduplicates the theme-default wallpaper from the builtin section", () => {
    // Use indigo theme which declares neural-live as its default
    useThemeStore.setState({
      activeThemeId: "indigo",
      themeDefaultWallpaperId: { indigo: "neural-live" },
    } as never);
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections[0].items[0].id).toBe("neural-live");
    const builtinIds = sections[1].items.map((w) => w.id);
    expect(builtinIds).not.toContain("neural-live");
    // All builtins except neural-live should be present
    expect(builtinIds).toContain("graphite");
    expect(builtinIds).toContain("default");
  });

  it("user and online sections are empty placeholders", () => {
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections[2].items).toHaveLength(0);
    expect(sections[3].items).toHaveLength(0);
  });

  it("still returns all wallpapers in builtin when themeDefault wallpaper is not found", () => {
    useThemeStore.setState({
      activeThemeId: "custom",
      themeDefaultWallpaperId: { custom: "nonexistent" },
    } as never);
    const sections = useThemeStore.getState().getWallpapersBySection();
    // Falls back to graphite since "nonexistent" is not in WALLPAPERS
    expect(sections[0].items[0].id).toBe("graphite");
  });
});
