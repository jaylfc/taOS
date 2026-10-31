import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useSwitcherKey, SWITCHER_KEY } from "./use-switcher-key";

const press = (key: string) => window.dispatchEvent(new KeyboardEvent("keydown", { key }));

describe("useSwitcherKey", () => {
  it("calls onSwitcher for the switcher key and ignores other keys", () => {
    const onSwitcher = vi.fn();
    renderHook(() => useSwitcherKey(true, onSwitcher));
    press("F13");
    press("F12");
    expect(onSwitcher).not.toHaveBeenCalled();
    press(SWITCHER_KEY);
    expect(SWITCHER_KEY).toBe("F14");
    expect(onSwitcher).toHaveBeenCalledTimes(1);
  });

  it("does nothing while disabled (desktop)", () => {
    const onSwitcher = vi.fn();
    renderHook(() => useSwitcherKey(false, onSwitcher));
    press(SWITCHER_KEY);
    expect(onSwitcher).not.toHaveBeenCalled();
  });

  it("stops listening on unmount", () => {
    const onSwitcher = vi.fn();
    const { unmount } = renderHook(() => useSwitcherKey(true, onSwitcher));
    unmount();
    press(SWITCHER_KEY);
    expect(onSwitcher).not.toHaveBeenCalled();
  });
});
