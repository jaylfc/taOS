import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PropertiesPanel } from "./PropertiesPanel";
import type { CanvasElement } from "./types";

function textEl(over: Partial<CanvasElement> = {}): CanvasElement {
  return {
    id: "t",
    type: "text",
    x: 12.4,
    y: 40.6,
    width: 100,
    height: 40,
    rotation: 0,
    zIndex: 0,
    visible: true,
    text: "Hi",
    fontSize: 32,
    fontFamily: "Inter",
    fill: "#ffffff",
    align: "left",
    ...over,
  } as CanvasElement;
}

function rectEl(over: Partial<CanvasElement> = {}): CanvasElement {
  return {
    id: "r",
    type: "rect",
    x: 0,
    y: 0,
    width: 50,
    height: 60,
    rotation: 0,
    zIndex: 0,
    visible: true,
    fill: "#112233",
    stroke: "#445566",
    strokeWidth: 2,
    ...over,
  } as CanvasElement;
}

function lineEl(): CanvasElement {
  return {
    id: "l",
    type: "line",
    x: 0,
    y: 0,
    width: 80,
    height: 2,
    rotation: 0,
    zIndex: 0,
    visible: true,
    stroke: "#abcdef",
    strokeWidth: 3,
  } as CanvasElement;
}

function props(over: Partial<Parameters<typeof PropertiesPanel>[0]> = {}) {
  return {
    onUpdate: vi.fn(),
    onDuplicate: vi.fn(),
    onDelete: vi.fn(),
    ...over,
  };
}

describe("PropertiesPanel", () => {
  it("prompts to select an element when nothing is selected", () => {
    render(<PropertiesPanel selected={null} {...props()} />);
    expect(screen.getByText(/Select an element/i)).toBeDefined();
  });

  it("shows text controls for a text element and emits font-size updates", () => {
    const p = props();
    render(<PropertiesPanel selected={textEl()} {...p} />);
    const size = screen.getByLabelText("Font size") as HTMLInputElement;
    expect(size.value).toBe("32");
    fireEvent.change(size, { target: { value: "48" } });
    expect(p.onUpdate).toHaveBeenCalledWith({ fontSize: 48 });
  });

  it("emits text color and alignment updates", () => {
    const p = props();
    render(<PropertiesPanel selected={textEl()} {...p} />);
    fireEvent.change(screen.getByLabelText("Text color"), { target: { value: "#ff0000" } });
    expect(p.onUpdate).toHaveBeenCalledWith({ fill: "#ff0000" });
    fireEvent.click(screen.getByLabelText("Align center"));
    expect(p.onUpdate).toHaveBeenCalledWith({ align: "center" });
  });

  it("reflects the active alignment via aria-pressed", () => {
    render(<PropertiesPanel selected={textEl({ align: "right" })} {...props()} />);
    expect(screen.getByLabelText("Align right").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByLabelText("Align left").getAttribute("aria-pressed")).toBe("false");
  });

  it("shows fill/stroke controls for a rect and emits updates", () => {
    const p = props();
    render(<PropertiesPanel selected={rectEl()} {...p} />);
    fireEvent.change(screen.getByLabelText("Fill color"), { target: { value: "#000000" } });
    expect(p.onUpdate).toHaveBeenCalledWith({ fill: "#000000" });
    fireEvent.change(screen.getByLabelText("Stroke width"), { target: { value: "5" } });
    expect(p.onUpdate).toHaveBeenCalledWith({ strokeWidth: 5 });
    // Text-only controls must not appear for a shape.
    expect(screen.queryByLabelText("Font size")).toBeNull();
  });

  it("shows only a stroke control for a line (no fill)", () => {
    render(<PropertiesPanel selected={lineEl()} {...props()} />);
    expect(screen.getByLabelText("Stroke color")).toBeDefined();
    expect(screen.queryByLabelText("Fill color")).toBeNull();
  });

  it("rounds and displays position and size", () => {
    render(<PropertiesPanel selected={textEl({ x: 12.4, y: 40.6, width: 100, height: 40 })} {...props()} />);
    expect(screen.getByText("X 12")).toBeDefined();
    expect(screen.getByText("Y 41")).toBeDefined();
    expect(screen.getByText("W 100")).toBeDefined();
    expect(screen.getByText("H 40")).toBeDefined();
  });

  it("wires the duplicate and delete buttons", () => {
    const p = props();
    render(<PropertiesPanel selected={rectEl()} {...p} />);
    fireEvent.click(screen.getByLabelText("Duplicate element"));
    expect(p.onDuplicate).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("Delete element"));
    expect(p.onDelete).toHaveBeenCalledTimes(1);
  });

  it("renders the coming-soon Magic chips as disabled", () => {
    render(<PropertiesPanel selected={rectEl()} {...props()} />);
    const chip = screen.getByRole("button", { name: "Make it bolder" }) as HTMLButtonElement;
    expect(chip.disabled).toBe(true);
  });
});
