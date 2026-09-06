import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import { EmojiPickerField } from "../EmojiPicker";

beforeAll(() => {
  class MockIntersectionObserver implements IntersectionObserver {
    readonly root: Element | null = null;
    readonly rootMargin: string = "";
    readonly thresholds: ReadonlyArray<number> = [];
    constructor(public callback: IntersectionObserverCallback) {}
    observe = (el: Element) => {
      this.callback([{ isIntersecting: true, target: el, intersectionRatio: 1, boundingClientRect: {} as DOMRectReadOnly, intersectionRect: {} as DOMRectReadOnly, time: 0 }], this);
    };
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = () => [];
  };
  Object.defineProperty(window, "IntersectionObserver", { configurable: true, writable: true, value: MockIntersectionObserver });
  Object.defineProperty(globalThis, "IntersectionObserver", { configurable: true, writable: true, value: MockIntersectionObserver });
});

describe("<EmojiPickerField />", () => {
  it("renders with minimal valid props and shows the value", () => {
    render(<EmojiPickerField value=":)" onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /open emoji picker/i })).toBeInTheDocument();
    expect(screen.getByText(":)")).toBeInTheDocument();
  });

  it("shows a plus sign when value is empty", () => {
    render(<EmojiPickerField value="" onChange={() => {}} />);
    expect(screen.getByText("+")).toBeInTheDocument();
  });

  it("toggles the picker open and closed on button click", () => {
    render(<EmojiPickerField value="" onChange={() => {}} />);
    const button = screen.getByRole("button", { name: /open emoji picker/i });
    expect(screen.queryByRole("dialog", { name: /emoji picker/i })).toBeNull();
    fireEvent.click(button);
    expect(screen.getByRole("dialog", { name: /emoji picker/i })).toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.queryByRole("dialog", { name: /emoji picker/i })).toBeNull();
  });

  it("calls onChange with an emoji string when an emoji is clicked", () => {
    const onChange = vi.fn();
    render(<EmojiPickerField value="" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /open emoji picker/i }));
    const emojiButtons = screen.getAllByRole("button", { name: /grinning face/i });
    expect(emojiButtons.length).toBeGreaterThan(0);
    fireEvent.click(emojiButtons[0]);
    expect(onChange).toHaveBeenCalled();
    expect(typeof onChange.mock.calls[0][0]).toBe("string");
  });
});
