import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

// Konva renders to a real <canvas>, which jsdom does not implement. Mock
// react-konva so the board's mapping + structure can be asserted without a
// canvas. Each primitive becomes a div carrying its key props as data attrs.
vi.mock("react-konva", () => {
  const make =
    (name: string) =>
    ({ children, text, fill }: { children?: unknown; text?: string; fill?: string }) =>
      (
        <div data-konva={name} data-text={text} data-fill={fill}>
          {children as never}
        </div>
      );
  return {
    Stage: make("stage"),
    Layer: make("layer"),
    Group: make("group"),
    Rect: make("rect"),
    Text: make("text"),
  };
});

import { KonvaBoard } from "../canvas/KonvaBoard";
import type { CanvasElement } from "../canvas/canvas-api";

function el(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "e", project_id: "p", kind: "note", author_kind: "user", author_id: "u",
    x: 0, y: 0, w: 80, h: 60, rotation: 0, z_index: 0, payload: {},
    created_at: 0, updated_at: 0, deleted_at: null, ...over,
  };
}

describe("KonvaBoard", () => {
  it("renders a node group per non-deleted element", () => {
    const { container } = render(
      <KonvaBoard
        width={400}
        height={300}
        elements={[
          el({ id: "n1", kind: "note", payload: { text: "alpha" } }),
          el({ id: "l1", kind: "link", payload: { url: "https://x.test", title: "X" } }),
          el({ id: "gone", deleted_at: 1 }),
        ]}
      />,
    );
    // note (Group) + link (Group) = 2 groups; the deleted one is skipped.
    expect(container.querySelectorAll('[data-konva="group"]').length).toBe(2);
    // the note text reaches a Text primitive.
    const texts = Array.from(container.querySelectorAll('[data-konva="text"]')).map(
      (t) => t.getAttribute("data-text"),
    );
    expect(texts).toContain("alpha");
  });

  it("renders an empty stage with no elements", () => {
    const { container } = render(<KonvaBoard width={400} height={300} elements={[]} />);
    expect(container.querySelector('[data-konva="stage"]')).toBeTruthy();
    expect(container.querySelectorAll('[data-konva="group"]').length).toBe(0);
  });
});
