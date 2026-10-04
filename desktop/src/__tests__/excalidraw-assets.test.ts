import { describe, it, expect } from "vitest";

const DESKTOP = import.meta.dirname.slice(0, -"/src/__tests__".length);

describe("excalidraw offline assets", () => {
  it("EXCALIDRAW_ASSET_PATH is same-origin after importing excalidraw-assets", async () => {
    const mod = await import(
      DESKTOP + "/src/apps/ProjectsApp/canvas/excalidraw-assets.ts"
    );
    const path = (globalThis as Record<string, unknown>).EXCALIDRAW_ASSET_PATH;
    expect(typeof path).toBe("string");
    const url = new URL(path as string, window.location.href);
    expect(url.origin).toBe(window.location.origin);
  });

  it("ExcalidrawBoard.tsx and mermaid-to-elements.ts import ./excalidraw-assets before @excalidraw", async () => {
    const { readFile } = await import("node:fs/promises");
    const boardSrc = await readFile(
      DESKTOP + "/src/apps/ProjectsApp/canvas/ExcalidrawBoard.tsx",
      "utf8",
    );
    const mermaidSrc = await readFile(
      DESKTOP + "/src/apps/ProjectsApp/canvas/mermaid-to-elements.ts",
      "utf8",
    );

    const assetLineRe = /^import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["']\.\/excalidraw-assets["']/m;
    const excalidrawLineRe =
      /^import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["']@excalidraw\/(?:excalidraw|mermaid-to-excalidraw)["']/m;

    const boardAssetMatch = boardSrc.match(assetLineRe);
    const boardExcalidrawMatch = boardSrc.match(excalidrawLineRe);
    expect(boardAssetMatch).not.toBeNull();
    expect(boardExcalidrawMatch).not.toBeNull();
    expect(boardAssetMatch!.index).toBeLessThan(boardExcalidrawMatch!.index);

    const mermaidAssetMatch = mermaidSrc.match(assetLineRe);
    const mermaidExcalidrawMatch = mermaidSrc.match(excalidrawLineRe);
    expect(mermaidAssetMatch).not.toBeNull();
    expect(mermaidExcalidrawMatch).not.toBeNull();
    expect(mermaidAssetMatch!.index).toBeLessThan(mermaidExcalidrawMatch!.index);
  });
});
