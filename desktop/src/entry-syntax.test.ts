// @vitest-environment node
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { transformWithOxc } from "vite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const projectRoot = resolve(__dirname, "..");

const entries = [
  "src/main.tsx",
  "src/app-standalone-main.tsx",
  "src/chat-main.tsx",
];

describe("entry syntax compatibility with Chrome 87", () => {
  for (const entry of entries) {
    const filePath = join(projectRoot, entry);

    it(`${entry} transforms without error for target chrome87`, async () => {
      const source = readFileSync(filePath, "utf-8");
      const result = await transformWithOxc(source, entry, {
        lang: "tsx",
        target: "chrome87",
      });
      const parseErrors = result.warnings.filter((w) => w.code === "PARSE_ERROR");
      expect(parseErrors).toHaveLength(0);
    });

    it(`${entry} has ./polyfills as the first import`, () => {
      const source = readFileSync(filePath, "utf-8");
      const lines = source.split("\n");
      const firstImportLine = lines.find((line) => line.trim().startsWith("import "));
      expect(firstImportLine).toBeDefined();
      expect(firstImportLine?.trim()).toBe('import "./polyfills";');
    });
  }
});