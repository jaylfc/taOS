import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { useDeepNavigation } from "./use-deep-navigation";

function withSearch(search: string) {
  // jsdom honours history.pushState; URLSearchParams reads window.location.search.
  window.history.pushState({}, "", "/desktop" + search);
}

afterEach(() => {
  window.history.pushState({}, "", "/desktop");
  vi.restoreAllMocks();
});

describe("useDeepNavigation", () => {
  it("opens the app named by ?app= on mount (resolving alias)", () => {
    const openWindow = vi.fn();
    withSearch("?app=activity");
    renderHook(() => useDeepNavigation(openWindow));
    expect(openWindow).toHaveBeenCalledTimes(1);
    expect(openWindow.mock.calls[0][0]).toBe("dashboard");
  });

  it("opens several comma-separated apps", () => {
    const openWindow = vi.fn();
    withSearch("?app=messages,settings");
    renderHook(() => useDeepNavigation(openWindow));
    const ids = openWindow.mock.calls.map((c) => c[0]);
    expect(ids).toEqual(["messages", "settings"]);
  });

  it("passes parsed appProps through to openWindow", () => {
    const openWindow = vi.fn();
    withSearch("?app=messages&appProps=" + encodeURIComponent('{"channel":"general"}'));
    renderHook(() => useDeepNavigation(openWindow));
    expect(openWindow).toHaveBeenCalledTimes(1);
    expect(openWindow.mock.calls[0][2]).toEqual({ channel: "general" });
  });

  it("opens the app without props when appProps is malformed", () => {
    const openWindow = vi.fn();
    withSearch("?app=messages&appProps=not-json");
    renderHook(() => useDeepNavigation(openWindow));
    expect(openWindow).toHaveBeenCalledTimes(1);
    expect(openWindow.mock.calls[0][2]).toBeUndefined();
  });

  it("does nothing for an unknown app token", () => {
    const openWindow = vi.fn();
    withSearch("?app=does-not-exist");
    renderHook(() => useDeepNavigation(openWindow));
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("opens an app from a taos:open-app event while mounted", () => {
    const openWindow = vi.fn();
    withSearch("");
    renderHook(() => useDeepNavigation(openWindow));
    expect(openWindow).not.toHaveBeenCalled();
    window.dispatchEvent(
      new CustomEvent("taos:open-app", { detail: { app: "settings", props: { tab: "about" } } }),
    );
    expect(openWindow).toHaveBeenCalledTimes(1);
    expect(openWindow.mock.calls[0][0]).toBe("settings");
    expect(openWindow.mock.calls[0][2]).toEqual({ tab: "about" });
  });

  it("removes the event listener on unmount", () => {
    const openWindow = vi.fn();
    withSearch("");
    const { unmount } = renderHook(() => useDeepNavigation(openWindow));
    unmount();
    window.dispatchEvent(new CustomEvent("taos:open-app", { detail: { app: "settings" } }));
    expect(openWindow).not.toHaveBeenCalled();
  });
});
