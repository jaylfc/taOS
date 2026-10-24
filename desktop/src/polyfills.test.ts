import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const originalHasOwn = Object.hasOwn;
const originalStructuredClone = globalThis.structuredClone;
const originalAt = Array.prototype.at;

function getAtPolyfillFromSource(): typeof Array.prototype.at {
  const polyfillsPath = join(__dirname, "polyfills.ts");
  const source = readFileSync(polyfillsPath, "utf-8");

  const match = source.match(
    /if \(typeof Array\.prototype\.at === "undefined"\) \{[\s\S]*?Object\.defineProperty\(Array\.prototype, "at", \{\s*value: (function at\(this: unknown, index: number\): unknown \{[\s\S]*?\n\s*\})\s*,/
  );

  if (!match) {
    throw new Error("Could not extract at polyfill from polyfills.ts");
  }

  let fnSource = match[1];
  fnSource = fnSource.replace(/\(this: unknown, index: number\): unknown/, "(index)");
  fnSource = fnSource.replace(/(\w+) as \w+/g, "$1");
  return new Function("return " + fnSource)() as typeof Array.prototype.at;
}

function deleteGlobalHasOwn() {
  try {
    delete (Object as any).hasOwn;
  } catch {
    // ignore
  }
}

function deleteGlobalStructuredClone() {
  try {
    delete (globalThis as any).structuredClone;
  } catch {
    // ignore
  }
}

function deleteGlobalAt() {
  try {
    delete (Array.prototype as any).at;
  } catch {
    // ignore
  }
}

function restoreGlobals() {
  if (originalHasOwn !== undefined) {
    Object.hasOwn = originalHasOwn;
  } else {
    deleteGlobalHasOwn();
  }
  if (originalStructuredClone !== undefined) {
    globalThis.structuredClone = originalStructuredClone;
  } else {
    deleteGlobalStructuredClone();
  }
  if (originalAt !== undefined) {
    Array.prototype.at = originalAt;
  } else {
    deleteGlobalAt();
  }
}

async function testWithPolyfill<T>(
  name: string,
  setup: () => void,
  testFn: (polyfillRefs?: { at?: typeof Array.prototype.at }) => T | Promise<T>
): Promise<T> {
  setup();
  vi.resetModules();
  try {
    await import("./polyfills");
    const atPolyfill = Array.prototype.at;
    if (originalAt !== undefined) {
      Array.prototype.at = originalAt;
    }
    return await testFn({ at: atPolyfill });
  } finally {
    restoreGlobals();
  }
}

describe("polyfills", () => {
  it("Object.hasOwn polyfill exists and works", async () => {
    await testWithPolyfill("Object.hasOwn", deleteGlobalHasOwn, () => {
      expect(typeof Object.hasOwn).toBe("function");
      expect(Object.hasOwn({ a: 1 }, "a")).toBe(true);
      expect(Object.hasOwn({ a: 1 }, "b")).toBe(false);
      const obj = Object.create({ inherited: true });
      obj.own = true;
      expect(Object.hasOwn(obj, "own")).toBe(true);
      expect(Object.hasOwn(obj, "inherited")).toBe(false);
    });
  });

  it("Array.prototype.at polyfill exists and works", () => {
    const atPolyfill = getAtPolyfillFromSource();
    expect(typeof atPolyfill).toBe("function");
    expect(atPolyfill).not.toBe(originalAt);

    const arr = [1, 2, 3];
    expect(atPolyfill.call(arr, 0)).toBe(1);
    expect(atPolyfill.call(arr, -1)).toBe(3);
    expect(atPolyfill.call(arr, -2)).toBe(2);
    expect(atPolyfill.call(arr, 5)).toBeUndefined();
    expect(atPolyfill.call(arr, -5)).toBeUndefined();

    const arrayLike = { 0: "a", 1: "b", 2: "c", length: 3 };
    expect(atPolyfill.call(arrayLike, 0)).toBe("a");
    expect(atPolyfill.call(arrayLike, -1)).toBe("c");
  });

  it("globalThis.structuredClone polyfill exists and works", async () => {
    await testWithPolyfill("structuredClone", deleteGlobalStructuredClone, () => {
      expect(typeof globalThis.structuredClone).toBe("function");

      const original: any = {
        name: "test",
        date: new Date("2024-01-01T00:00:00Z"),
        map: new Map([["key", "value"]]),
        nested: { value: 42 },
      };
      original.cycle = original;

      const cloned = globalThis.structuredClone(original);

      expect(cloned).not.toBe(original);
      expect(cloned.name).toBe("test");
      expect(cloned.date).toBeInstanceOf(Date);
      expect(cloned.date.getTime()).toBe(original.date.getTime());
      expect(cloned.map).toBeInstanceOf(Map);
      expect(cloned.map.get("key")).toBe("value");
      expect(cloned.map).not.toBe(original.map);
      expect(cloned.nested).not.toBe(original.nested);
      expect(cloned.nested.value).toBe(42);
      expect(cloned.cycle).toBe(cloned);
    });
  });

  it("structuredClone throws DataCloneError for functions", async () => {
    await testWithPolyfill("structuredClone throws on function", deleteGlobalStructuredClone, () => {
      try {
        globalThis.structuredClone(() => {});
        throw new Error("Expected to throw");
      } catch (e: any) {
        expect(e.name).toBe("DataCloneError");
      }
    });
  });

  it("structuredClone throws DataCloneError for symbols", async () => {
    await testWithPolyfill("structuredClone throws on symbol", deleteGlobalStructuredClone, () => {
      try {
        globalThis.structuredClone(Symbol("test"));
        throw new Error("Expected to throw");
      } catch (e: any) {
        expect(e.name).toBe("DataCloneError");
      }
    });
  });

  it("structuredClone copies typed array subarray with independent buffer", async () => {
    await testWithPolyfill("structuredClone typed array subarray", deleteGlobalStructuredClone, () => {
      const original = new Uint16Array([1, 2, 3, 4]).subarray(1, 3);
      const cloned = globalThis.structuredClone(original);

      expect(cloned).toBeInstanceOf(Uint16Array);
      expect(cloned.length).toBe(2);
      expect(cloned[0]).toBe(2);
      expect(cloned[1]).toBe(3);

      cloned[0] = 9;
      expect(original[0]).toBe(2);
    });
  });

  it("structuredClone copies DataView with independent buffer", async () => {
    await testWithPolyfill("structuredClone DataView", deleteGlobalStructuredClone, () => {
      const buffer = new ArrayBuffer(4);
      const view = new DataView(buffer);
      view.setUint8(0, 1);
      view.setUint8(1, 2);
      view.setUint8(2, 3);
      view.setUint8(3, 4);

      const cloned = globalThis.structuredClone(view);

      expect(cloned).toBeInstanceOf(DataView);
      expect(cloned.byteLength).toBe(4);
      expect(cloned.getUint8(0)).toBe(1);
      expect(cloned.getUint8(1)).toBe(2);
      expect(cloned.getUint8(2)).toBe(3);
      expect(cloned.getUint8(3)).toBe(4);

      cloned.setUint8(0, 99);
      expect(view.getUint8(0)).toBe(1);
    });
  });

  it("Array.prototype.at uses ToIntegerOrInfinity and handles edge cases", () => {
    const atPolyfill = getAtPolyfillFromSource();
    expect(typeof atPolyfill).toBe("function");
    expect(atPolyfill).not.toBe(originalAt);

    const arr = [10, 20, 30];
    expect(atPolyfill.call(arr, 1.5)).toBe(20);
    expect(atPolyfill.call(arr)).toBe(10);
    expect(atPolyfill.call(arr, -1)).toBe(30);
    expect(atPolyfill.call([], 0)).toBeUndefined();
    expect(atPolyfill.call(arr, Infinity)).toBeUndefined();
    expect(atPolyfill.call(arr, -Infinity)).toBeUndefined();
    expect(atPolyfill.call(arr, NaN)).toBe(10);
  });

  it("does not replace existing native Object.hasOwn", async () => {
    Object.hasOwn = originalHasOwn;
    const nativeHasOwn = Object.hasOwn;
    vi.resetModules();
    await import("./polyfills");
    expect(Object.hasOwn).toBe(nativeHasOwn);
  });

  it("does not replace existing native Array.prototype.at", async () => {
    Array.prototype.at = originalAt;
    const nativeAt = Array.prototype.at;
    vi.resetModules();
    await import("./polyfills");
    expect(Array.prototype.at).toBe(nativeAt);
  });

  it("does not replace existing native globalThis.structuredClone", async () => {
    globalThis.structuredClone = originalStructuredClone;
    const nativeStructuredClone = globalThis.structuredClone;
    vi.resetModules();
    await import("./polyfills");
    expect(globalThis.structuredClone).toBe(nativeStructuredClone);
  });
});