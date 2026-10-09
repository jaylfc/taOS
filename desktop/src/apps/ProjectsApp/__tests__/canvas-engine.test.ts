import { getCanvasEngine } from "../canvas/canvas-engine";
import { describe, test, expect, beforeEach } from "vitest";

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string): string | null => (store[key] ?? null),
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
});

describe("getCanvasEngine", () => {
  beforeEach(() => {
    // Reset mocks before each test
    localStorageMock.clear();
    // Clear URL search params
    delete window.history.state;
    window.history.replaceState({}, "", "/");
  });

  test("defaults to tldraw", () => {
    expect(getCanvasEngine()).toBe("tldraw");
  });

  test("query param wins over localStorage", () => {
    // Set localStorage to excalidraw
    localStorageMock.setItem("taos.canvas.engine", "excalidraw");
    // Set URL param to tldraw
    window.history.replaceState({}, "", "?canvas=tldraw");
    expect(getCanvasEngine()).toBe("tldraw");
  });

  test("localStorage selects excalidraw", () => {
    localStorageMock.setItem("taos.canvas.engine", "excalidraw");
    expect(getCanvasEngine()).toBe("excalidraw");
  });

  test("unknown values fall back to the default", () => {
    // Test unknown URL param
    window.history.replaceState({}, "", "?canvas=unknown");
    expect(getCanvasEngine()).toBe("tldraw");
    
    // Test unknown localStorage value
    localStorageMock.setItem("taos.canvas.engine", "unknown");
    expect(getCanvasEngine()).toBe("tldraw");
  });

  test("a throwing localStorage falls back to the default", () => {
    // Override localStorage to throw an error
    const originalGetItem = localStorageMock.getItem;
    localStorageMock.getItem = () => {
      throw new Error("Storage error");
    };
    
    expect(getCanvasEngine()).toBe("tldraw");
    
    // Restore
    localStorageMock.getItem = originalGetItem;
  });
});
