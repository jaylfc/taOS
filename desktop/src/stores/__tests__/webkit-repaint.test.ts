import { describe, it, expect, beforeEach } from "vitest";
import {
  installWebkitRepaintGuards,
  forceCompositingRepaint,
  isWebKit,
} from "../theme-store";

const ATTR = "data-theme-switching";
const clear = () => document.documentElement.removeAttribute(ATTR);
const painted = () => document.documentElement.hasAttribute(ATTR);
const setUA = (s: string) =>
  Object.defineProperty(navigator, "userAgent", { value: s, configurable: true });
const SAFARI_UA =
  "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15";

beforeEach(() => {
  clear();
  // Guards are WebKit-scoped; default to a Safari UA so install tests run the
  // real path. The isWebKit test overrides this within its own body.
  setUA(SAFARI_UA);
});

describe("forceCompositingRepaint", () => {
  it("synchronously toggles data-theme-switching to force a WebKit re-composite", () => {
    forceCompositingRepaint();
    expect(painted()).toBe(true);
  });
});

describe("isWebKit", () => {
  it("true for Safari, false for Chrome/Chromium/Edge", () => {
    setUA(SAFARI_UA);
    expect(isWebKit()).toBe(true);
    setUA("Mozilla/5.0 (X11; Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36");
    expect(isWebKit()).toBe(false);
    setUA("Mozilla/5.0 (Windows) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 Edg/124");
    expect(isWebKit()).toBe(false);
  });
});

describe("installWebkitRepaintGuards (jsdom UA is WebKit-like)", () => {
  it("repaints when the tab becomes visible again (switch back into taOS)", () => {
    installWebkitRepaintGuards();
    clear();
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(painted()).toBe(true);
  });

  it("repaints on bfcache restore (pageshow persisted) but not on a normal first load", () => {
    installWebkitRepaintGuards();
    clear();
    window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: false }));
    expect(painted()).toBe(false); // normal load: no needless repaint
    window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));
    expect(painted()).toBe(true); // bfcache restore: repaint
  });

  it("is idempotent: installing again registers no new listeners (order-independent)", () => {
    installWebkitRepaintGuards(); // ensure installed regardless of test order
    const calls: string[] = [];
    const orig = document.addEventListener.bind(document);
    document.addEventListener = ((t: string, ...a: unknown[]) => {
      calls.push(t);
      // @ts-expect-error pass-through to the real signature
      return orig(t, ...a);
    }) as typeof document.addEventListener;
    installWebkitRepaintGuards();
    document.addEventListener = orig;
    expect(calls).not.toContain("visibilitychange");
  });
});
