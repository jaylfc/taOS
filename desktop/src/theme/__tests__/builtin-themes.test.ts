// desktop/src/theme/__tests__/builtin-themes.test.ts
import { describe, it, expect } from "vitest";
import { BUILTIN_THEMES } from "../builtin-themes";
import { ALLOWED_TOKENS } from "../theme-config";

describe("builtin themes", () => {
  it("includes an undeletable taOS Dark (default), taOS Light and taOS Indigo", () => {
    const ids = BUILTIN_THEMES.map((t) => t.theme_id);
    expect(ids).toContain("default");
    expect(ids).toContain("light");
    expect(ids).toContain("indigo");
    expect(BUILTIN_THEMES.find((t) => t.theme_id === "default")!.builtin).toBe(true);
  });
  it("only uses allowlisted tokens", () => {
    for (const t of BUILTIN_THEMES)
      for (const k of Object.keys(t.config.tokens)) expect(ALLOWED_TOKENS.has(k)).toBe(true);
  });
  it("defaults indigo to the neural (particles) wallpaper", () => {
    const indigo = BUILTIN_THEMES.find((t) => t.theme_id === "indigo")!;
    expect(indigo.config.defaultWallpaperId).toBe("neural-live");
    // dark + light keep their own wallpaper defaults (no neural override).
    expect(BUILTIN_THEMES.find((t) => t.theme_id === "default")!.config.defaultWallpaperId).toBeUndefined();
    expect(BUILTIN_THEMES.find((t) => t.theme_id === "light")!.config.defaultWallpaperId).toBeUndefined();
  });
});
