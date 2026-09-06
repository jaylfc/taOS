import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import React from "react";

interface ResizeEntry { contentRect: { width: number; height: number } }
type ResizeCallback = (entries: ResizeEntry[]) => void;

function createMockElement(width: number, height: number) {
  return {
    offsetWidth: width,
    offsetHeight: height,
  } as unknown as HTMLDivElement;
}

const { mockRef } = vi.hoisted(() => {
  const mockRef: React.MutableRefObject<HTMLDivElement | null> = { current: null };
  return { mockRef };
});

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useRef: () => mockRef,
  };
});

import { useWidgetSize } from "./use-widget-size";

describe("useWidgetSize", () => {
  const OriginalResizeObserver = globalThis.ResizeObserver;
  let observerCallback: ResizeCallback | null = null;

  beforeEach(() => {
    observerCallback = null;
    globalThis.ResizeObserver = class {
      constructor(cb: ResizeCallback) { observerCallback = cb; }
      observe = vi.fn();
      disconnect = vi.fn();
      unobserve = vi.fn();
    } as unknown as new (cb: ResizeCallback) => { observe: typeof vi.fn; disconnect: typeof vi.fn; unobserve: typeof vi.fn };
    mockRef.current = createMockElement(300, 200);
  });

  afterEach(() => {
    globalThis.ResizeObserver = OriginalResizeObserver;
    mockRef.current = null;
  });

  it("returns the initial size and tier from the measured element", () => {
    const { result } = renderHook(() => useWidgetSize());

    expect(result.current[1].width).toBe(300);
    expect(result.current[1].height).toBe(200);
    expect(result.current[1].tier).toBe("m");
  });

  it("updates size when ResizeObserver fires", () => {
    const { result } = renderHook(() => useWidgetSize());

    expect(result.current[1].width).toBe(300);

    act(() => {
      observerCallback!([{ contentRect: { width: 500, height: 400 } }]);
    });

    expect(result.current[1].width).toBe(500);
    expect(result.current[1].height).toBe(400);
    expect(result.current[1].tier).toBe("l");
  });
});
