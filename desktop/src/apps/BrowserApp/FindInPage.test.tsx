import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FindInPage } from "./FindInPage";

describe("FindInPage", () => {
  it("renders the search input + nav + close buttons", () => {
    render(<FindInPage windowId="w" onClose={() => {}} />);
    expect(screen.getByLabelText("Find query")).toBeTruthy();
    expect(screen.getByLabelText("Previous match")).toBeTruthy();
    expect(screen.getByLabelText("Next match")).toBeTruthy();
    expect(screen.getByLabelText("Close find")).toBeTruthy();
  });

  it("auto-focuses the input on mount", () => {
    render(<FindInPage windowId="w" onClose={() => {}} />);
    const input = screen.getByLabelText("Find query");
    expect(document.activeElement).toBe(input);
  });

  it("Escape calls onClose", () => {
    const onClose = vi.fn();
    render(<FindInPage windowId="w" onClose={onClose} />);
    const input = screen.getByLabelText("Find query");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("Close button calls onClose", () => {
    const onClose = vi.fn();
    render(<FindInPage windowId="w" onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("Close find"));
    expect(onClose).toHaveBeenCalled();
  });

  it("typing updates the input value", () => {
    render(<FindInPage windowId="w" onClose={() => {}} />);
    const input = screen.getByLabelText("Find query") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "hello" } });
    expect(input.value).toBe("hello");
  });
});
