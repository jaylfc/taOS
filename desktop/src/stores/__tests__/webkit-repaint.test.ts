import { describe, it, expect, beforeEach, vi } from "vitest";
import { installWebkitRepaintGuards, forceCompositingRepaint } from "../theme-store";

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme-switching");
});

describe("forceCompositingRepaint", () => {
  it("toggles the data-theme-switching attribute to force a WebKit re-composite", () => {
    forceCompositingRepaint();
    // The attribute is set synchronously (filter:none for a frame) then cleared
    // on a later rAF/timer; we only assert the synchronous nudge happened.
    expect(document.documentElement.hasAttribute("data-theme-switching")).toBe(true);
  });
});

describe("installWebkitRepaintGuards", () => {
  it("repaints when the tab becomes visible again (switch back into taOS)", () => {
    installWebkitRepaintGuards();
    document.documentElement.removeAttribute("data-theme-switching");
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(document.documentElement.hasAttribute("data-theme-switching")).toBe(true);
  });

  it("repaints on pageshow (bfcache restore)", () => {
    installWebkitRepaintGuards();
    document.documentElement.removeAttribute("data-theme-switching");
    window.dispatchEvent(new Event("pageshow"));
    expect(document.documentElement.hasAttribute("data-theme-switching")).toBe(true);
  });

  it("is idempotent: a second install does not double-register listeners", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    installWebkitRepaintGuards();
    installWebkitRepaintGuards();
    const visCalls = addSpy.mock.calls.filter((c) => c[0] === "visibilitychange");
    expect(visCalls.length).toBe(0); // already installed by earlier tests in this file
    addSpy.mockRestore();
  });
});
