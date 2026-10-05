import { describe, it, expect } from "vitest";
import { readFileSync, mkdirSync, writeFileSync, mkdtempSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";
import child_process from "node:child_process";

const DESKTOP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../");
const EXCALIDRAW_ASSETS = path.join(DESKTOP, "src/apps/ProjectsApp/canvas/excalidraw-assets.ts");
const EXCALIDRAW_BOARD = path.join(DESKTOP, "src/apps/ProjectsApp/canvas/ExcalidrawBoard.tsx");
const MERMAID = path.join(DESKTOP, "src/apps/ProjectsApp/canvas/mermaid-to-elements.ts");

const fs = { mkdtempSync, mkdirSync, writeFileSync };

function makeDirs(nodeContents: string[], distContents: string[]): { nodeDir: string; distDir: string } {
  const nodeDir = fs.mkdtempSync(path.join(os.tmpdir(), "excalidraw-node-"));
  const distDir = fs.mkdtempSync(path.join(os.tmpdir(), "excalidraw-dist-"));
  nodeContents.forEach((f) => {
    const p = path.join(nodeDir, f);
    mkdirSync(path.dirname(p), { recursive: true });
    writeFileSync(p, "");
  });
  distContents.forEach((f) => {
    const p = path.join(distDir, f);
    mkdirSync(path.dirname(p), { recursive: true });
    writeFileSync(p, "");
  });
  return { nodeDir, distDir };
}

describe("excalidraw-assets integration", () => {
  it("EXCALIDRAW_ASSET_PATH is same-origin after importing excalidraw-assets", async () => {
    const mod = await import(EXCALIDRAW_ASSETS);
    const before = (window as unknown as Record<string, string>).EXCALIDRAW_ASSET_PATH ?? "";
    expect(before).not.toBe("");
    const url = new URL(before);
    expect(url.origin).toBe(window.location.origin);
    // The path must resolve to the served /excalidraw-assets/ directory
    // so Excalidraw can fetch fonts same-origin without a CSP violation.
    expect(url.pathname).toMatch(/\/excalidraw-assets\/$/);
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

  it("check-excalidraw-assets.mjs passes when node and dist hold exactly the same .woff2 files", () => {
    const { nodeDir, distDir } = makeDirs(["Fam/a.woff2"], ["Fam/a.woff2"]);
    const result = child_process.spawnSync(process.execPath, [path.join(DESKTOP, "scripts", "check-excalidraw-assets.mjs")], {
      env: { ...process.env, EXCALIDRAW_NODE_FONTS: nodeDir, EXCALIDRAW_DIST_FONTS: distDir },
    });
    expect(result.status).toBe(0);
    expect(result.stderr.toString()).not.toContain("FAIL");
  });

  it("check-excalidraw-assets.mjs fails when dist holds a .woff2 node_modules does not", () => {
    const { nodeDir, distDir } = makeDirs(["Fam/a.woff2"], ["Fam/a.woff2", "Fam/stale.woff2"]);
    const result = child_process.spawnSync(process.execPath, [path.join(DESKTOP, "scripts", "check-excalidraw-assets.mjs")], {
      env: { ...process.env, EXCALIDRAW_NODE_FONTS: nodeDir, EXCALIDRAW_DIST_FONTS: distDir },
    });
    expect(result.status).toBe(1);
    expect(result.stderr.toString()).toContain("extra in dist");
  });
});
