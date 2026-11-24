// @vitest-environment node
/**
 * Build-artefact guard for the webview compatibility shim (#2082).
 *
 * The desktop bundle calls runtime APIs that postdate old system webviews
 * (Object.hasOwn needs Chromium 93, structuredClone Chromium 98; Ubuntu
 * Touch's Morph webview is Chromium 87), so the SPA booted to a silent blank
 * screen there. The fix is public/compat.js: a feature-detected polyfill
 * script plus a window.onerror fallback that paints the failure instead.
 *
 * Like sw.js, compat.js is a public-dir classic script, not part of the
 * Rollup graph — so the bundler output, not the source, is what has to be
 * checked: this test runs a real `vite build` of the SPA into a temp dir
 * and inspects the emitted files, then executes the emitted shim in a vm
 * context stripped of the modern APIs to prove the polyfills behave.
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
let compat = "";
let sw = "";
const shells: Record<string, string> = {};

beforeAll(() => {
  outDir = mkdtempSync(path.join(tmpdir(), "taos-compat-bundle-"));
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
  compat = readFileSync(path.join(outDir, "compat.js"), "utf8");
  sw = readFileSync(path.join(outDir, "sw.js"), "utf8");
  for (const name of ["index.html", "chat.html", "app.html"]) {
    shells[name] = readFileSync(path.join(outDir, name), "utf8");
  }
}, 600_000);

afterAll(() => {
  if (outDir) rmSync(outDir, { recursive: true, force: true });
});

/** A vm context standing in for a browser window, with a minimal document
 *  stub (compat.js only touches it inside its error painter). */
function makeWindow() {
  const sandbox: Record<string, unknown> = {
    console,
    document: {
      getElementById: () => null,
      createElement: () => ({
        setAttribute: () => {},
        appendChild: () => {},
        style: {},
        textContent: "",
      }),
      addEventListener: () => {},
      documentElement: { appendChild: () => {} },
      body: null,
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

describe("built compat.js webview shim (#2082)", () => {
  it("ships at the output root and parses as a classic script", () => {
    expect(compat.length).toBeGreaterThan(0);
    expect(() => new vm.Script(compat, { filename: "compat.js" })).not.toThrow();
  });

  it("is loaded by every shell before its module entry", () => {
    for (const [name, html] of Object.entries(shells)) {
      const compatAt = html.indexOf("/desktop/compat.js");
      const moduleAt = html.indexOf('type="module"');
      expect(compatAt, `${name} references compat.js`).toBeGreaterThanOrEqual(0);
      expect(moduleAt, `${name} has a module entry`).toBeGreaterThanOrEqual(0);
      expect(compatAt, `${name} loads compat.js first`).toBeLessThan(moduleAt);
    }
    // The desktop shell also loads the pre-paint boot script; the shim the
    // bundle depends on must run before it too.
    const index = shells["index.html"];
    expect(index.indexOf("/desktop/compat.js")).toBeLessThan(index.indexOf("/desktop/boot.js"));
  });

  it("is precached by the service worker, like boot.js", () => {
    expect(sw).toContain("/desktop/compat.js");
  });

  it("installs working polyfills when the engine lacks the APIs", () => {
    const sandbox = makeWindow();
    // Simulate the old engine: the feature checks in compat.js test
    // `typeof x === "function"`, so undefined stands in for absence.
    vm.runInContext(
      "Object.hasOwn = undefined; structuredClone = undefined;" +
        "Array.prototype.at = undefined; String.prototype.at = undefined;",
      sandbox,
    );
    vm.runInContext(compat, sandbox);
    const results = vm.runInContext(
      `(function () {
        var out = {};
        out.hasOwn = Object.hasOwn({ a: 1 }, "a") === true && Object.hasOwn({}, "a") === false;
        var d = new Date(1234567);
        var src = { list: [1, { deep: 2 }], when: d };
        src.self = src;
        var copy = structuredClone(src);
        out.clone = copy !== src && copy.list !== src.list && copy.list[1].deep === 2 &&
          copy.when instanceof Date && copy.when.getTime() === 1234567 && copy.self === copy;
        out.arrayAt = [1, 2, 3].at(-1) === 3 && [1, 2, 3].at(0) === 1 && [1, 2, 3].at(9) === undefined;
        out.stringAt = "abc".at(-1) === "c" && "abc".at(3) === undefined;
        out.onerror = typeof window.onerror === "function";
        return out;
      })()`,
      sandbox,
    ) as Record<string, boolean>;
    expect(results).toEqual({
      hasOwn: true,
      clone: true,
      arrayAt: true,
      stringAt: true,
      onerror: true,
    });
  });

  it("never overwrites a native implementation", () => {
    const sandbox = makeWindow();
    vm.runInContext(
      `Object.hasOwn = function sentinelHasOwn() {};
       structuredClone = function sentinelClone() {};
       Array.prototype.at = function sentinelAt() {};
       String.prototype.at = function sentinelAt() {};`,
      sandbox,
    );
    const before = vm.runInContext(
      "[Object.hasOwn.name, structuredClone.name, Array.prototype.at.name, String.prototype.at.name].join(',')",
      sandbox,
    );
    vm.runInContext(compat, sandbox);
    const after = vm.runInContext(
      "[Object.hasOwn.name, structuredClone.name, Array.prototype.at.name, String.prototype.at.name].join(',')",
      sandbox,
    );
    expect(after).toBe(before);
    expect(after).toContain("sentinelHasOwn");
  });
});
