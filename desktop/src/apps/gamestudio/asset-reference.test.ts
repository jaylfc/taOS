import { describe, it, expect } from "vitest";
import { buildAssetReference } from "./asset-reference";

describe("buildAssetReference", () => {
  it("emits an <img> tag for html files", () => {
    expect(buildAssetReference("index.html", "texture-1.png")).toContain(
      '<img src="./texture-1.png"',
    );
  });

  it("emits a JS const for js files with a filename-derived name", () => {
    const ref = buildAssetReference("game.js", "sprite-2.png");
    expect(ref).toContain('const sprite_2Url = "./sprite-2.png";');
  });

  it("derives distinct const names so repeated inserts don't collide", () => {
    const a = buildAssetReference("game.js", "texture-a.png");
    const b = buildAssetReference("game.js", "texture-b.png");
    expect(a).toContain("const texture_aUrl =");
    expect(b).toContain("const texture_bUrl =");
    expect(a).not.toEqual(b);
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
