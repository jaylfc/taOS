import { describe, it, expect, vi } from "vitest";

vi.mock("@excalidraw/mermaid-to-excalidraw", () => ({
  parseMermaidToExcalidraw: vi.fn(async (src: string) => {
    if (src.includes("BOOM")) throw new Error("invalid mermaid");
    return { elements: [{ type: "rectangle", x: 5, y: 7, width: 10, height: 10 }], files: {} };
  }),
}));
vi.mock("@excalidraw/excalidraw", () => ({
  convertToExcalidrawElements: (els: unknown[]) => els,
}));

import { mermaidToExcalidraw } from "../canvas/mermaid-to-elements";

describe("mermaidToExcalidraw", () => {
  it("converts the diagram and translates it by the element offset", async () => {
    const out = await mermaidToExcalidraw("graph TD\n A-->B", 100, 200);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ type: "rectangle", x: 105, y: 207 });
  });

  it("returns an empty array for a blank source", async () => {
    expect(await mermaidToExcalidraw("   ", 0, 0)).toEqual([]);
  });

  it("returns an empty array when the mermaid source is invalid", async () => {
    expect(await mermaidToExcalidraw("BOOM", 0, 0)).toEqual([]);
  });
});
