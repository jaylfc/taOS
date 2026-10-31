import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("licence gates", () => {
  it("no source-available packages in the lockfile", () => {
    const lockPath = resolve(__dirname, "../../package-lock.json");
    const lock = JSON.parse(readFileSync(lockPath, "utf-8"));
    const packages = lock.packages ?? {};
    const sourceAvailable: string[] = [];
    for (const [name, info] of Object.entries(packages)) {
      if (name === "") continue;
      const pkg = info as { license?: string; licenses?: string };
      const license = pkg.license ?? pkg.licenses ?? "";
      if (typeof license === "string" && license.startsWith("SEE LICENSE")) {
        sourceAvailable.push(name);
      }
    }
    expect(sourceAvailable).toEqual([]);
  });

  it("no tldraw imports in desktop/src", async () => {
    const modules = import.meta.glob("/src/**/*.{ts,tsx}", { eager: true, as: "raw" });
    let count = 0;
    for (const [file, source] of Object.entries(modules)) {
      if (file.endsWith("licences.test.ts")) continue;
      const matches = source.match(/from\s+["']@?tldraw/g);
      if (matches) count += matches.length;
    }
    expect(count).toBe(0);
  });
});
