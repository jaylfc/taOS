import "@testing-library/jest-dom";

// Node.js 25+ ships a built-in localStorage stub that is broken when
// --localstorage-file is not provided (all methods are undefined).
// Polyfill it with an in-memory implementation so tests can call
// localStorage.clear(), setItem(), getItem(), etc.
if (typeof localStorage === "undefined" || typeof localStorage.clear !== "function") {
  const store = new Map<string, string>();
  const impl = {
    get length() { return store.size; },
    key(index: number) { return [...store.keys()][index] ?? null; },
    getItem(key: string) { return store.has(key) ? store.get(key)! : null; },
    setItem(key: string, value: string) { store.set(key, String(value)); },
    removeItem(key: string) { store.delete(key); },
    clear() { store.clear(); },
  };
  Object.defineProperty(globalThis, "localStorage", { value: impl, configurable: true, writable: true });
  Object.defineProperty(globalThis, "sessionStorage", { value: { ...impl, clear() { store.clear(); } }, configurable: true, writable: true });
}

// JSDOM does not implement Element.prototype.scrollIntoView. Components that
// scroll an active item into view (e.g. WorkspaceTabPills) call it during
// useEffect, which would otherwise crash every test that mounts them.
if (typeof Element !== "undefined" && typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = function () {};
}

// JSDOM does not implement Range.prototype.getClientRects/getBoundingClientRect.
// ProseMirror (the Tiptap editor powering Office Studio's Write view) calls
// these to scroll the selection into view after every transaction, which
// would otherwise throw and crash any test that types into the editor.
if (typeof Range !== "undefined" && typeof Range.prototype.getClientRects !== "function") {
  const emptyRect = (): DOMRect => ({
    x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0,
    toJSON() { return this; },
  });
  Range.prototype.getClientRects = () =>
    ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = emptyRect;
}

// JSDOM does not implement window.matchMedia. Hooks like useIsMobile call it
// in a useEffect, which would otherwise crash any test that mounts them.
// Default to "no match" (desktop). Individual tests can override per-suite.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// JSDOM does not implement HTMLCanvasElement.getContext (it logs a noisy "Not
// implemented" line and returns null). Components that touch a canvas (charts,
// game/wallpaper previews) would otherwise crash or spam the log. Return a
// minimal no-op 2D context so those tests render without a native canvas dep.
if (typeof HTMLCanvasElement !== "undefined") {
  const noop = () => {};
  const stub2d = () =>
    new Proxy(
      {
        canvas: null as unknown,
        measureText: () => ({ width: 0 }),
        getImageData: () => ({ data: new Uint8ClampedArray(0), width: 0, height: 0 }),
        createImageData: () => ({ data: new Uint8ClampedArray(0), width: 0, height: 0 }),
        createLinearGradient: () => ({ addColorStop: noop }),
        createRadialGradient: () => ({ addColorStop: noop }),
        createPattern: () => null,
      },
      // Any unknown drawing method (fillRect, arc, beginPath, ...) is a no-op.
      { get: (target, prop) => (prop in target ? (target as Record<string, unknown>)[prop as string] : noop) },
    );
  HTMLCanvasElement.prototype.getContext = function (type: string) {
    return type === "2d" ? (stub2d() as unknown as CanvasRenderingContext2D) : null;
  } as HTMLCanvasElement["getContext"];
  HTMLCanvasElement.prototype.toDataURL = () => "data:image/png;base64,";
}
