declare global {
  interface ObjectConstructor {
    hasOwn(obj: object, key: PropertyKey): boolean;
  }
  interface Array<T> {
    at(index: number): T | undefined;
  }
}

if (typeof Object.hasOwn === "undefined") {
  Object.hasOwn = function hasOwn(obj: object, key: PropertyKey): boolean {
    return Object.prototype.hasOwnProperty.call(obj, key);
  };
}

if (typeof Array.prototype.at === "undefined") {
  Object.defineProperty(Array.prototype, "at", {
    value: function at(this: unknown, index: number): unknown {
      const o = Object(this);
      const len = o.length >>> 0;
      if (len === 0) return undefined;
      let n = Math.trunc(Number(index)) || 0;
      if (n < 0) n += len;
      if (n < 0 || n >= len) return undefined;
      return o[n];
    },
    writable: true,
    configurable: true,
    enumerable: false,
  });
}

if (typeof globalThis.structuredClone === "undefined") {
  function isPlainObject(value: unknown): value is Record<string, unknown> {
    return Object.prototype.toString.call(value) === "[object Object]";
  }

  function cloneValue(
    value: unknown,
    seen: WeakMap<object, unknown>
  ): unknown {
    if (value === null || typeof value !== "object") {
      if (typeof value === "function" || typeof value === "symbol") {
        const err = new Error("DataCloneError") as Error & { name: string };
        err.name = "DataCloneError";
        throw err;
      }
      return value;
    }

    if (seen.has(value)) {
      return seen.get(value);
    }

    if (value instanceof Date) {
      return new Date(value.getTime());
    }

    if (value instanceof RegExp) {
      return new RegExp(value.source, value.flags);
    }

    if (value instanceof ArrayBuffer) {
      const copy = value.slice(0);
      seen.set(value, copy);
      return copy;
    }

    if (ArrayBuffer.isView(value)) {
      const buffer = value.buffer as ArrayBuffer;
      const copy = buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
      const cloned = value instanceof DataView
        ? new DataView(copy)
        : new (value.constructor as any)(copy);
      seen.set(value, cloned);
      return cloned;
    }

    if (value instanceof Map) {
      const cloned = new Map();
      seen.set(value, cloned);
      for (const [k, v] of value.entries()) {
        cloned.set(cloneValue(k, seen), cloneValue(v, seen));
      }
      return cloned;
    }

    if (value instanceof Set) {
      const cloned = new Set();
      seen.set(value, cloned);
      for (const v of value.values()) {
        cloned.add(cloneValue(v, seen));
      }
      return cloned;
    }

    if (Array.isArray(value)) {
      const cloned: unknown[] = [];
      seen.set(value, cloned);
      for (let i = 0; i < value.length; i++) {
        cloned[i] = cloneValue(value[i], seen);
      }
      return cloned;
    }

    if (isPlainObject(value)) {
      const cloned: Record<string, unknown> = {};
      seen.set(value, cloned);
      for (const key of Object.keys(value)) {
        cloned[key] = cloneValue(value[key], seen);
      }
      return cloned;
    }

    const err = new Error("DataCloneError") as Error & { name: string };
    err.name = "DataCloneError";
    throw err;
  }

  globalThis.structuredClone = function structuredClone<T>(value: T): T {
    return cloneValue(value, new WeakMap()) as T;
  };
}
