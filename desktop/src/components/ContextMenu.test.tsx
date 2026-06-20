import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ContextMenu } from "./ContextMenu";

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

describe("ContextMenu", () => {
  it("renders menu items and calls action on click", async () => {
    const onClose = vi.fn();
    const onCopy = vi.fn();
    const onPaste = vi.fn();
    const items = [
      { label: "Copy", action: onCopy },
      { label: "Paste", action: onPaste },
    ];

    render(<ContextMenu x={100} y={200} items={items} onClose={onClose} />);

    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Paste" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy" }));
    await waitFor(() => {
      expect(onCopy).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  it("renders a separator between items", () => {
    const onClose = vi.fn();
    const items = [
      { label: "Cut", action: vi.fn() },
      { separator: true, label: "" },
      { label: "Delete", action: vi.fn() },
    ];

    render(<ContextMenu x={100} y={200} items={items} onClose={onClose} />);

    expect(screen.getByRole("menuitem", { name: "Cut" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
  });

  it("does not fire action for disabled items", async () => {
    const onClose = vi.fn();
    const onDisabled = vi.fn();
    const items = [
      { label: "Disabled action", action: onDisabled, disabled: true },
    ];

    render(<ContextMenu x={100} y={200} items={items} onClose={onClose} />);

    fireEvent.click(screen.getByRole("menuitem", { name: "Disabled action" }));
    await waitFor(() => {
      expect(onDisabled).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    });
  });
});
