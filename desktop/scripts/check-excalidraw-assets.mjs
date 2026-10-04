#!/usr/bin/env node
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(__dirname, "..");
const NODE_FONTS = path.join(DESKTOP, "node_modules", "@excalidraw", "excalidraw", "dist", "prod", "fonts");
const DIST = path.join(DESKTOP, "..", "static", "desktop");
const DIST_FONTS = path.join(DIST, "excalidraw-assets", "fonts");

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (entry.isFile()) {
      const full = entry.parentPath ? path.join(entry.parentPath, entry.name) : path.join(dir, entry.name);
      out.push(path.relative(dir, full));
    }
  }
  return out;
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

if (distCount < nodeCount) {
  console.error(`FAIL: dist has ${distCount} .woff2 files, expected ${nodeCount}`);
  process.exit(1);
}

console.log(`OK: ${distCount} .woff2 files across ${distFamilies.length} families`);
process.exit(0);
