import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isIOS, isStandalone } from "./platform";

describe("isIOS", () => {
  const originalNavigator = navigator;

  beforeEach(() => {
    vi.stubGlobal("navigator", { ...originalNavigator });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns true for iPhone user agent", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "iPhone", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 5, configurable: true });
    expect(isIOS()).toBe(true);
  });

  it("returns true for iPad user agent", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "iPad", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 5, configurable: true });
    expect(isIOS()).toBe(true);
  });

  it("returns true for iPod user agent", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (iPod touch; CPU iPhone OS 16_0 like Mac OS X)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "iPod", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 5, configurable: true });
    expect(isIOS()).toBe(true);
  });

  it("returns true for MacIntel with multiple touch points (iPadOS)", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "MacIntel", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 5, configurable: true });
    expect(isIOS()).toBe(true);
  });

  it("returns false for MacIntel with single touch point", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "MacIntel", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 1, configurable: true });
    expect(isIOS()).toBe(false);
  });

  it("returns false for MacIntel with zero touch points", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "MacIntel", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 0, configurable: true });
    expect(isIOS()).toBe(false);
  });

  it("returns false for Windows user agent", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "Win32", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 0, configurable: true });
    expect(isIOS()).toBe(false);
  });

  it("returns false for empty user agent", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "",
      configurable: true,
    });
    Object.defineProperty(navigator, "platform", { value: "", configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: 0, configurable: true });
    expect(isIOS()).toBe(false);
  });
});

describe("isStandalone", () => {
  const originalNavigator = window.navigator;
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    vi.stubGlobal("navigator", { ...originalNavigator });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.matchMedia = originalMatchMedia;
  });

  it("returns true when navigator.standalone is true", () => {
    Object.defineProperty(window, "navigator", {
      value: { standalone: true },
      configurable: true,
    });
    window.matchMedia = vi.fn().mockReturnValue({ matches: false });
    expect(isStandalone()).toBe(true);
  });

  it("returns true when display-mode standalone matches", () => {
    Object.defineProperty(window, "navigator", {
      value: { standalone: false },
      configurable: true,
    });
    window.matchMedia = vi.fn().mockReturnValue({ matches: true });
    expect(isStandalone()).toBe(true);
  });

  it("returns false when neither standalone nor display-mode matches", () => {
    Object.defineProperty(window, "navigator", {
      value: { standalone: false },
      configurable: true,
    });
    window.matchMedia = vi.fn().mockReturnValue({ matches: false });
    expect(isStandalone()).toBe(false);
  });

  it("returns false when navigator has no standalone property", () => {
    Object.defineProperty(window, "navigator", {
      value: {},
      configurable: true,
    });
    window.matchMedia = vi.fn().mockReturnValue({ matches: false });
    expect(isStandalone()).toBe(false);
  });

  it("returns true when both standalone and display-mode are true", () => {
    Object.defineProperty(window, "navigator", {
      value: { standalone: true },
      configurable: true,
    });
    window.matchMedia = vi.fn().mockReturnValue({ matches: true });
    expect(isStandalone()).toBe(true);
  });
});
