import { create } from "zustand";
import type { CanvasElement } from "./canvas-api";

export interface CanvasState {
  elements: Record<string, CanvasElement>;
  seed: (elements: CanvasElement[]) => void;
  upsert: (element: CanvasElement) => void;
  remove: (elementId: string) => void;
  clear: () => void;
}

export function createCanvasStore() {
  return create<CanvasState>((set) => ({
    elements: {},
    seed: (elements) =>
      set(() => ({
        elements: Object.fromEntries(elements.map((e) => [e.id, e])),
      })),
    upsert: (element) =>
      set((s) => ({ elements: { ...s.elements, [element.id]: element } })),
    remove: (elementId) =>
      set((s) => {
        const { [elementId]: _, ...rest } = s.elements;
        return { elements: rest };
      }),
    clear: () => set({ elements: {} }),
  }));
}
