import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageEditor } from "../MessageEditor";

describe("MessageEditor", () => {
  it("renders with initial text", () => {
    render(<MessageEditor initial="hi" onSave={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("textbox")).toHaveValue("hi");
  });

  it("Enter triggers save with trimmed text", () => {
    const onSave = vi.fn();
    render(<MessageEditor initial="hi" onSave={onSave} onCancel={vi.fn()} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "updated " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSave).toHaveBeenCalledWith("updated");
  });

  it("Esc triggers cancel", () => {
    const onCancel = vi.fn();
    render(<MessageEditor initial="hi" onSave={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("Shift+Enter does not save", () => {
    const onSave = vi.fn();
    render(<MessageEditor initial="hi" onSave={onSave} onCancel={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: true });
    expect(onSave).not.toHaveBeenCalled();
  });
});
