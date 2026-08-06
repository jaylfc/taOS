import { describe, it, expect, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDeviceMode } from "./use-device-mode";
import { applyScale, DEFAULT_SCALE } from "@/stores/display-store";

// LOCKS A DELIBERATE DECISION (2026-08-06).
//
// Display scale is an APPEARANCE preference. Device mode is a FORM-FACTOR fact.
// Scaling the UI down to fit more on a laptop must never flip the shell into
// its phone layout, and scaling up on a phone must never make it claim to be a
// desktop.
//
// This is a live hazard rather than a hypothetical: CSS media queries evaluate
// against the zoomed viewport while `use-device-mode` reads `window.innerWidth`
// in JS, so the two can disagree. The resolution is that device mode stays on
// the TRUE viewport. If someone later "fixes" the inconsistency by pointing
// device mode at the effective viewport, these fail.

function setViewportWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true, writable: true });
}

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  applyScale(DEFAULT_SCALE);
});

describe("device mode is independent of display scale", () => {
  it("stays desktop on a laptop viewport scaled down to fit more", () => {
    setViewportWidth(1440);
    applyScale(0.8);
    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("desktop");
  });

  it("stays mobile on a phone viewport scaled up for larger text", () => {
    setViewportWidth(390);
    applyScale(1.25);
    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("mobile");
  });

  it("gives the same mode at every offered scale for one viewport", () => {
    setViewportWidth(1440);
    const modes = [0.8, 0.9, 1.0, 1.1, 1.25].map((scale) => {
      applyScale(scale);
      return renderHook(() => useDeviceMode()).result.current;
    });
    expect(new Set(modes).size).toBe(1);
  });
});
