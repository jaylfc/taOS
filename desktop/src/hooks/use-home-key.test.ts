import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useHomeKey, HOME_KEY } from "./use-home-key";

const press = (key: string) => window.dispatchEvent(new KeyboardEvent("keydown", { key }));

describe("useHomeKey", () => {
  it("calls onHome for the home key and ignores other keys", () => {
    const onHome = vi.fn();
    renderHook(() => useHomeKey(true, onHome));
    press("F12");
    press("Home");
    expect(onHome).not.toHaveBeenCalled();
    press(HOME_KEY);
    expect(onHome).toHaveBeenCalledTimes(1);
  });

  it("does nothing while disabled (desktop)", () => {
    const onHome = vi.fn();
    renderHook(() => useHomeKey(false, onHome));
    press(HOME_KEY);
    expect(onHome).not.toHaveBeenCalled();
  });

  it("stops listening on unmount", () => {
    const onHome = vi.fn();
    const { unmount } = renderHook(() => useHomeKey(true, onHome));
    unmount();
    press(HOME_KEY);
    expect(onHome).not.toHaveBeenCalled();
  });
});
