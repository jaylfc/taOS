import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useTextEntryActive } from "./use-text-entry-active";

function mount(html: string): HTMLElement {
  document.body.innerHTML = html;
  return document.body.firstElementChild as HTMLElement;
}

function focusAndSettle(el: HTMLElement) {
  act(() => {
    el.focus();
    vi.advanceTimersByTime(150);
  });
}

describe("useTextEntryActive", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it.each([
    ["text", '<input type="text" />'],
    ["email", '<input type="email" />'],
    ["password", '<input type="password" />'],
    ["search", '<input type="search" />'],
    ["textarea", "<textarea></textarea>"],
    ["contenteditable", '<div contenteditable="true" tabindex="0"></div>'],
  ])("is true when a %s field is focused", (_n, html) => {
    const el = mount(html);
    const { result } = renderHook(() => useTextEntryActive());
    expect(result.current).toBe(false);
    focusAndSettle(el);
    expect(result.current).toBe(true);
  });

  it.each([
    ["checkbox", '<input type="checkbox" />'],
    ["button", "<button>x</button>"],
    ["button input", '<input type="button" />'],
    ["inputmode none", '<input type="text" inputmode="none" />'],
    ["readOnly", '<input type="text" readonly />'],
    ["disabled textarea", "<textarea disabled></textarea>"],
  ])("is false for %s", (_n, html) => {
    const el = mount(html);
    const { result } = renderHook(() => useTextEntryActive());
    focusAndSettle(el);
    expect(result.current).toBe(false);
  });

  it("goes false after blur", () => {
    const el = mount('<input type="text" />');
    const { result } = renderHook(() => useTextEntryActive());
    focusAndSettle(el);
    expect(result.current).toBe(true);
    act(() => {
      el.blur();
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toBe(false);
  });

  it("stays true while focus moves from field to field", () => {
    mount('<input id="a" type="text" /><input id="b" type="text" />');
    const a = document.getElementById("a")!;
    const b = document.getElementById("b")!;
    const { result } = renderHook(() => useTextEntryActive());
    focusAndSettle(a);
    expect(result.current).toBe(true);
    act(() => {
      a.blur();
      vi.advanceTimersByTime(30);
    });
    expect(result.current).toBe(true);
    act(() => {
      b.focus();
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toBe(true);
  });

  it("is always false when disabled", () => {
    const el = mount('<input type="text" />');
    const { result } = renderHook(() => useTextEntryActive(false));
    focusAndSettle(el);
    expect(result.current).toBe(false);
  });
});
