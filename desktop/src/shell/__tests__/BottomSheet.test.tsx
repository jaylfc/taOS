import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BottomSheet } from "../BottomSheet";

describe("BottomSheet", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <BottomSheet open={false} onClose={vi.fn()}>
        <div>content</div>
      </BottomSheet>
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders children when open", () => {
    render(
      <BottomSheet open={true} onClose={vi.fn()}>
        <div>hello sheet</div>
      </BottomSheet>
    );
    expect(screen.getByText("hello sheet")).toBeInTheDocument();
  });

  it("Escape key calls onClose", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open={true} onClose={onClose}>
        <div>x</div>
      </BottomSheet>
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("backdrop click calls onClose", () => {
    const onClose = vi.fn();
    render(
      <BottomSheet open={true} onClose={onClose}>
        <div>x</div>
      </BottomSheet>
    );
    fireEvent.click(screen.getByTestId("bottom-sheet-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });

  it("drag handle renders by default", () => {
    render(
      <BottomSheet open={true} onClose={vi.fn()}>
        <div>x</div>
      </BottomSheet>
    );
    expect(screen.getByTestId("bottom-sheet-handle")).toBeInTheDocument();
  });

  it("dragHandle=false hides the handle", () => {
    render(
      <BottomSheet open={true} onClose={vi.fn()} dragHandle={false}>
        <div>x</div>
      </BottomSheet>
    );
    expect(screen.queryByTestId("bottom-sheet-handle")).toBeNull();
  });
});
