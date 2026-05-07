import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { BACKEND_META } from "./backends";

/**
 * Every backend id referenced from app-catalog/models/manifest files
 * must have a BACKEND_META entry. Falling through to the generic
 * fallback hid rk-llama-cpp / exo / onnxruntime / sd-webui /
 * sentence-transformers / ezrknpu in the Store filter.
 *
 * Cheap regex parse: every backend reference under requires.backends
 * is on its own line as `- id: <name>` (audit-manifests.py enforces
 * the schema). New backends added to a manifest must also land in
 * BACKEND_META — otherwise the filter pill loses its label and colour.
 */
function collectCatalogBackends(catalogRoot: string): Set<string> {
  const out = new Set<string>();
  const idRe = /^\s*-\s*id:\s*([A-Za-z0-9_.-]+)/;
  for (const entry of readdirSync(catalogRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const manifestPath = join(catalogRoot, entry.name, "manifest.yaml");
    let raw: string;
    try {
      raw = readFileSync(manifestPath, "utf-8");
    } catch {
      continue;
    }
    // Walk lines tracking YAML indent. Only capture `- id: <name>`
    // entries that sit under a `backends:` key. Variant ids live under
    // a sibling `variants:` block which we deliberately ignore.
    let underBackends = false;
    let backendsIndent = -1;
    for (const line of raw.split("\n")) {
      const indent = line.length - line.trimStart().length;
      const trimmed = line.trim();
      if (trimmed === "" || trimmed.startsWith("#")) continue;
      if (/^backends\s*:/.test(trimmed)) {
        underBackends = true;
        backendsIndent = indent;
        continue;
      }
      if (underBackends && indent <= backendsIndent) {
        underBackends = false;
      }
      if (underBackends) {
        const m = idRe.exec(line);
        if (m && m[1]) out.add(m[1]);
      }
    }
  }
  return out;
}

describe("BACKEND_META coverage", () => {
  it("includes every backend id referenced from the model catalog", () => {
    const catalogRoot = join(__dirname, "../../../../app-catalog/models");
    const referenced = collectCatalogBackends(catalogRoot);
    const missing = [...referenced].filter((id) => !(id in BACKEND_META)).sort();
    expect(missing).toEqual([]);
  });
});
