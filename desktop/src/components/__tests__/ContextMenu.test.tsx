import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ContextMenu } from "../ContextMenu";

const ITEMS = [
  { label: "Copy", action: vi.fn() },
  { label: "Paste", action: vi.fn() },
  { label: "Delete", action: vi.fn(), disabled: true },
  { label: "Rename", action: vi.fn() },
];

function renderMenu(onClose = vi.fn()) {
  return render(<ContextMenu x={100} y={100} items={ITEMS} onClose={onClose} />);
}

describe("ContextMenu keyboard navigation", () => {
  it("renders role=menu and role=menuitem", () => {
    renderMenu();
    expect(screen.getByRole("menu")).toBeInTheDocument();
    const menuItems = screen.getAllByRole("menuitem");
    // 4 items total (disabled still gets role=menuitem)
    expect(menuItems.length).toBe(4);
  });

  it("focuses first enabled item on open", () => {
    renderMenu();
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);
  });

  it("ArrowDown moves focus to next enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    const items = screen.getAllByRole("menuitem");
    // Skip disabled "Delete", so ArrowDown from Copy → Paste
    expect(document.activeElement).toBe(items[1]);
  });

  it("ArrowDown wraps from last to first enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    // Navigate to last enabled item (Rename)
    fireEvent.keyDown(menu, { key: "End" });
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]); // wraps to Copy
  });

  it("ArrowUp moves focus to previous enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    fireEvent.keyDown(menu, { key: "ArrowDown" }); // Paste
    fireEvent.keyDown(menu, { key: "ArrowUp" });   // back to Copy
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);
  });

  it("Home moves focus to first enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    fireEvent.keyDown(menu, { key: "Home" });
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);
  });

  it("End moves focus to last enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    fireEvent.keyDown(menu, { key: "End" });
    // Rename is last enabled (Delete is disabled)
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[3]);
  });

  it("Escape calls onClose", () => {
    const onClose = vi.fn();
    const { container } = renderMenu(onClose);
    const menu = container.firstChild as HTMLElement;
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("disabled item is skipped by arrow navigation", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    // ArrowDown from Copy → Paste, ArrowDown again → Rename (skipping disabled Delete)
    fireEvent.keyDown(menu, { key: "ArrowDown" }); // Paste
    fireEvent.keyDown(menu, { key: "ArrowDown" }); // Rename (skips Delete)
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[3]); // Rename
  });

  it("roving tabindex: active item has tabIndex=0, others -1", () => {
    renderMenu();
    const items = screen.getAllByRole("menuitem");
    // First enabled item (Copy) should be tabIndex=0
    expect(items[0].getAttribute("tabIndex")).toBe("0");
    expect(items[1].getAttribute("tabIndex")).toBe("-1");
  });
});
