import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, "MessagesApp.tsx"), "utf-8");

const HARDCODED = /\b(bg|text|border)-(white|black)(?:\/(?:\d+|\[[\d.]+\]))?\b/g;

describe("MessagesApp palette token regression", () => {
  it("contains no hardcoded white/black palette utilities", () => {
    const hits: string[] = [];
    for (const line of source.split("\n")) {
      if (line.includes("palette-ok")) continue;
      let m: RegExpExecArray | null;
      while ((m = HARDCODED.exec(line)) !== null) {
        hits.push(m[0]);
      }
      HARDCODED.lastIndex = 0;
    }
    expect(hits, `hardcoded palette utilities found: ${hits.join(", ")}`).toEqual([]);
  });
});
