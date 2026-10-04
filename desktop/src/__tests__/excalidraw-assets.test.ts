import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const DESKTOP = path.resolve(import.meta.dirname, "../../");
const EXCALIDRAW_ASSETS = path.join(DESKTOP, "src/apps/ProjectsApp/canvas/excalidraw-assets.ts");
const EXCALIDRAW_BOARD = path.join(DESKTOP, "src/apps/ProjectsApp/canvas/ExcalidrawBoard.tsx");
const MERMAID = path.join(DESKTOP, "src/apps/ProjectsApp/canvas/mermaid-to-elements.ts");

describe("excalidraw-assets integration", () => {
  it("EXCALIDRAW_ASSET_PATH is same-origin after importing excalidraw-assets", async () => {
    const mod = await import(EXCALIDRAW_ASSETS);
    const before = (window as unknown as Record<string, string>).EXCALIDRAW_ASSET_PATH ?? "";
    expect(before).not.toBe("");
    const url = new URL(before);
    expect(url.origin).toBe(window.location.origin);
  });

  it("ExcalidrawBoard.tsx and mermaid-to-elements.ts import ./excalidraw-assets before @excalidraw", async () => {
    const boardRaw = readFileSync(EXCALIDRAW_BOARD, "utf8");
    const mermaidRaw = readFileSync(MERMAID, "utf8");
    const assetImport = '"./excalidraw-assets"';
    const excalidrawImport = '"@excalidraw/excalidraw"';

    const boardLines = boardRaw.split("\n");
    const mermaidLines = mermaidRaw.split("\n");

    const boardAssetIdx = boardLines.findIndex((l) => l.includes(assetImport) && l.includes("import"));
    const boardExcalIdx = boardLines.findIndex((l) => l.includes(excalidrawImport) && l.includes("from"));
    const mermaidAssetIdx = mermaidLines.findIndex((l) => l.includes(assetImport) && l.includes("import"));
    const mermaidExcalIdx = mermaidLines.findIndex((l) => l.includes(excalidrawImport) && l.includes("from"));

    expect(boardAssetIdx).toBeGreaterThanOrEqual(0);
    expect(boardExcalIdx).toBeGreaterThanOrEqual(0);
    expect(boardAssetIdx).toBeLessThan(boardExcalIdx);

    expect(mermaidAssetIdx).toBeGreaterThanOrEqual(0);
    expect(mermaidExcalIdx).toBeGreaterThanOrEqual(0);
    expect(mermaidAssetIdx).toBeLessThan(mermaidExcalIdx);
  });
});
