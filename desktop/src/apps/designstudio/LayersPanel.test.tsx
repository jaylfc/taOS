import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LayersPanel } from "./LayersPanel";
import type { CanvasElement } from "./types";

function text(id: string, zIndex: number, over: Partial<CanvasElement> = {}): CanvasElement {
  return {
    id,
    type: "text",
    x: 0,
    y: 0,
    width: 100,
    height: 40,
    rotation: 0,
    zIndex,
    visible: true,
    text: "Hello world",
    fontSize: 32,
    fontFamily: "Inter",
    fill: "#fff",
    align: "left",
    ...over,
  } as CanvasElement;
}

function baseProps() {
  return {
    onSelect: vi.fn(),
    onToggleVisibility: vi.fn(),
    onReorder: vi.fn(),
    onDelete: vi.fn(),
  };
}

describe("LayersPanel", () => {
  it("shows an empty state with no layers", () => {
    render(<LayersPanel elements={[]} selectedId={null} {...baseProps()} />);
    expect(screen.getByText(/No layers yet/i)).toBeDefined();
  });

  it("lists layers top-most (highest zIndex) first", () => {
    const els = [text("low", 0, { text: "Bottom" }), text("high", 5, { text: "Top" })];
    render(<LayersPanel elements={els} selectedId={null} {...baseProps()} />);
    const labels = screen.getAllByText(/Top|Bottom/).map((n) => n.textContent);
    expect(labels[0]).toBe("Top");
    expect(labels[1]).toBe("Bottom");
  });

  it("labels typed layers by their content", () => {
    const els = [
      text("t", 3),
      { ...text("r", 2), type: "rect", fill: "#000", stroke: "#fff", strokeWidth: 0 } as CanvasElement,
      { ...text("l", 1), type: "line", stroke: "#fff", strokeWidth: 3 } as CanvasElement,
    ];
    render(<LayersPanel elements={els} selectedId={null} {...baseProps()} />);
    expect(screen.getByText("Hello world")).toBeDefined();
    expect(screen.getByText("Rectangle")).toBeDefined();
    expect(screen.getByText("Line")).toBeDefined();
  });

  it("fires onSelect with the clicked layer id", () => {
    const props = baseProps();
    render(<LayersPanel elements={[text("a", 0)]} selectedId={null} {...props} />);
    fireEvent.click(screen.getByLabelText(/Select layer/i));
    expect(props.onSelect).toHaveBeenCalledWith("a");
  });

  it("marks the selected layer via aria-pressed", () => {
    render(<LayersPanel elements={[text("a", 0)]} selectedId="a" {...baseProps()} />);
    expect(screen.getByLabelText(/Select layer/i).getAttribute("aria-pressed")).toBe("true");
  });

  it("disables move-up on the top layer and move-down on the bottom layer", () => {
    const els = [text("bottom", 0, { text: "Bottom" }), text("top", 5, { text: "Top" })];
    render(<LayersPanel elements={els} selectedId={null} {...baseProps()} />);
    // Top row (rendered first) can't move up; bottom row can't move down.
    expect((screen.getByLabelText("Move Top up") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Move Bottom down") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Move Top down") as HTMLButtonElement).disabled).toBe(false);
  });

  it("fires onReorder with the direction", () => {
    const props = baseProps();
    const els = [text("bottom", 0, { text: "Bottom" }), text("top", 5, { text: "Top" })];
    render(<LayersPanel elements={els} selectedId={null} {...props} />);
    fireEvent.click(screen.getByLabelText("Move Top down"));
    expect(props.onReorder).toHaveBeenCalledWith("top", "down");
  });

  it("toggles visibility with the correct label per state", () => {
    const props = baseProps();
    const els = [text("a", 0), text("b", 1, { visible: false, text: "Hidden" })];
    render(<LayersPanel elements={els} selectedId={null} {...props} />);
    fireEvent.click(screen.getByLabelText("Hide Hello world"));
    expect(props.onToggleVisibility).toHaveBeenCalledWith("a");
    expect(screen.getByLabelText("Show Hidden")).toBeDefined();
  });

  it("fires onDelete for the layer", () => {
    const props = baseProps();
    render(<LayersPanel elements={[text("a", 0)]} selectedId={null} {...props} />);
    fireEvent.click(screen.getByLabelText("Delete Hello world"));
    expect(props.onDelete).toHaveBeenCalledWith("a");
  });
});
