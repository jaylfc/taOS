import { describe, it, expect, beforeEach, vi } from "vitest";
import { getWidgetSize, setWidgetSize } from "./mobile-widget-size";

const KEY = "taos-mobile-widget-size";

describe("mobile-widget-size", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to half with empty storage", () => {
    expect(getWidgetSize("weather")).toBe("half");
  });

  it("remembers a per-type size without touching other types", () => {
    setWidgetSize("weather", "full");
    expect(getWidgetSize("weather")).toBe("full");
    expect(getWidgetSize("clock")).toBe("half");
  });

  it("treats corrupt JSON as half", () => {
    localStorage.setItem(KEY, "{not json");
    expect(getWidgetSize("weather")).toBe("half");
  });

  it("treats anything other than exactly full as half", () => {
    localStorage.setItem(KEY, JSON.stringify({ weather: "FULL" }));
    expect(getWidgetSize("weather")).toBe("half");
  });

  it("dispatches the change event on set", () => {
    const spy = vi.fn();
    window.addEventListener("taos-mobile-widget-size", spy);
    setWidgetSize("clock", "full");
    window.removeEventListener("taos-mobile-widget-size", spy);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
