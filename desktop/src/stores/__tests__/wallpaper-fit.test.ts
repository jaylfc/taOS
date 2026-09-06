import { beforeEach, describe, expect, it } from "vitest";
import { useThemeStore, loadWallpaperFit } from "../theme-store";

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
    wallpaperFit: "fill",
    structure: {},
    effects: [],
    activeThemeId: "default",
    wallpaperByTheme: {},
    themeDefaultWallpaper: {},
    themeDefaultWallpaperId: {},
    wallpaperIdByTheme: {},
  });
};

describe("wallpaper-fit (per-device, CSS-driven)", () => {
  beforeEach(() => {
    reset();
    localStorage.clear();
  });

  /* ------------------------------------------------------------------ */
  /*  Per-device persistence                                              */
  /* ------------------------------------------------------------------ */

  it("setWallpaperFit stores the value under the current device id", () => {
    useThemeStore.getState().setWallpaperFit("fit");
    const deviceId = localStorage.getItem("taos-wallpaper-device-id")!;
    expect(localStorage.getItem("taos-wallpaper-fit:" + deviceId)).toBe("fit");
  });

  it("the stored value is isolated from another device id", () => {
    localStorage.setItem("taos-wallpaper-device-id", "device-A");
    localStorage.setItem("taos-wallpaper-fit:device-A", "stretch");
    localStorage.setItem("taos-wallpaper-device-id", "device-B");

    // Re-initialise fit state for the new device id
    useThemeStore.setState({ wallpaperFit: "fill" });

    useThemeStore.getState().setWallpaperFit("center");
    const deviceB = localStorage.getItem("taos-wallpaper-device-id")!;
    expect(localStorage.getItem("taos-wallpaper-fit:" + deviceB)).toBe("center");
    // device-A's choice must be untouched
    expect(localStorage.getItem("taos-wallpaper-fit:device-A")).toBe("stretch");
  });

  it("loads the persisted value as the initial store state", () => {
    localStorage.clear();
    // Set a known device id and stored fit before calling loadWallpaperFit,
    // simulating a fresh page load where localStorage already has the pref.
    localStorage.setItem("taos-wallpaper-device-id", "reload-device");
    localStorage.setItem("taos-wallpaper-fit:reload-device", "tile");
    expect(loadWallpaperFit()).toBe("tile");
  });

  /* ------------------------------------------------------------------ */
  /*  Default when nothing is stored                                      */
  /* ------------------------------------------------------------------ */

  it("defaults to fill when no preference is stored", () => {
    localStorage.clear();
    expect(loadWallpaperFit()).toBe("fill");
  });

  it("ignores an invalid stored value and falls back to fill", () => {
    localStorage.setItem("taos-wallpaper-device-id", "bad-device");
    localStorage.setItem("taos-wallpaper-fit:bad-device", "not-a-real-fit");
    expect(loadWallpaperFit()).toBe("fill");
  });

  /* ------------------------------------------------------------------ */
  /*  Store state updates                                                 */
  /* ------------------------------------------------------------------ */

  it("updates wallpaperFit in store state when setWallpaperFit is called", () => {
    const before = useThemeStore.getState().wallpaperFit;
    useThemeStore.getState().setWallpaperFit("stretch");
    expect(useThemeStore.getState().wallpaperFit).toBe("stretch");
    useThemeStore.getState().setWallpaperFit(before);
  });

  it("setWallpaperFit flips between two values round-tripping through the store", () => {
    useThemeStore.getState().setWallpaperFit("center");
    expect(useThemeStore.getState().wallpaperFit).toBe("center");
    useThemeStore.getState().setWallpaperFit("fill");
    expect(useThemeStore.getState().wallpaperFit).toBe("fill");
  });
});
