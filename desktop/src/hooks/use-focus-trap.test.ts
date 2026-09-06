import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { getFocusableElements, useFocusTrap } from "./use-focus-trap";

function createContainer() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  return container;
}

function createFocusableButton(label: string): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.textContent = label;
  return btn;
}

function createFocusableLink(href: string, text: string): HTMLAnchorElement {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = text;
  return a;
}

function createFocusableInput(): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "text";
  return input;
}

describe("getFocusableElements", () => {
  it("returns an empty array when container is null", () => {
    expect(getFocusableElements(null)).toEqual([]);
  });

  it("returns focusable elements inside a container", () => {
    const container = createContainer();
    const btn1 = createFocusableButton("One");
    const btn2 = createFocusableButton("Two");
    container.appendChild(btn1);
    container.appendChild(btn2);

    const result = getFocusableElements(container);
    expect(result).toHaveLength(2);
    expect(result[0]).toBe(btn1);
    expect(result[1]).toBe(btn2);

    document.body.removeChild(container);
  });

  it("includes links, buttons, inputs, selects, textareas, and tabindex elements", () => {
    const container = createContainer();
    const link = createFocusableLink("#", "link");
    const btn = createFocusableButton("btn");
    const input = createFocusableInput();
    const select = document.createElement("select");
    const textarea = document.createElement("textarea");
    const div = document.createElement("div");
    div.setAttribute("tabindex", "0");

    container.appendChild(link);
    container.appendChild(btn);
    container.appendChild(input);
    container.appendChild(select);
    container.appendChild(textarea);
    container.appendChild(div);

    const result = getFocusableElements(container);
    expect(result).toHaveLength(6);

    document.body.removeChild(container);
  });

  it("excludes disabled elements", () => {
    const container = createContainer();
    const btn = createFocusableButton("enabled");
    const disabledBtn = createFocusableButton("disabled");
    disabledBtn.disabled = true;
    const input = createFocusableInput();
    const disabledInput = createFocusableInput();
    disabledInput.disabled = true;

    container.appendChild(btn);
    container.appendChild(disabledBtn);
    container.appendChild(input);
    container.appendChild(disabledInput);

    const result = getFocusableElements(container);
    expect(result).toHaveLength(2);
    expect(result[0]).toBe(btn);
    expect(result[1]).toBe(input);

    document.body.removeChild(container);
  });

  it("excludes elements with tabindex=-1", () => {
    const container = createContainer();
    const btn = createFocusableButton("ok");
    const neg = document.createElement("div");
    neg.setAttribute("tabindex", "-1");

    container.appendChild(btn);
    container.appendChild(neg);

    const result = getFocusableElements(container);
    expect(result).toHaveLength(1);
    expect(result[0]).toBe(btn);

    document.body.removeChild(container);
  });
});

describe("useFocusTrap", () => {
  let originalActiveElement: typeof document.activeElement;

  beforeEach(() => {
    originalActiveElement = document.activeElement;
  });

  afterEach(() => {
    // restore focus
    if (originalActiveElement instanceof HTMLElement) {
      originalActiveElement.focus();
    }
  });

  it("focuses the first focusable element when active becomes true", () => {
    const container = createContainer();
    const btn1 = createFocusableButton("First");
    const btn2 = createFocusableButton("Second");
    container.appendChild(btn1);
    container.appendChild(btn2);

    const ref = { current: container };

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    expect(document.activeElement).toBe(btn1);

    document.body.removeChild(container);
  });

  it("does not focus anything when active is false", () => {
    const container = createContainer();
    const btn = createFocusableButton("btn");
    container.appendChild(btn);

    const ref = { current: container };
    const prev = document.activeElement;

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: false } },
    );

    expect(document.activeElement).toBe(prev);

    document.body.removeChild(container);
  });

  it("does not focus when ref.current is null", () => {
    const ref = { current: null };
    const prev = document.activeElement;

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    expect(document.activeElement).toBe(prev);
  });

  it("traps focus: Tab on last element wraps to first", () => {
    const container = createContainer();
    const btn1 = createFocusableButton("First");
    const btn2 = createFocusableButton("Second");
    const btn3 = createFocusableButton("Third");
    container.appendChild(btn1);
    container.appendChild(btn2);
    container.appendChild(btn3);

    const ref = { current: container };

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    expect(document.activeElement).toBe(btn1);

    // Tab to second
    btn2.focus();
    expect(document.activeElement).toBe(btn2);

    // Tab to third
    btn3.focus();
    expect(document.activeElement).toBe(btn3);

    // Tab on third should wrap to first
    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "Tab",
        bubbles: true,
      });
      container.dispatchEvent(event);
    });

    expect(document.activeElement).toBe(btn1);

    document.body.removeChild(container);
  });

  it("traps focus: Shift+Tab on first element wraps to last", () => {
    const container = createContainer();
    const btn1 = createFocusableButton("First");
    const btn2 = createFocusableButton("Second");
    const btn3 = createFocusableButton("Third");
    container.appendChild(btn1);
    container.appendChild(btn2);
    container.appendChild(btn3);

    const ref = { current: container };

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    expect(document.activeElement).toBe(btn1);

    // Shift+Tab on first should wrap to last
    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "Tab",
        shiftKey: true,
        bubbles: true,
      });
      container.dispatchEvent(event);
    });

    expect(document.activeElement).toBe(btn3);

    document.body.removeChild(container);
  });

  it("restores previous focus when active changes to false", () => {
    const container = createContainer();
    const btn1 = createFocusableButton("First");
    const btn2 = createFocusableButton("Second");
    container.appendChild(btn1);
    container.appendChild(btn2);

    const outsideBtn = createFocusableButton("Outside");
    document.body.appendChild(outsideBtn);
    outsideBtn.focus();
    expect(document.activeElement).toBe(outsideBtn);

    const ref = { current: container };

    const { rerender } = renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    expect(document.activeElement).toBe(btn1);

    rerender({ active: false });

    expect(document.activeElement).toBe(outsideBtn);

    document.body.removeChild(container);
    document.body.removeChild(outsideBtn);
  });

  it("does not prevent default for non-Tab keys", () => {
    const container = createContainer();
    const btn = createFocusableButton("btn");
    container.appendChild(btn);

    const ref = { current: container };

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    let defaultPrevented = false;
    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        cancelable: true,
      });
      defaultPrevented = !container.dispatchEvent(event);
    });

    expect(defaultPrevented).toBe(false);

    document.body.removeChild(container);
  });

  it("does nothing when there are no focusable elements", () => {
    const container = createContainer();
    const div = document.createElement("div");
    div.textContent = "no focusable elements";
    container.appendChild(div);

    const ref = { current: container };
    const prev = document.activeElement;

    renderHook(
      ({ active }) => useFocusTrap(ref, active),
      { initialProps: { active: true } },
    );

    expect(document.activeElement).toBe(prev);

    document.body.removeChild(container);
  });
});
