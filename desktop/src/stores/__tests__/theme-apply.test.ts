import { describe, it, expect, beforeEach } from "vitest";
import { applyThemeConfig, revertTheme } from "../theme-store";

beforeEach(() => { document.documentElement.removeAttribute("style"); });

describe("applyThemeConfig", () => {
  it("sets token CSS vars on :root and reverts", () => {
    applyThemeConfig({ tokens: { "--color-accent": "#00ff46" }, structure: {}, effects: [], requires: ["assistant","launcher"] });
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#00ff46");
    revertTheme();
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("");
  });

  it("ignores token keys not in the allowlist (defence in depth)", () => {
    applyThemeConfig({ tokens: { "--evil": "x" } as Record<string,string>, structure: {}, effects: [], requires: [] });
    expect(document.documentElement.style.getPropertyValue("--evil")).toBe("");
  });

  it("tags the root data-scheme from the theme's bg luminance", () => {
    // Light bg -> light scheme (drives the compatibility layer in tokens.css).
    applyThemeConfig({ tokens: { "--color-shell-bg": "#f4f5f7" }, structure: {}, effects: [], requires: [] });
    expect(document.documentElement.dataset.scheme).toBe("light");
    // rgba dark bg -> dark.
    applyThemeConfig({ tokens: { "--color-shell-bg": "rgba(22, 25, 32, 0.92)" }, structure: {}, effects: [], requires: [] });
    expect(document.documentElement.dataset.scheme).toBe("dark");
    // No bg override (default theme) -> dark base.
    applyThemeConfig({ tokens: { "--color-accent": "#abc" }, structure: {}, effects: [], requires: [] });
    expect(document.documentElement.dataset.scheme).toBe("dark");
    // revert resets to dark.
    applyThemeConfig({ tokens: { "--color-shell-bg": "#ffffff" }, structure: {}, effects: [], requires: [] });
    revertTheme();
    expect(document.documentElement.dataset.scheme).toBe("dark");
  });
});
