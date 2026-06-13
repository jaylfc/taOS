import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { restoreActiveTheme, useThemeStore } from "../theme-store";

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  useThemeStore.setState({ activeThemeId: "default" });
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
