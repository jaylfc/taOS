import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { registerServiceWorker } from "../sw-register";

describe("registerServiceWorker", () => {
  let originalSW: any;
  beforeEach(() => {
    originalSW = (navigator as any).serviceWorker;
  });
  afterEach(() => {
    Object.defineProperty(navigator, "serviceWorker", {
      value: originalSW, writable: true, configurable: true,
    });
    vi.restoreAllMocks();
  });

  it("does nothing if serviceWorker is unavailable", async () => {
    Object.defineProperty(navigator, "serviceWorker", {
      value: undefined, writable: true, configurable: true,
    });
    await expect(registerServiceWorker()).resolves.toBeUndefined();
  });

  it("calls navigator.serviceWorker.register('/sw.js')", async () => {
    const register = vi.fn().mockResolvedValue({ scope: "/" });
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register }, writable: true, configurable: true,
    });
    await registerServiceWorker();
    expect(register).toHaveBeenCalledWith("/sw.js");
  });

  it("swallows registration errors (logs only)", async () => {
    const consoleErr = vi.spyOn(console, "warn").mockImplementation(() => {});
    const register = vi.fn().mockRejectedValue(new Error("nope"));
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register }, writable: true, configurable: true,
    });
    await expect(registerServiceWorker()).resolves.toBeUndefined();
    expect(consoleErr).toHaveBeenCalled();
  });

  it("proactively calls registration.update() to check for a new SW", async () => {
    const update = vi.fn().mockResolvedValue(undefined);
    const register = vi.fn().mockResolvedValue({ update });
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register, controller: null }, writable: true, configurable: true,
    });
    await registerServiceWorker();
    expect(update).toHaveBeenCalled();
  });

  it("reloads once when a new SW takes control on a returning session", async () => {
    let handler: (() => void) | null = null;
    const addEventListener = vi.fn((type: string, h: () => void) => {
      if (type === "controllerchange") handler = h;
    });
    const register = vi.fn().mockResolvedValue({ update: vi.fn() });
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register, addEventListener, controller: {} }, // controller present = returning session
      writable: true, configurable: true,
    });
    const originalLocation = window.location;
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      value: { reload }, writable: true, configurable: true,
    });

    await registerServiceWorker();
    expect(addEventListener).toHaveBeenCalledWith("controllerchange", expect.any(Function));
    handler?.();
    handler?.(); // second fire must not double-reload
    expect(reload).toHaveBeenCalledTimes(1);

    Object.defineProperty(window, "location", {
      value: originalLocation, writable: true, configurable: true,
    });
  });

  it("does not wire the reload on a first-ever visit (no controller)", async () => {
    const addEventListener = vi.fn();
    const register = vi.fn().mockResolvedValue({ update: vi.fn() });
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register, addEventListener, controller: null },
      writable: true, configurable: true,
    });
    await registerServiceWorker();
    expect(addEventListener).not.toHaveBeenCalled();
  });

  it("wires the controllerchange listener at most once across calls", async () => {
    const addEventListener = vi.fn();
    const register = vi.fn().mockResolvedValue({ update: vi.fn() });
    // Same container instance across both calls (StrictMode / remount).
    const swMock = { register, addEventListener, controller: {} };
    Object.defineProperty(navigator, "serviceWorker", {
      value: swMock, writable: true, configurable: true,
    });
    await registerServiceWorker();
    await registerServiceWorker();
    const controllerChangeCalls = addEventListener.mock.calls.filter(
      ([type]: [string]) => type === "controllerchange",
    );
    expect(controllerChangeCalls).toHaveLength(1);
  });
});
