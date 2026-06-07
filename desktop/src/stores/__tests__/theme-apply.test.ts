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
});
