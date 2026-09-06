import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  useSnapZones,
  detectSnapZone,
  getSnapBounds,
} from "./use-snap-zones";
import type { SnapPosition } from "@/stores/process-store";

const VP = { width: 1200, height: 800, topBarH: 40, dockH: 60 };

describe("detectSnapZone", () => {
  it("returns null when cursor is in the middle of the viewport", () => {
    expect(detectSnapZone(600, 400, VP)).toBeNull();
  });

  it("returns 'left' when cursor is near the left edge", () => {
    expect(detectSnapZone(5, 400, VP)).toBe("left");
  });

  it("returns 'right' when cursor is near the right edge", () => {
    expect(detectSnapZone(1195, 400, VP)).toBe("right");
  });

  it("returns 'top-left' when cursor is near the top-left corner", () => {
    expect(detectSnapZone(5, 50, VP)).toBe("top-left");
  });

  it("returns 'top-right' when cursor is near the top-right corner", () => {
    expect(detectSnapZone(1195, 50, VP)).toBe("top-right");
  });

  it("returns 'bottom-left' when cursor is near the bottom-left corner", () => {
    expect(detectSnapZone(5, 730, VP)).toBe("bottom-left");
  });

  it("returns 'bottom-right' when cursor is near the bottom-right corner", () => {
    expect(detectSnapZone(1195, 730, VP)).toBe("bottom-right");
  });
});

describe("getSnapBounds", () => {
  it("returns null for a null snap position", () => {
    expect(getSnapBounds(null, VP)).toBeNull();
  });

  it("returns half-width left bounds for 'left'", () => {
    expect(getSnapBounds("left", VP)).toEqual({
      x: 0,
      y: 0,
      w: 600,
      h: 700,
    });
  });

  it("returns half-width right bounds for 'right'", () => {
    expect(getSnapBounds("right", VP)).toEqual({
      x: 600,
      y: 0,
      w: 600,
      h: 700,
    });
  });

  it("returns quarter bounds for 'top-left'", () => {
    expect(getSnapBounds("top-left", VP)).toEqual({
      x: 0,
      y: 0,
      w: 600,
      h: 350,
    });
  });

  it("returns quarter bounds for 'top-right'", () => {
    expect(getSnapBounds("top-right", VP)).toEqual({
      x: 600,
      y: 0,
      w: 600,
      h: 350,
    });
  });

  it("returns quarter bounds for 'bottom-left'", () => {
    expect(getSnapBounds("bottom-left", VP)).toEqual({
      x: 0,
      y: 350,
      w: 600,
      h: 350,
    });
  });

  it("returns quarter bounds for 'bottom-right'", () => {
    expect(getSnapBounds("bottom-right", VP)).toEqual({
      x: 600,
      y: 350,
      w: 600,
      h: 350,
    });
  });
});

describe("useSnapZones", () => {
  it("starts with null preview and null previewBounds", () => {
    const { result } = renderHook(() => useSnapZones(VP));

    expect(result.current.preview).toBeNull();
    expect(result.current.previewBounds).toBeNull();
  });

  it("updates preview and previewBounds when onDrag detects a zone", () => {
    const { result } = renderHook(() => useSnapZones(VP));

    act(() => {
      result.current.onDrag(5, 400);
    });

    expect(result.current.preview).toBe("left");
    expect(result.current.previewBounds).toEqual({
      x: 0,
      y: 0,
      w: 600,
      h: 700,
    });
  });

  it("updates preview to a different zone when onDrag moves to a new zone", () => {
    const { result } = renderHook(() => useSnapZones(VP));

    act(() => {
      result.current.onDrag(5, 400);
    });
    expect(result.current.preview).toBe("left");

    act(() => {
      result.current.onDrag(1195, 400);
    });
    expect(result.current.preview).toBe("right");
    expect(result.current.previewBounds).toEqual({
      x: 600,
      y: 0,
      w: 600,
      h: 700,
    });
  });

  it("sets preview back to null when onDrag moves out of any zone", () => {
    const { result } = renderHook(() => useSnapZones(VP));

    act(() => {
      result.current.onDrag(5, 400);
    });
    expect(result.current.preview).toBe("left");

    act(() => {
      result.current.onDrag(600, 400);
    });
    expect(result.current.preview).toBeNull();
    expect(result.current.previewBounds).toBeNull();
  });

  it("onDragStop returns the current zone and resets preview to null", () => {
    const { result } = renderHook(() => useSnapZones(VP));

    act(() => {
      result.current.onDrag(5, 400);
    });
    expect(result.current.preview).toBe("left");

    let stoppedZone: SnapPosition;
    act(() => {
      stoppedZone = result.current.onDragStop();
    });

    expect(stoppedZone!).toBe("left");
    expect(result.current.preview).toBeNull();
    expect(result.current.previewBounds).toBeNull();
  });

  it("onDragStop returns null when no zone is active", () => {
    const { result } = renderHook(() => useSnapZones(VP));

    let stoppedZone: SnapPosition;
    act(() => {
      stoppedZone = result.current.onDragStop();
    });

    expect(stoppedZone!).toBeNull();
    expect(result.current.preview).toBeNull();
  });
});
