import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { restoreActiveTheme, keepTheme, useThemeStore } from "../theme-store";

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  useThemeStore.setState({
    activeThemeId: "default",
    wallpaperByTheme: {},
    wallpaperIdByTheme: {},
    wallpaperId: "graphite",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("restoreActiveTheme", () => {
  it("applies the saved theme's tokens to :root on boot", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/preferences/themes")) {
        return Promise.resolve(new Response(JSON.stringify({ active_theme_id: "light" })));
      }
      return Promise.resolve(new Response(JSON.stringify([])));
    });

    await restoreActiveTheme();

    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#5b6472");
    expect(useThemeStore.getState().activeThemeId).toBe("light");
  });

  it("is a no-op when no active theme is saved", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({})));

    await restoreActiveTheme();

    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("");
    expect(useThemeStore.getState().activeThemeId).toBe("default");
  });

  it("does not throw on fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    await expect(restoreActiveTheme()).resolves.toBeUndefined();
  });
});

describe("wallpaper restore on theme switch", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
  });

  it("applies a theme's defaultWallpaperId when it declares one", () => {
    keepTheme("indigo", { tokens: {}, defaultWallpaperId: "neural-live" });
    expect(useThemeStore.getState().wallpaperId).toBe("neural-live");
  });

  it("restores the global default when switching to a theme with no default (not the previous theme's wallpaper)", () => {
    // Indigo set neural-live; switching to Dark/Light (no defaultWallpaperId)
    // must not leave neural-live stuck.
    keepTheme("indigo", { tokens: {}, defaultWallpaperId: "neural-live" });
    expect(useThemeStore.getState().wallpaperId).toBe("neural-live");
    keepTheme("default", { tokens: {} });
    expect(useThemeStore.getState().wallpaperId).toBe("graphite");
  });

  it("restores the user's explicit per-theme wallpaper choice over the default", () => {
    keepTheme("default", { tokens: {} });
    useThemeStore.getState().setWallpaper("ocean"); // user picks Ocean for Dark
    keepTheme("indigo", { tokens: {}, defaultWallpaperId: "neural-live" });
    expect(useThemeStore.getState().wallpaperId).toBe("neural-live");
    keepTheme("default", { tokens: {} }); // back to Dark -> Ocean restored
    expect(useThemeStore.getState().wallpaperId).toBe("ocean");
  });
});

describe("restoreActiveTheme does not clobber the persisted wallpaper", () => {
  // On boot, wallpaperIdByTheme is empty (in-memory) and useSessionPersistence
  // restores the user's persisted wallpaper. restoreActiveTheme must not race
  // that restore back to the global default for a theme with no defaultWallpaperId.
  it("leaves a user's custom wallpaper untouched when the restored theme declares no default", async () => {
    // The Light builtin theme has a `wallpaper` but no `defaultWallpaperId`.
    vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/preferences/themes")) {
        return Promise.resolve(new Response(JSON.stringify({ active_theme_id: "light" })));
      }
      return Promise.resolve(new Response(JSON.stringify([])));
    });
    // Stand in for useSessionPersistence having restored the persisted choice.
    useThemeStore.getState().setWallpaper("ocean");
    expect(useThemeStore.getState().wallpaperId).toBe("ocean");

    await restoreActiveTheme();

    expect(useThemeStore.getState().activeThemeId).toBe("light");
    // NOT reset to the global graphite default.
    expect(useThemeStore.getState().wallpaperId).toBe("ocean");
  });

  it("does NOT override the user's persisted wallpaper with the theme's default (#1603)", async () => {
    // The user actively picked Ocean while on Indigo. On boot,
    // useSessionPersistence restores that persisted pick; restoreActiveTheme
    // must not stomp it back to Indigo's declared default (neural-live).
    vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/preferences/themes")) {
        return Promise.resolve(new Response(JSON.stringify({ active_theme_id: "indigo" })));
      }
      // Provide an Indigo-like theme that declares neural-live as its default.
      return Promise.resolve(
        new Response(JSON.stringify([{ theme_id: "indigo", config: { tokens: {}, defaultWallpaperId: "neural-live" } }])),
      );
    });
    // Stand in for useSessionPersistence having restored the user's pick.
    useThemeStore.getState().setWallpaper("ocean");
    expect(useThemeStore.getState().wallpaperId).toBe("ocean");

    await restoreActiveTheme();

    expect(useThemeStore.getState().activeThemeId).toBe("indigo");
    // The user's explicit pick wins over the theme's declared default.
    expect(useThemeStore.getState().wallpaperId).toBe("ocean");
  });
});
