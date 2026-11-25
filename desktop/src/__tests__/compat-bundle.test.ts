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

/** A vm window with a working fake DOM — element tree, id lookup,
 *  MutationObserver and controllable timers — so the boot-error overlay
 *  lifecycle (grace period, removal on mount) can be exercised. */
interface FakeEl {
  tagName: string;
  id: string;
  childNodes: FakeEl[];
  parentNode: FakeEl | null;
  style: Record<string, string>;
  textContent: string;
  setAttribute: (name: string, value: string) => void;
  appendChild: (child: FakeEl) => FakeEl;
  removeChild: (child: FakeEl) => FakeEl;
}

function makeDomWindow() {
  const observers: { target: FakeEl; cb: () => void; active: boolean }[] = [];
  const timers = new Map<number, { fn: () => void; fireAt: number }>();
  let nextTimer = 1;
  let now = 0;
  const listeners: Record<string, (() => void)[]> = {};

  function notify(el: FakeEl) {
    for (const o of observers.slice()) {
      if (o.active && o.target === el) o.cb();
    }
  }
  function makeEl(tag: string): FakeEl {
    const el: FakeEl = {
      tagName: tag,
      id: "",
      childNodes: [],
      parentNode: null,
      style: {},
      textContent: "",
      setAttribute: () => {},
      appendChild: (child: FakeEl) => {
        child.parentNode = el;
        el.childNodes.push(child);
        notify(el);
        return child;
      },
      removeChild: (child: FakeEl) => {
        const i = el.childNodes.indexOf(child);
        if (i >= 0) el.childNodes.splice(i, 1);
        child.parentNode = null;
        notify(el);
        return child;
      },
    };
    return el;
  }
  const documentElement = makeEl("html");
  const body = makeEl("body");
  const root = makeEl("div");
  root.id = "root";
  documentElement.appendChild(body);
  body.appendChild(root);

  function findById(el: FakeEl, id: string): FakeEl | null {
    if (el.id === id) return el;
    for (const c of el.childNodes) {
      const r = findById(c, id);
      if (r) return r;
    }
    return null;
  }
  class FakeMutationObserver {
    private obs: { target: FakeEl; cb: () => void; active: boolean } | null = null;
    constructor(private cb: () => void) {}
    observe(target: FakeEl) {
      this.obs = { target, cb: this.cb, active: true };
      observers.push(this.obs);
    }
    disconnect() {
      if (this.obs) this.obs.active = false;
    }
  }
  const document = {
    documentElement,
    body,
    getElementById: (id: string) => findById(documentElement, id),
    createElement: (tag: string) => makeEl(tag),
    addEventListener: (name: string, fn: () => void) => {
      (listeners[name] = listeners[name] || []).push(fn);
    },
  };
  const sandbox: Record<string, unknown> = {
    console,
    document,
    MutationObserver: FakeMutationObserver,
    setTimeout: (fn: () => void, ms: number) => {
      const id = nextTimer++;
      timers.set(id, { fn, fireAt: now + ms });
      return id;
    },
    clearTimeout: (id: number) => {
      timers.delete(id);
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const win = sandbox as unknown as {
    onerror: (m: string, s: string, l: number, c: number, e: Error) => boolean;
  };
  return {
    sandbox,
    root,
    fireDOMContentLoaded() {
      for (const fn of (listeners.DOMContentLoaded || []).slice()) fn();
    },
    advance(ms: number) {
      const target = now + ms;
      for (;;) {
        let dueId: number | null = null;
        let dueAt = Infinity;
        for (const [id, t] of timers) {
          if (t.fireAt <= target && t.fireAt < dueAt) {
            dueId = id;
            dueAt = t.fireAt;
          }
        }
        if (dueId === null) break;
        const t = timers.get(dueId);
        timers.delete(dueId);
        now = dueAt;
        if (t) t.fn();
      }
      now = target;
    },
    runTimers() {
      const pending = [...timers.values()].sort((a, b) => a.fireAt - b.fireAt);
      timers.clear();
      for (const t of pending) t.fn();
    },
    overlay() {
      return document.getElementById("taos-boot-error");
    },
    error(message: string) {
      win.onerror(message, "test.js", 1, 1, new Error(message));
    },
    mount() {
      root.appendChild(makeEl("div"));
    },
  };
}

describe("built compat.js webview shim (#2082)", () => {
  it("ships at the output root and parses as a classic script", () => {
    expect(compat.length).toBeGreaterThan(0);
    expect(() => new vm.Script(compat, { filename: "compat.js" })).not.toThrow();
  });

  it("is loaded by every shell before its module entry", () => {
    // Match the emitted <script> tag, not the first mention of the URL
    // anywhere in the document: the shells' explanatory comments mention
    // these URLs too (index.html's boot.js comment literally says the build
    // rewrites it to `/desktop/boot.js`), the build preserves comments, and
    // that sentence sits before the compat tag — a bare indexOf() reports a
    // load order the tags do not have (CI: compat string at 2713 vs boot
    // string at 1959, while the tags are in compat -> boot order).
    const compatTag = '<script src="/desktop/compat.js"';
    for (const [name, html] of Object.entries(shells)) {
      const compatAt = html.indexOf(compatTag);
      const moduleAt = html.indexOf('type="module"');
      expect(compatAt, `${name} references compat.js`).toBeGreaterThanOrEqual(0);
      expect(moduleAt, `${name} has a module entry`).toBeGreaterThanOrEqual(0);
      expect(compatAt, `${name} loads compat.js first`).toBeLessThan(moduleAt);
    }
    // The desktop shell also loads the pre-paint boot script; the shim the
    // bundle depends on must run before it too.
    const index = shells["index.html"];
    const bootAt = index.indexOf('<script src="/desktop/boot.js"');
    expect(bootAt, "index.html loads boot.js").toBeGreaterThanOrEqual(0);
    expect(index.indexOf(compatTag), "index.html loads compat.js before boot.js").toBeLessThan(bootAt);
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

  it("clones typed-array and DataView views over the cloned backing buffer", () => {
    const sandbox = makeWindow();
    vm.runInContext("structuredClone = undefined;", sandbox);
    vm.runInContext(compat, sandbox);
    const results = vm.runInContext(
      `(function () {
        var buffer = new ArrayBuffer(6);
        var all = new Uint8Array(buffer);
        all[2] = 7; all[3] = 8;
        var src = { buffer: buffer, view: new Uint8Array(buffer, 2, 2), dv: new DataView(buffer, 1, 3) };
        var copy = structuredClone(src);
        return {
          bufferCloned: copy.buffer !== buffer && copy.buffer.byteLength === 6,
          viewSharesClonedBuffer: copy.view.buffer === copy.buffer,
          viewBounds: copy.view.byteOffset === 2 && copy.view.length === 2,
          viewData: copy.view[0] === 7 && copy.view[1] === 8,
          dvSharesClonedBuffer: copy.dv.buffer === copy.buffer,
          dvBounds: copy.dv.byteOffset === 1 && copy.dv.byteLength === 3,
        };
      })()`,
      sandbox,
    ) as Record<string, boolean>;
    expect(results).toEqual({
      bufferCloned: true,
      viewSharesClonedBuffer: true,
      viewBounds: true,
      viewData: true,
      dvSharesClonedBuffer: true,
      dvBounds: true,
    });
  });

  it("throws DataCloneError for WeakMap, WeakSet and Promise values", () => {
    const sandbox = makeWindow();
    vm.runInContext("structuredClone = undefined;", sandbox);
    vm.runInContext(compat, sandbox);
    const results = vm.runInContext(
      `(function () {
        function throwsDataCloneError(value) {
          try {
            structuredClone(value);
            return false;
          } catch (e) {
            return !!e && e.name === "DataCloneError";
          }
        }
        function Foo() { this.x = 1; }
        var instance = structuredClone(new Foo());
        return {
          weakMap: throwsDataCloneError(new WeakMap()),
          weakSet: throwsDataCloneError(new WeakSet()),
          promise: throwsDataCloneError(Promise.resolve(1)),
          classInstanceStillClones: !!instance && instance.x === 1,
        };
      })()`,
      sandbox,
    ) as Record<string, boolean>;
    expect(results).toEqual({
      weakMap: true,
      weakSet: true,
      promise: true,
      classInstanceStillClones: true,
    });
  });

  it("clones an own __proto__ key as a data property, not a prototype change", () => {
    const sandbox = makeWindow();
    vm.runInContext("structuredClone = undefined;", sandbox);
    vm.runInContext(compat, sandbox);
    const results = vm.runInContext(
      `(function () {
        var src = JSON.parse('{"__proto__":{"flag":true}}');
        var copy = structuredClone(src);
        return {
          ownProperty: Object.prototype.hasOwnProperty.call(copy, "__proto__"),
          valueCloned: !!copy["__proto__"] && copy["__proto__"].flag === true,
          prototypeIntact: Object.getPrototypeOf(copy) === Object.prototype && copy.flag === undefined,
        };
      })()`,
      sandbox,
    ) as Record<string, boolean>;
    expect(results).toEqual({
      ownProperty: true,
      valueCloned: true,
      prototypeIntact: true,
    });
  });

  it("does not paint the boot error when the app mounts within the grace period", () => {
    const w = makeDomWindow();
    vm.runInContext(compat, w.sandbox);
    w.fireDOMContentLoaded();
    w.error("ResizeObserver loop completed with undelivered notifications");
    w.mount();
    w.advance(5000);
    expect(w.overlay()).toBeNull();
  });

  it("removes a painted boot error as soon as the app mounts", () => {
    const w = makeDomWindow();
    vm.runInContext(compat, w.sandbox);
    w.fireDOMContentLoaded();
    w.error("TypeError: Object.hasOwn is not a function");
    w.advance(2000);
    expect(w.overlay()).not.toBeNull();
    w.mount();
    expect(w.overlay()).toBeNull();
  });

  it("still paints the boot error when the app never mounts", () => {
    const w = makeDomWindow();
    vm.runInContext(compat, w.sandbox);
    w.fireDOMContentLoaded();
    w.error("TypeError: structuredClone is not a function");
    w.advance(1999);
    expect(w.overlay()).toBeNull();
    w.advance(1);
    expect(w.overlay()).not.toBeNull();
  });

  it("clones buffers and views from a foreign realm over the cloned buffer", () => {
    const sandbox = makeWindow();
    vm.runInContext("structuredClone = undefined;", sandbox);
    vm.runInContext(compat, sandbox);
    const foreign: Record<string, unknown> = {};
    vm.createContext(foreign);
    (sandbox as Record<string, unknown>).__foreign = vm.runInContext(
      `(function () {
         var buf = new ArrayBuffer(4);
         var all = new Uint8Array(buf);
         all[1] = 7; all[2] = 8;
         return { buf: buf, view: new Uint8Array(buf, 1, 2), dv: new DataView(buf, 1, 2) };
       })()`,
      foreign,
    );
    const results = vm.runInContext(
      `(function () {
         var copy = structuredClone(window.__foreign);
         return {
           bufIsLocalBuffer: copy.buf instanceof ArrayBuffer && copy.buf.byteLength === 4,
           viewSharesClonedBuffer: copy.view.buffer === copy.buf,
           viewBoundsAndData: copy.view.byteOffset === 1 && copy.view.length === 2 &&
             copy.view[0] === 7 && copy.view[1] === 8,
           dvSharesClonedBuffer: copy.dv.buffer === copy.buf,
           dvBounds: copy.dv.byteOffset === 1 && copy.dv.byteLength === 2,
         };
       })()`,
      sandbox,
    ) as Record<string, boolean>;
    expect(results).toEqual({
      bufIsLocalBuffer: true,
      viewSharesClonedBuffer: true,
      viewBoundsAndData: true,
      dvSharesClonedBuffer: true,
      dvBounds: true,
    });
  });
});
