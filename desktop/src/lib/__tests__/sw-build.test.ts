import { describe, it, expect } from "vitest";
import { execSync } from "child_process";
import { existsSync, readFileSync } from "fs";
import { join } from "path";

function topLevelImportOrExport(line: string): boolean {
  const trimmed = line.trim();
  if (trimmed.length === 0) return false;
  if (trimmed.startsWith("//") || trimmed.startsWith("/*") || trimmed.startsWith("*")) return false;
  if (/^import\b/.test(trimmed) || /^export\b/.test(trimmed)) return true;
  return false;
}

describe("sw.js build artefact", () => {
  const swPath = join(process.cwd(), "..", "static", "desktop", "sw.js");

  beforeAll(() => {
    execSync("npx vite build --config vite.sw.config.ts", {
      cwd: join(process.cwd()),
      stdio: "inherit",
    });
  });

  it("exists after the dedicated SW build", () => {
    expect(existsSync(swPath)).toBe(true);
  });

  it("contains no top-level import or export statement", () => {
    const content = readFileSync(swPath, "utf-8");
    const bad = content.split("\n").find(topLevelImportOrExport);
    if (bad) {
      throw new Error(`Built sw.js contains a top-level import/export: ${bad.trim()}`);
    }
  });
});
