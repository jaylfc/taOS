#!/usr/bin/env node
import { readdirSync, statSync } from "node:fs";
import path from "node:path";

const desktop = path.resolve(process.cwd());
const srcFonts = path.join(
  desktop,
  "node_modules",
  "@excalidraw",
  "excalidraw",
  "dist",
  "prod",
  "fonts",
);
const distFonts = path.join(
  desktop,
  "..",
  "static",
  "desktop",
  "excalidraw-assets",
  "fonts",
);

function listFamilies(dir) {
  try {
    return new Set(readdirSync(dir).filter((f) => statSync(path.join(dir, f)).isDirectory()));
  } catch {
    return new Set();
  }
}

function listWoff2(dir) {
  try {
    return new Set(
      readdirSync(dir)
        .map((f) => path.join(dir, f))
        .filter((f) => statSync(f).isFile() && f.endsWith(".woff2"))
        .map((f) => path.basename(f)),
    );
  } catch {
    return new Set();
  }
}

const srcFamilies = listFamilies(srcFonts);
const distFamilies = listFamilies(distFonts);

let failed = false;

for (const family of srcFamilies) {
  const srcFamilyDir = path.join(srcFonts, family);
  const distFamilyDir = path.join(distFonts, family);
  if (!distFamilies.has(family)) {
    console.error(`MISSING family in dist: ${family}`);
    failed = true;
    continue;
  }
  const srcFiles = listWoff2(srcFamilyDir);
  const distFiles = listWoff2(distFamilyDir);
  for (const f of srcFiles) {
    if (!distFiles.has(f)) {
      console.error(`MISSING file in dist/${family}: ${f}`);
      failed = true;
    }
  }
}

const required = ["Excalifont", "Xiaolai"];
for (const r of required) {
  if (!distFamilies.has(r)) {
    console.error(`MISSING required family in dist: ${r}`);
    failed = true;
  } else if (listWoff2(path.join(distFonts, r)).size === 0) {
    console.error(`MISSING .woff2 files in dist/${r}`);
    failed = true;
  }
}

if (failed) {
  console.error("\nBuild has not copied the full Excalidraw font set.");
  process.exit(1);
}

console.log(`OK: ${distFonts} contains all ${srcFamilies.size} font families.`);
