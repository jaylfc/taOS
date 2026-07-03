import type { CanvasElement, ImageElementData } from "./types";

let counter = 0;

function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${Date.now()}-${counter}`;
}

export function nextZIndex(elements: CanvasElement[]): number {
  return elements.reduce((max, el) => Math.max(max, el.zIndex), -1) + 1;
}

/** Centers a new element's default box inside the artboard. */
function centeredBox(artboardWidth: number, artboardHeight: number, w: number, h: number) {
  return { x: Math.round((artboardWidth - w) / 2), y: Math.round((artboardHeight - h) / 2) };
}

export function createTextElement(
  elements: CanvasElement[],
  artboardWidth: number,
  artboardHeight: number,
): CanvasElement {
  const w = Math.min(320, artboardWidth * 0.6);
  const h = 60;
  return {
    id: nextId("text"),
    type: "text",
    ...centeredBox(artboardWidth, artboardHeight, w, h),
    width: w,
    height: h,
    rotation: 0,
    zIndex: nextZIndex(elements),
    visible: true,
    text: "Double-click to edit",
    fontSize: 32,
    fontFamily: "Inter, sans-serif",
    fill: "#ffffff",
    align: "left",
  };
}

export function createShapeElement(
  elements: CanvasElement[],
  kind: "rect" | "ellipse",
  artboardWidth: number,
  artboardHeight: number,
): CanvasElement {
  const size = Math.min(220, artboardWidth * 0.35);
  return {
    id: nextId(kind),
    type: kind,
    ...centeredBox(artboardWidth, artboardHeight, size, size),
    width: size,
    height: size,
    rotation: 0,
    zIndex: nextZIndex(elements),
    visible: true,
    fill: "#8b92a3",
    stroke: "#ffffff",
    strokeWidth: 0,
  };
}

export function createLineElement(
  elements: CanvasElement[],
  artboardWidth: number,
  artboardHeight: number,
): CanvasElement {
  const w = Math.min(280, artboardWidth * 0.5);
  return {
    id: nextId("line"),
    type: "line",
    ...centeredBox(artboardWidth, artboardHeight, w, 2),
    width: w,
    height: 2,
    rotation: 0,
    zIndex: nextZIndex(elements),
    visible: true,
    stroke: "#ffffff",
    strokeWidth: 3,
  };
}

export function createImageElement(
  elements: CanvasElement[],
  src: string,
  artboardWidth: number,
  artboardHeight: number,
  prompt?: string,
): ImageElementData {
  const w = Math.min(320, artboardWidth * 0.5);
  const h = w;
  return {
    id: nextId("image"),
    type: "image",
    ...centeredBox(artboardWidth, artboardHeight, w, h),
    width: w,
    height: h,
    rotation: 0,
    zIndex: nextZIndex(elements),
    visible: true,
    src,
    prompt,
  };
}

export function duplicateElement(el: CanvasElement, elements: CanvasElement[]): CanvasElement {
  return {
    ...el,
    id: nextId(el.type),
    x: el.x + 16,
    y: el.y + 16,
    zIndex: nextZIndex(elements),
  };
}
