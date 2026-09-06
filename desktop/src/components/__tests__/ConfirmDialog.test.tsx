import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ConfirmDialog } from "../ConfirmDialog";

describe("ConfirmDialog", () => {
  it("focuses the confirm button when opened", async () => {
    render(
      <ConfirmDialog
        open={true}
        title="Confirm?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
  });

  it("closes on Escape", async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="Confirm?"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("closes on backdrop click", async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="Confirm?"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Confirm?" });
    fireEvent.click(dialog);
    expect(onCancel).toHaveBeenCalled();
  });

  it("traps Tab focus inside the panel", async () => {
    render(
      <ConfirmDialog
        open={true}
        title="Confirm?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const confirmBtn = screen.getByRole("button", { name: "Confirm" });
    const cancelBtn = screen.getByRole("button", { name: "Cancel" });

    expect(confirmBtn).toHaveFocus();

    fireEvent.keyDown(document, { key: "Tab" });
    expect(cancelBtn).toHaveFocus();

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirmBtn).toHaveFocus();
  });
});
