import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ContextMenu } from "./ContextMenu";

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

describe("ContextMenu", () => {
  it("renders all menu item labels", () => {
    const items = [
      { label: "Copy", action: vi.fn() },
      { label: "Paste", action: vi.fn() },
      { label: "Delete", action: vi.fn() },
    ];
    render(
      <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
    );

    expect(screen.getByText("Copy")).toBeInTheDocument();
    expect(screen.getByText("Paste")).toBeInTheDocument();
    expect(screen.getByText("Delete")).toBeInTheDocument();
  });

  it("renders a separator between items", () => {
    const items = [
      { label: "Copy", action: vi.fn() },
      { separator: true, label: "" },
      { label: "Paste", action: vi.fn() },
    ];
    render(
      <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
    );

    expect(screen.getByText("Copy")).toBeInTheDocument();
    expect(screen.getByText("Paste")).toBeInTheDocument();
    // The separator renders as a <div> with a top border
    const menu = screen.getByRole("menu");
    const separators = menu.querySelectorAll("div.border-t");
    expect(separators.length).toBeGreaterThanOrEqual(1);
  });

  it("renders disabled items and does not fire action on click", () => {
    const action = vi.fn();
    const items = [
      { label: "Disabled Item", action, disabled: true },
      { label: "Enabled Item", action: vi.fn() },
    ];
    render(
      <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
    );

    const disabledBtn = screen.getByRole("menuitem", { name: /disabled item/i });
    expect(disabledBtn).toBeDisabled();

    fireEvent.click(disabledBtn);
    expect(action).not.toHaveBeenCalled();
  });

  it("fires action and onClose when an enabled item is clicked", () => {
    const action = vi.fn();
    const onClose = vi.fn();
    const items = [{ label: "Save", action }];
    render(<ContextMenu x={100} y={100} items={items} onClose={onClose} />);

    fireEvent.click(screen.getByRole("menuitem", { name: /save/i }));

    expect(action).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    const items = [{ label: "One", action: vi.fn() }];
    render(<ContextMenu x={100} y={100} items={items} onClose={onClose} />);

    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("navigates to next item with ArrowDown", () => {
    const items = [
      { label: "First", action: vi.fn() },
      { label: "Second", action: vi.fn() },
    ];
    render(
      <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
    );

    const menu = screen.getByRole("menu");
    // First item gets auto-focused by the useEffect
    const firstItem = screen.getByRole("menuitem", { name: /first/i });
    expect(document.activeElement).toBe(firstItem);

    fireEvent.keyDown(menu, { key: "ArrowDown" });

    const secondItem = screen.getByRole("menuitem", { name: /second/i });
    expect(document.activeElement).toBe(secondItem);
  });

  it("wraps around with ArrowDown at the last item", () => {
    const items = [
      { label: "First", action: vi.fn() },
      { label: "Last", action: vi.fn() },
    ];
    render(
      <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
    );

    const menu = screen.getByRole("menu");
    const firstItem = screen.getByRole("menuitem", { name: /first/i });
    expect(document.activeElement).toBe(firstItem);

    // Move to last
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: /last/i }));

    // Wrap to first
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(firstItem);
  });

  it("renders items with icons", () => {
    const items = [
      { label: "Copy", icon: <span data-testid="copy-icon">C</span>, action: vi.fn() },
    ];
    render(
      <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
    );

    expect(screen.getByTestId("copy-icon")).toBeInTheDocument();
    expect(screen.getByText("Copy")).toBeInTheDocument();
  });

  it("renders an empty menu without crashing", () => {
    const onClose = vi.fn();
    render(<ContextMenu x={100} y={100} items={[]} onClose={onClose} />);

    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    // Pressing Escape with no items still calls onClose
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("has the correct aria-label on the menu", () => {
    render(
      <ContextMenu x={100} y={100} items={[{ label: "Test", action: vi.fn() }]} onClose={vi.fn()} />
    );

    expect(screen.getByRole("menu")).toHaveAttribute("aria-label", "Context menu");
  });
});
