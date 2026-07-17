import { beforeEach, describe, expect, it } from "vitest";
import { useThemeStore } from "./theme-store";

const reset = () => {
  useThemeStore.setState({
    wallpaperId: "graphite",
    wallpaperImage: "url('/static/wallpaper-graphite.png')",
    wallpaperMobileImage: "url('/static/wallpaper-graphite-mobile.png')",
    wallpaperFallback: "#141415",
    wallpaperLightImage: "url('/static/wallpaper-graphite-light.png')",
    wallpaperLightMobileImage: "url('/static/wallpaper-graphite-light-mobile.png')",
    wallpaperLightFallback: "#eef0f3",
    wallpaperKind: "image",
    wallpaperComponent: null,
    wallpaperOverlayText: null,
    showOverlayText: true,
    wallpaperParams: { density: 200, speed: 0.5, glow: 6 },
    showDesktopIcons: true,
    reduceEffects: false,
    structure: {},
    effects: [],
    activeThemeId: "default",
    wallpaperByTheme: {},
    themeDefaultWallpaper: {},
    themeDefaultWallpaperId: {},
    wallpaperIdByTheme: {},
    userWallpapers: [],
  });
};

describe("theme-store", () => {
  beforeEach(() => {
    reset();
    localStorage.clear();
  });

  it("setWallpaper updates wallpaper fields for a known wallpaper id", () => {
    useThemeStore.getState().setWallpaper("neural-live");
    const s = useThemeStore.getState();
    expect(s.wallpaperId).toBe("neural-live");
    expect(s.wallpaperImage).toBe("");
    expect(s.wallpaperKind).toBe("animated");
    expect(s.wallpaperComponent).toBe("particles");
    expect(s.wallpaperOverlayText).toBe("taOS");
    expect(s.wallpaperFallback).toBe("#141415");
  });

  it("setWallpaper records the choice under the active theme for later restore", () => {
    useThemeStore.setState({ activeThemeId: "custom-a" });
    useThemeStore.getState().setWallpaper("aurora");
    expect(useThemeStore.getState().wallpaperIdByTheme["custom-a"]).toBe("aurora");
  });

  it("setWallpaper ignores an unknown wallpaper id and leaves state untouched", () => {
    const before = useThemeStore.getState();
    useThemeStore.getState().setWallpaper("does-not-exist");
    const after = useThemeStore.getState();
    expect(after.wallpaperId).toBe(before.wallpaperId);
    expect(after.wallpaperImage).toBe(before.wallpaperImage);
    expect(after.wallpaperFallback).toBe(before.wallpaperFallback);
  });

  it("toggleOverlayText flips the flag and persists the pref", () => {
    const initial = useThemeStore.getState().showOverlayText;
    useThemeStore.getState().toggleOverlayText();
    expect(useThemeStore.getState().showOverlayText).toBe(!initial);
    expect(localStorage.getItem("taos-wallpaper-slogan")).toBe(initial ? "off" : "on");

    useThemeStore.getState().toggleOverlayText();
    expect(useThemeStore.getState().showOverlayText).toBe(initial);
    expect(localStorage.getItem("taos-wallpaper-slogan")).toBe(initial ? "on" : "off");
  });

  it("setWallpaperParam updates only the requested key and persists all params", () => {
    useThemeStore.getState().setWallpaperParam("density", 50);
    const params = useThemeStore.getState().wallpaperParams;
    expect(params.density).toBe(50);
    expect(params.speed).toBe(0.5);
    expect(params.glow).toBe(6);
    expect(JSON.parse(localStorage.getItem("taos-wallpaper-params")!)).toEqual({
      density: 50,
      speed: 0.5,
      glow: 6,
    });
  });

  it("toggleDesktopIcons flips the boolean", () => {
    const before = useThemeStore.getState().showDesktopIcons;
    useThemeStore.getState().toggleDesktopIcons();
    expect(useThemeStore.getState().showDesktopIcons).toBe(!before);
    useThemeStore.getState().toggleDesktopIcons();
    expect(useThemeStore.getState().showDesktopIcons).toBe(before);
  });

  it("setReduceEffects sets the flag and persists the pref", () => {
    useThemeStore.getState().setReduceEffects(true);
    expect(useThemeStore.getState().reduceEffects).toBe(true);
    expect(localStorage.getItem("taos-reduce-effects")).toBe("on");

    useThemeStore.getState().setReduceEffects(false);
    expect(useThemeStore.getState().reduceEffects).toBe(false);
    expect(localStorage.getItem("taos-reduce-effects")).toBe("off");
  });

  it("getWallpapers returns the full wallpaper catalogue with stable ids", () => {
    const list = useThemeStore.getState().getWallpapers();
    expect(list.length).toBeGreaterThan(0);
    const ids = list.map((w) => w.id);
    expect(ids).toContain("graphite");
    expect(ids).toContain("neural-live");
    expect(ids).toContain("default");
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("getWallpapersBySection returns structured sections with theme-default first", () => {
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections.length).toBe(4);

    // themeDefault section
    expect(sections[0].id).toBe("themeDefault");
    expect(sections[0].label).toBe("Theme default");
    // Falls back to graphite when no theme is set
    expect(sections[0].items[0].id).toBe("graphite");

    // builtin section
    expect(sections[1].id).toBe("builtin");
    expect(sections[1].label).toBe("Built-in");
    // All 11 wallpapers minus the theme default (graphite) = 10
    expect(sections[1].items.length).toBe(10);
    const builtinIds = sections[1].items.map((w) => w.id);
    expect(builtinIds).not.toContain("graphite"); // deduplicated

    // user and online are empty placeholders
    expect(sections[2].id).toBe("user");
    expect(sections[2].items).toHaveLength(0);
    expect(sections[3].id).toBe("online");
    expect(sections[3].items).toHaveLength(0);
  });

  it("getWallpapersBySection uses the active theme's declared default wallpaper id", () => {
    useThemeStore.setState({
      activeThemeId: "indigo",
      themeDefaultWallpaperId: { indigo: "neural-live" },
    });
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections[0].items[0].id).toBe("neural-live");
    expect(sections[1].items.map((w) => w.id)).not.toContain("neural-live");
  });

  it("getWallpapersBySection falls back to graphite when themeDefaultWallpaperId is not set", () => {
    useThemeStore.setState({
      activeThemeId: "custom-theme",
      themeDefaultWallpaperId: {},
    });
    const sections = useThemeStore.getState().getWallpapersBySection();
    expect(sections[0].items[0].id).toBe("graphite");
  });

  it("reset: showDesktopIcons defaults to true after explicit false then reset", () => {
    useThemeStore.getState().toggleDesktopIcons();
    expect(useThemeStore.getState().showDesktopIcons).toBe(false);
    reset();
    expect(useThemeStore.getState().showDesktopIcons).toBe(true);
  });

  // User wallpaper tests
  it("addUserWallpaper appends to the list", () => {
    useThemeStore.getState().addUserWallpaper({ id: "abc", label: "Test", url: "/api/desktop/wallpapers/abc" });
    const state = useThemeStore.getState();
    expect(state.userWallpapers).toHaveLength(1);
    expect(state.userWallpapers[0]!.id).toBe("abc");
    expect(state.userWallpapers[0]!.label).toBe("Test");
  });

  it("setWallpaper works with a user wallpaper id", () => {
    useThemeStore.getState().addUserWallpaper({ id: "uw1", label: "My Pic", url: "/api/desktop/wallpapers/uw1" });
    useThemeStore.getState().setWallpaper("user-uw1");
    const s = useThemeStore.getState();
    expect(s.wallpaperId).toBe("user-uw1");
    expect(s.wallpaperImage).toBe("url('/api/desktop/wallpapers/uw1')");
    expect(s.wallpaperFallback).toBe("#141415");
    expect(s.wallpaperKind).toBe("image");
  });

  it("getWallpapers includes user wallpapers after addUserWallpaper", () => {
    useThemeStore.getState().addUserWallpaper({ id: "aaa", label: "Upload", url: "/api/desktop/wallpapers/aaa" });
    const list = useThemeStore.getState().getWallpapers();
    expect(list.some((w) => w.id === "user-aaa")).toBe(true);
  });

  it("setWallpaper ignores unknown user wallpaper id when userWallpapers is empty", () => {
    const before = useThemeStore.getState();
    useThemeStore.getState().setWallpaper("user-nonexistent");
    const after = useThemeStore.getState();
    expect(after.wallpaperId).toBe(before.wallpaperId);
  });
});

// ---------------------------------------------------------------------------
// User wallpaper helpers (exported for WallpaperPicker tests)
// ---------------------------------------------------------------------------
export { reset as resetThemeStore };
