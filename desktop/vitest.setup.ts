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
