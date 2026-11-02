// @vitest-environment node
import { describe, it, expect } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spaVersionPlugin } from "../spa-version-plugin.mjs";

describe("spaVersionPlugin", () => {
  it("writes the passed version into version.json", () => {
    const tmp = mkdtempSync(path.join(tmpdir(), "spa-ver-"));
    try {
      const p = spaVersionPlugin("1.2.3+abc.xyz");
      p.configResolved({ root: tmp, build: { outDir: "." } });
      p.writeBundle();
      expect(JSON.parse(readFileSync(path.join(tmp, "version.json"), "utf8")).version).toBe("1.2.3+abc.xyz");
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});
