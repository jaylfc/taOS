import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useState } from "react";
import { ContextMenu, MenuItem } from "../ContextMenu";

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

  it("arrow keys do not throw or produce NaN when all items are disabled", () => {
    const allDisabled = [
      { label: "Copy", action: vi.fn(), disabled: true },
      { label: "Paste", action: vi.fn(), disabled: true },
    ];
    const { container } = render(<ContextMenu x={100} y={100} items={allDisabled} onClose={vi.fn()} />);
    const menu = container.firstChild as HTMLElement;
    // None of these should throw or attempt to focus undefined
    expect(() => fireEvent.keyDown(menu, { key: "ArrowDown" })).not.toThrow();
    expect(() => fireEvent.keyDown(menu, { key: "ArrowUp" })).not.toThrow();
    expect(() => fireEvent.keyDown(menu, { key: "Home" })).not.toThrow();
    expect(() => fireEvent.keyDown(menu, { key: "End" })).not.toThrow();
    // Escape still calls onClose even with all disabled
    const onClose = vi.fn();
    const { container: c2 } = render(<ContextMenu x={100} y={100} items={allDisabled} onClose={onClose} />);
    fireEvent.keyDown(c2.firstChild as HTMLElement, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("refocuses first enabled item when items prop changes while menu is open", () => {
    const initialItems: MenuItem[] = [
      { label: "Copy", action: vi.fn() },
      { label: "Paste", action: vi.fn() },
    ];
    const updatedItems: MenuItem[] = [
      { label: "Share", action: vi.fn() },
      { label: "Export", action: vi.fn() },
    ];

    function Wrapper() {
      const [items, setItems] = useState<MenuItem[]>(initialItems);
      return (
        <>
          <button data-testid="swap" onClick={() => setItems(updatedItems)} />
          <ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />
        </>
      );
    }

    render(<Wrapper />);
    // First item of initial items is focused
    expect(document.activeElement).toBe(screen.getAllByRole("menuitem")[0]);
    expect(document.activeElement?.textContent).toBe("Copy");

    // Swap items
    act(() => {
      fireEvent.click(screen.getByTestId("swap"));
    });

    // First item of updated items should now be focused
    expect(document.activeElement?.textContent).toBe("Share");
  });

  it("ArrowDown when focus is outside list moves to first enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    // Move focus to a focusable element outside the menu
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    expect(document.activeElement).toBe(outside);
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]); // first enabled: Copy
    outside.remove();
  });

  it("ArrowUp when focus is outside list moves to last enabled item", () => {
    const { container } = renderMenu();
    const menu = container.firstChild as HTMLElement;
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    expect(document.activeElement).toBe(outside);
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[3]); // last enabled: Rename
    outside.remove();
  });
});
