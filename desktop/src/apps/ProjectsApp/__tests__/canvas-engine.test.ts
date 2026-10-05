import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DEFAULT_ENGINE, getCanvasEngine } from "../canvas/canvas-engine";

describe("getCanvasEngine", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    localStorage.removeItem("taos.canvas.engine");
  });

  it("falls back to DEFAULT_ENGINE when the URL is absent", () => {
    Object.defineProperty(globalThis, "window", {
      value: {},
      writable: true,
      configurable: true,
    });
    expect(getCanvasEngine()).toBe(DEFAULT_ENGINE);
    expect(DEFAULT_ENGINE).toBe("tldraw");
  });

  it("honours ?canvas=excalidraw over everything else", () => {
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: new URL("http://localhost:5173/?canvas=excalidraw"),
      writable: true,
      configurable: true,
    });
    localStorage.setItem("taos.canvas.engine", "tldraw");
    expect(getCanvasEngine()).toBe("excalidraw");
    window.location = original;
  });

  it("honours ?canvas=tldraw over everything else", () => {
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: new URL("http://localhost:5173/?canvas=tldraw"),
      writable: true,
      configurable: true,
    });
    expect(getCanvasEngine()).toBe("tldraw");
    window.location = original;
  });

  it("falls back to localStorage when the URL query is absent", () => {
    localStorage.setItem("taos.canvas.engine", "excalidraw");
    expect(getCanvasEngine()).toBe("excalidraw");
    localStorage.removeItem("taos.canvas.engine");
  });

  it("URL wins over localStorage when both are set", () => {
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: new URL("http://localhost:5173/?canvas=tldraw"),
      writable: true,
      configurable: true,
    });
    localStorage.setItem("taos.canvas.engine", "excalidraw");
    expect(getCanvasEngine()).toBe("tldraw");
    window.location = original;
  });

  it("rejects an invalid URL value and falls back to DEFAULT_ENGINE", () => {
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: new URL("http://localhost:5173/?canvas=foo"),
      writable: true,
      configurable: true,
    });
    expect(getCanvasEngine()).toBe(DEFAULT_ENGINE);
    window.location = original;
  });

  it("rejects an invalid localStorage value and falls back to DEFAULT_ENGINE", () => {
    localStorage.setItem("taos.canvas.engine", "foo");
    expect(getCanvasEngine()).toBe(DEFAULT_ENGINE);
    localStorage.removeItem("taos.canvas.engine");
  });

  it("handles a try/catch-safe location even when parsing throws", () => {
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: { href: "h ttp://bad" },
      writable: true,
      configurable: true,
    });
    expect(getCanvasEngine()).toBe(DEFAULT_ENGINE);
    window.location = original;
  });

  it("handles a try/catch-safe localStorage even when access throws", () => {
    const define = Object.defineProperty;
    Object.defineProperty = (obj, key, desc) => {
      if (key === "localStorage") {
        throw new Error("localStorage unavailable");
      }
      return define(obj, key, desc);
    };
    try {
      expect(getCanvasEngine()).toBe(DEFAULT_ENGINE);
    } finally {
      Object.defineProperty = define;
    }
  });
});
