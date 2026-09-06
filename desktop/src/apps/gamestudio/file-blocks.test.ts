import { describe, it, expect } from "vitest";
import { parseFileBlocks, isSafeFilename } from "./file-blocks";

describe("parseFileBlocks", () => {
  it("parses a single file block", () => {
    const text = [
      "Sure, here's the change:",
      "",
      "### FILE: game.js",
      "```js",
      "console.log('hi');",
      "```",
      "",
      "Let me know if you want more.",
    ].join("\n");
    const files = parseFileBlocks(text);
    expect(files).toEqual([{ path: "game.js", content: "console.log('hi');" }]);
  });

  it("parses multiple file blocks", () => {
    const text = [
      "### FILE: index.html",
      "```html",
      "<!doctype html>",
      "<html></html>",
      "```",
      "### FILE: game.js",
      "```",
      "let x = 1;",
      "let y = 2;",
      "```",
    ].join("\n");
    const files = parseFileBlocks(text);
    expect(files).toEqual([
      { path: "index.html", content: "<!doctype html>\n<html></html>" },
      { path: "game.js", content: "let x = 1;\nlet y = 2;" },
    ]);
  });

  it("returns an empty array when there are no file blocks", () => {
    expect(parseFileBlocks("Sorry, I can't help with that.")).toEqual([]);
  });

  it("returns an empty array for an unterminated fence", () => {
    const text = ["### FILE: game.js", "```js", "console.log('unterminated');"].join("\n");
    expect(parseFileBlocks(text)).toEqual([]);
  });

  it("rejects unsafe filenames (traversal, separators)", () => {
    for (const bad of ["../evil.js", "sub/dir.js", "sub\\dir.js", "..", "."]) {
      const text = ["### FILE: " + bad, "```", "content", "```"].join("\n");
      expect(parseFileBlocks(text)).toEqual([]);
    }
  });
});

describe("isSafeFilename", () => {
  it("accepts flat filenames", () => {
    expect(isSafeFilename("game.js")).toBe(true);
    expect(isSafeFilename("index.html")).toBe(true);
  });

  it("rejects traversal and separators", () => {
    expect(isSafeFilename("../evil.js")).toBe(false);
    expect(isSafeFilename("a/b.js")).toBe(false);
    expect(isSafeFilename("a\\b.js")).toBe(false);
    expect(isSafeFilename("..")).toBe(false);
    expect(isSafeFilename(".")).toBe(false);
    expect(isSafeFilename("")).toBe(false);
  });
});
