/* ------------------------------------------------------------------ */
/*  Design Studio -- shared types                                      */
/* ------------------------------------------------------------------ */

export type DesignStudioView = "design" | "templates" | "elements" | "magic";

export interface GeneratedImage {
  id: string;
  url: string;
  prompt: string;
}

/** The current design's page size. */
export interface Artboard {
  name: string;
  width: number;
  height: number;
}

interface BaseElement {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  zIndex: number;
  visible: boolean;
}

export interface TextElementData extends BaseElement {
  type: "text";
  text: string;
  fontSize: number;
  fontFamily: string;
  fill: string;
  align: "left" | "center" | "right";
}

/** An image layer placed on the design canvas artboard. */
export interface ImageElementData extends BaseElement {
  type: "image";
  src: string;
  prompt?: string;
}

export interface RectElementData extends BaseElement {
  type: "rect";
  fill: string;
  stroke: string;
  strokeWidth: number;
}

export interface EllipseElementData extends BaseElement {
  type: "ellipse";
  fill: string;
  stroke: string;
  strokeWidth: number;
}

export interface LineElementData extends BaseElement {
  type: "line";
  stroke: string;
  strokeWidth: number;
}

export type CanvasElement =
  | TextElementData
  | ImageElementData
  | RectElementData
  | EllipseElementData
  | LineElementData;

/** Elements with a fill color (used to wire brand swatches + fill pickers). */
export type FillableElement =
  | TextElementData
  | RectElementData
  | EllipseElementData;

export function hasFill(el: CanvasElement): el is FillableElement {
  return el.type === "text" || el.type === "rect" || el.type === "ellipse";
}

export function hasStroke(
  el: CanvasElement,
): el is RectElementData | EllipseElementData | LineElementData {
  return el.type === "rect" || el.type === "ellipse" || el.type === "line";
}

export const MAGIC_STYLE_CHIPS = [
  "Bold",
  "Minimal",
  "Editorial",
  "Playful",
  "Dark",
  "Corporate",
] as const;

export const ZOOM_STEPS = [0.25, 0.5, 0.75, 1, 1.5, 2] as const;

export const DEFAULT_ARTBOARD: Artboard = {
  name: "Untitled poster",
  width: 1080,
  height: 1350,
};
