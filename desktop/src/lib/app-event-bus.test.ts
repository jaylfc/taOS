import { describe, it, expect, vi } from "vitest";
import { emitAppEvent, onAppEvent, APP_INSTALLED, APP_OPTIONAL_CHANGED } from "./app-event-bus";

describe("APP_INSTALLED", () => {
  it("is the expected string constant", () => {
    expect(APP_INSTALLED).toBe("app.installed");
  });
});

describe("APP_OPTIONAL_CHANGED", () => {
  it("is the expected string constant", () => {
    expect(APP_OPTIONAL_CHANGED).toBe("app.optional.changed");
  });
});

describe("emitAppEvent", () => {
  it("dispatches an event with the given name and detail", () => {
    const fn = vi.fn();
    const unsub = onAppEvent("custom.event", fn);
    emitAppEvent("custom.event", "payload-1");
    expect(fn).toHaveBeenCalledWith("payload-1");
    unsub();
  });

  it("dispatches an event with null detail when detail is omitted", () => {
    const fn = vi.fn();
    const unsub = onAppEvent("no-detail", fn);
    emitAppEvent("no-detail");
    expect(fn).toHaveBeenCalledWith(null);
    unsub();
  });

  it("dispatches an event with empty string detail", () => {
    const fn = vi.fn();
    const unsub = onAppEvent("empty-detail", fn);
    emitAppEvent("empty-detail", "");
    expect(fn).toHaveBeenCalledWith("");
    unsub();
  });
});

describe("onAppEvent", () => {
  it("returns an unsubscribe function that stops future calls", () => {
    const fn = vi.fn();
    const unsub = onAppEvent("toggle.event", fn);
    emitAppEvent("toggle.event", "first");
    expect(fn).toHaveBeenCalledTimes(1);
    unsub();
    emitAppEvent("toggle.event", "second");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("supports multiple subscribers on the same event", () => {
    const fn1 = vi.fn();
    const fn2 = vi.fn();
    const unsub1 = onAppEvent("multi", fn1);
    const unsub2 = onAppEvent("multi", fn2);
    emitAppEvent("multi", "data");
    expect(fn1).toHaveBeenCalledWith("data");
    expect(fn2).toHaveBeenCalledWith("data");
    unsub1();
    unsub2();
  });

  it("does not call listener for a different event name", () => {
    const fn = vi.fn();
    const unsub = onAppEvent("wanted", fn);
    emitAppEvent("unwanted", "data");
    expect(fn).not.toHaveBeenCalled();
    unsub();
  });

  it("passes detail through for the APP_INSTALLED constant", () => {
    const fn = vi.fn();
    const unsub = onAppEvent(APP_INSTALLED, fn);
    emitAppEvent(APP_INSTALLED, "app-42");
    expect(fn).toHaveBeenCalledWith("app-42");
    unsub();
  });

  it("passes detail through for the APP_OPTIONAL_CHANGED constant", () => {
    const fn = vi.fn();
    const unsub = onAppEvent(APP_OPTIONAL_CHANGED, fn);
    emitAppEvent(APP_OPTIONAL_CHANGED, "optional-app-7");
    expect(fn).toHaveBeenCalledWith("optional-app-7");
    unsub();
  });
});
