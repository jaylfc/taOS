// @vitest-environment node
import { describe, it, expect } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spaVersionPlugin } from "../spa-version-plugin.mjs";

describe("spaVersionPlugin writes version.json with the passed version", () => {
  it("uses the version argument and ignores readBackendVersion", () => {
    const tmp = mkdtempSync(path.join(tmpdir(), "spa-ver-"));
    try {
      const testVersion = "1.2.3+abc.xyz";
      const p = spaVersionPlugin(testVersion);
      p.configResolved({ root: tmp, build: { outDir: "." } });
      p.writeBundle();
      const versionJson = JSON.parse(readFileSync(path.join(tmp, "version.json"), "utf8"));
      expect(versionJson.version).toBe(testVersion);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});