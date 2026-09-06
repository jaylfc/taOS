import { describe, it, expect } from "vitest";
import { detectSnapZone, getSnapBounds } from "../src/hooks/use-snap-zones";

const viewport = { width: 1920, height: 1080, topBarH: 32, dockH: 64 };

describe("detectSnapZone", () => {
  it("returns 'left' when dragged to left edge", () => {
    expect(detectSnapZone(5, 400, viewport)).toBe("left");
  });

  it("returns 'right' when dragged to right edge", () => {
    expect(detectSnapZone(1915, 400, viewport)).toBe("right");
  });

  it("returns 'top-left' when dragged to top-left corner", () => {
    expect(detectSnapZone(5, 35, viewport)).toBe("top-left");
  });

  it("returns 'top-right' when dragged to top-right corner", () => {
    expect(detectSnapZone(1915, 35, viewport)).toBe("top-right");
  });

  it("returns 'bottom-left' when dragged to bottom-left corner", () => {
    expect(detectSnapZone(5, 1010, viewport)).toBe("bottom-left");
  });

  it("returns 'bottom-right' when dragged to bottom-right corner", () => {
    expect(detectSnapZone(1915, 1010, viewport)).toBe("bottom-right");
  });

  it("returns null when in the middle of the screen", () => {
    expect(detectSnapZone(960, 540, viewport)).toBeNull();
  });
});

describe("getSnapBounds", () => {
  it("returns left half for 'left' snap", () => {
    const bounds = getSnapBounds("left", viewport);
    expect(bounds).toEqual({ x: 0, y: 32, w: 960, h: 984 });
  });

  it("returns right half for 'right' snap", () => {
    const bounds = getSnapBounds("right", viewport);
    expect(bounds).toEqual({ x: 960, y: 32, w: 960, h: 984 });
  });

  it("returns top-left quarter for 'top-left' snap", () => {
    const bounds = getSnapBounds("top-left", viewport);
    expect(bounds).toEqual({ x: 0, y: 32, w: 960, h: 492 });
  });

  it("returns null for null snap", () => {
    expect(getSnapBounds(null, viewport)).toBeNull();
  });
});
