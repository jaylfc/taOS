import { describe, it, expect } from "vitest";
import { buildAssetReference } from "./asset-reference";

describe("buildAssetReference", () => {
  it("emits an <img> tag for html files", () => {
    expect(buildAssetReference("index.html", "texture-1.png")).toContain(
      '<img src="./texture-1.png"',
    );
  });

  it("emits a JS const for js files", () => {
    const ref = buildAssetReference("game.js", "sprite-2.png");
    expect(ref).toContain('const assetUrl = "./sprite-2.png";');
  });

  it("emits a CSS background rule for css files", () => {
    expect(buildAssetReference("style.css", "tex.png")).toContain(
      'background-image: url("./tex.png")',
    );
  });

  it("falls back to a comment for unknown extensions", () => {
    expect(buildAssetReference("data.txt", "tex.png")).toBe("\n/* asset: ./tex.png */");
  });
});
