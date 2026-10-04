import { describe, it, expect } from "vitest";
import { resolveExcalidrawFontPath } from "../../vite.config";

describe("resolveExcalidrawFontPath", () => {
  const srcFonts = "/x/fonts";
  const base = "/desktop/";

  it("resolves a valid excalidraw font path", () => {
    expect(resolveExcalidrawFontPath(srcFonts, base, "/desktop/excalidraw-assets/fonts/Excalifont/a.woff2"))
      .toBe("/x/fonts/Excalifont/a.woff2");
  });

  it("strips query strings", () => {
    expect(resolveExcalidrawFontPath(srcFonts, base, "/desktop/excalidraw-assets/fonts/Excalifont/a.woff2?v=1"))
      .toBe("/x/fonts/Excalifont/a.woff2");
  });

  it("returns null when base is missing", () => {
    expect(resolveExcalidrawFontPath(srcFonts, base, "/excalidraw-assets/fonts/Excalifont/a.woff2"))
      .toBeNull();
  });

  it("returns null for path traversal", () => {
    expect(resolveExcalidrawFontPath(srcFonts, base, "/desktop/excalidraw-assets/fonts/..%2F..%2Fpackage.json"))
      .toBeNull();
  });

  it("returns null for empty remainder", () => {
    expect(resolveExcalidrawFontPath(srcFonts, base, "/desktop/excalidraw-assets/fonts/"))
      .toBeNull();
  });
});
