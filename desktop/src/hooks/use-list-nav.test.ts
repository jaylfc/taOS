import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { computeNextIndex, useListNav } from "./use-list-nav";

describe("computeNextIndex", () => {
  it("returns -1 when total is 0", () => {
    expect(computeNextIndex(0, 0, "ArrowDown")).toBe(-1);
    expect(computeNextIndex(5, 0, "ArrowUp")).toBe(-1);
    expect(computeNextIndex(0, 0, "Home")).toBe(-1);
  });

  it("moves down with ArrowDown", () => {
    expect(computeNextIndex(0, 3, "ArrowDown")).toBe(1);
    expect(computeNextIndex(1, 3, "ArrowDown")).toBe(2);
  });

  it("wraps from last to first with ArrowDown", () => {
    expect(computeNextIndex(2, 3, "ArrowDown")).toBe(0);
  });

  it("moves up with ArrowUp", () => {
    expect(computeNextIndex(2, 3, "ArrowUp")).toBe(1);
    expect(computeNextIndex(1, 3, "ArrowUp")).toBe(0);
  });

  it("wraps from first to last with ArrowUp", () => {
    expect(computeNextIndex(0, 3, "ArrowUp")).toBe(2);
  });

  it("jumps to first with Home", () => {
    expect(computeNextIndex(2, 5, "Home")).toBe(0);
    expect(computeNextIndex(0, 1, "Home")).toBe(0);
  });

  it("jumps to last with End", () => {
    expect(computeNextIndex(0, 3, "End")).toBe(2);
    expect(computeNextIndex(1, 5, "End")).toBe(4);
  });

  it("returns current for unhandled keys", () => {
    expect(computeNextIndex(2, 5, "Tab")).toBe(2);
    expect(computeNextIndex(2, 5, "a")).toBe(2);
    expect(computeNextIndex(2, 5, "Escape")).toBe(2);
  });

  it("handles single-item list", () => {
    expect(computeNextIndex(0, 1, "ArrowDown")).toBe(0);
    expect(computeNextIndex(0, 1, "ArrowUp")).toBe(0);
    expect(computeNextIndex(0, 1, "Home")).toBe(0);
    expect(computeNextIndex(0, 1, "End")).toBe(0);
  });
});

describe("useListNav", () => {
  it("starts with selectedIndex at 0", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    expect(result.current.selectedIndex).toBe(0);
  });

  it("exposes setSelectedIndex", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => { result.current.setSelectedIndex(2); });
    expect(result.current.selectedIndex).toBe(2);
  });

  it("ArrowDown in onKeyDown increments selectedIndex", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => {
      result.current.onKeyDown({ key: "ArrowDown", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(result.current.selectedIndex).toBe(1);
  });

  it("ArrowUp in onKeyDown decrements selectedIndex with wrap", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => {
      result.current.onKeyDown({ key: "ArrowUp", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(result.current.selectedIndex).toBe(2);
  });

  it("Home in onKeyDown sets selectedIndex to 0", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => { result.current.setSelectedIndex(2); });
    act(() => {
      result.current.onKeyDown({ key: "Home", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(result.current.selectedIndex).toBe(0);
  });

  it("End in onKeyDown sets selectedIndex to last", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => {
      result.current.onKeyDown({ key: "End", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(result.current.selectedIndex).toBe(2);
  });

  it("Enter in onKeyDown calls onSelect with the selected item", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => { result.current.setSelectedIndex(1); });
    act(() => {
      result.current.onKeyDown({ key: "Enter", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("Space in onKeyDown calls onSelect with the selected item", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => {
      result.current.onKeyDown({ key: " ", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("unhandled key does not call onSelect", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => {
      result.current.onKeyDown({ key: "Tab", preventDefault: vi.fn() } as unknown as React.KeyboardEvent);
    });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("navigation keys call preventDefault", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const preventDefault = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));
    act(() => {
      result.current.onKeyDown({ key: "ArrowDown", preventDefault } as unknown as React.KeyboardEvent);
    });
    expect(preventDefault).toHaveBeenCalled();
  });

  it("Enter and Space call preventDefault", () => {
    const items = ["a", "b", "c"];
    const onSelect = vi.fn();
    const { result } = renderHook(() => useListNav(items, onSelect));

    const preventDefaultEnter = vi.fn();
    act(() => {
      result.current.onKeyDown({ key: "Enter", preventDefault: preventDefaultEnter } as unknown as React.KeyboardEvent);
    });
    expect(preventDefaultEnter).toHaveBeenCalled();

    const preventDefaultSpace = vi.fn();
    act(() => {
      result.current.onKeyDown({ key: " ", preventDefault: preventDefaultSpace } as unknown as React.KeyboardEvent);
    });
    expect(preventDefaultSpace).toHaveBeenCalled();
  });
});
