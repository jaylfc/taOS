// @vitest-environment node
/**
 * Build-artefact guard for the service worker.
 *
 * sw-register.ts registers "/sw.js" as a CLASSIC worker (WebKit has no module
 * service workers, and iOS is a first-class surface). A classic worker script
 * cannot contain a static `import`/`export`, and a relative import would also
 * resolve against "/sw.js" at the root and 404. So the bundler output, not the
 * TypeScript source, is what has to be checked: this test runs a real
 * `vite build` of the SPA into a temp dir and inspects the emitted sw.js.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import vm from "node:vm";

const DESKTOP = path.resolve(__dirname, "..", "..");
const VITE_BIN = path.join(DESKTOP, "node_modules", "vite", "bin", "vite.js");

let outDir = "";
let sw = "";

beforeAll(() => {
  outDir = mkdtempSync(path.join(tmpdir(), "taos-sw-bundle-"));
  // Drop the vitest-injected mode so the child is a normal production build.
  const env = { ...process.env };
  delete env.NODE_ENV;
  delete env.VITEST;
  delete env.VITEST_MODE;
  execFileSync(
    process.execPath,
    [VITE_BIN, "build", "--outDir", outDir, "--emptyOutDir", "--logLevel", "error"],
    { cwd: DESKTOP, env, stdio: ["ignore", "pipe", "pipe"] },
  );
  sw = readFileSync(path.join(outDir, "sw.js"), "utf8");
}, 600_000);

afterAll(() => {
  if (outDir) rmSync(outDir, { recursive: true, force: true });
});

describe("built sw.js is a self-contained classic worker", () => {
  it("is the real worker, not an empty stub", () => {
    expect(sw).toContain("taos-static-");
    expect(sw).toContain("notificationclick");
  });

  it("has no top-level import/export statement", () => {
    const statement = /(?:^|[;{}\n])\s*(?:import\s*[{*"'`\w]|export\s*[{*\w])/;
    expect(sw.match(statement)?.[0] ?? null).toBeNull();
  });

  it("parses as a classic script (what the browser does on register)", () => {
    expect(() => new vm.Script(sw, { filename: "sw.js" })).not.toThrow();
  });

  it("references no external chunk and uses no dynamic import", () => {
    expect(sw).not.toMatch(/assets\/[\w.-]+\.js/);
    expect(sw).not.toMatch(/\bimport\s*\(/);
  });
});
