#!/usr/bin/env node
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(__dirname, "..");

// Read the outDir from vite.config.ts so the check stays in sync if it changes.
let DIST = path.join(DESKTOP, "..", "static", "desktop");
try {
  const viteConfig = readFileSync(path.join(DESKTOP, "vite.config.ts"), "utf8");
  const m = viteConfig.match(/outDir:\s*"([^"]+)"/);
  if (m) {
    // vite.config.ts outDir is relative to config.root (the desktop/ dir),
    // e.g. "../static/desktop". Resolve it to an absolute path.
    DIST = path.resolve(DESKTOP, m[1]);
  }
} catch {}

const NODE_FONTS =
  process.env.EXCALIDRAW_NODE_FONTS || path.join(DESKTOP, "node_modules", "@excalidraw", "excalidraw", "dist", "prod", "fonts");
const DIST_FONTS = process.env.EXCALIDRAW_DIST_FONTS || path.join(DIST, "excalidraw-assets", "fonts");

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true, recursive: true })
    .filter((e) => e.isFile())
    .map((e) => {
      const full = e.parentPath ? path.join(e.parentPath, e.name) : path.join(dir, e.name);
      return path.relative(dir, full);
    });
}

function families(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name);
}

function hasWoff2(dir, family) {
  const famDir = path.join(dir, family);
  if (!existsSync(famDir)) return false;
  return readdirSync(famDir).some((f) => f.endsWith(".woff2"));
}

if (!existsSync(NODE_FONTS)) {
  console.error(`FAIL: node_modules fonts directory not found: ${NODE_FONTS}`);
  process.exit(1);
}

if (!existsSync(DIST_FONTS)) {
  console.error(`FAIL: dist fonts directory not found: ${DIST_FONTS}`);
  process.exit(1);
}

const nodeFamilies = families(NODE_FONTS);
const distFamilies = families(DIST_FONTS);

const missing = nodeFamilies.filter((f) => !hasWoff2(DIST_FONTS, f));

if (missing.length > 0) {
  console.error(`FAIL: missing families in dist: ${missing.join(", ")}`);
  process.exit(1);
}

const nodeCount = walk(NODE_FONTS).filter((f) => f.endsWith(".woff2")).length;
const distCount = walk(DIST_FONTS).filter((f) => f.endsWith(".woff2")).length;

if (nodeCount === 0) {
  console.error("FAIL: no .woff2 files found in node_modules fonts");
  process.exit(1);
}

const nodeWoff2 = new Set(walk(NODE_FONTS).filter((f) => f.endsWith(".woff2")));
const distWoff2 = new Set(walk(DIST_FONTS).filter((f) => f.endsWith(".woff2")));
const missingInDist = [...nodeWoff2].filter((f) => !distWoff2.has(f));
const extraInDist = [...distWoff2].filter((f) => !nodeWoff2.has(f));

if (extraInDist.length > 0) {
  const preview = extraInDist.slice(0, 5).join(", ");
  console.error(`FAIL: extra in dist: ${preview}`);
  process.exit(1);
}

if (missingInDist.length > 0) {
  const preview = missingInDist.slice(0, 5).join(", ");
  console.error(`FAIL: missing in dist: ${preview}`);
  process.exit(1);
}

console.log(`OK: ${distCount} .woff2 files across ${distFamilies.length} families`);
process.exit(0);
