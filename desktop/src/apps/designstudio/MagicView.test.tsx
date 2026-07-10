import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MagicView } from "./MagicView";
import type { GeneratedImage } from "./types";

function props(over: Partial<Parameters<typeof MagicView>[0]> = {}) {
  return {
    prompt: "",
    onPromptChange: vi.fn(),
    style: null as string | null,
    onStyleChange: vi.fn(),
    results: [] as GeneratedImage[],
    generating: false,
    canGenerate: true,
    error: null as string | null,
    errorNeedsModel: false,
    needsModel: false,
    onGenerate: vi.fn(),
    onPickModel: vi.fn(),
    onUseResult: vi.fn(),
    ...over,
  };
}

describe("MagicView", () => {
  it("edits the prompt through onPromptChange", () => {
    const p = props();
    render(<MagicView {...p} />);
    fireEvent.change(screen.getByLabelText("Design prompt"), { target: { value: "a poster" } });
    expect(p.onPromptChange).toHaveBeenCalledWith("a poster");
  });

  it("disables Generate when canGenerate is false", () => {
    render(<MagicView {...props({ canGenerate: false })} />);
    expect((screen.getByRole("button", { name: /Generate/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("fires onGenerate when enabled and clicked", () => {
    const p = props({ canGenerate: true });
    render(<MagicView {...p} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));
    expect(p.onGenerate).toHaveBeenCalledTimes(1);
  });

  it("shows the busy label while generating", () => {
    render(<MagicView {...props({ generating: true })} />);
    expect(screen.getByText(/Generating\.\.\./)).toBeDefined();
  });

  it("toggles a style chip on and off", () => {
    const p = props({ style: null });
    const { rerender } = render(<MagicView {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Bold" }));
    expect(p.onStyleChange).toHaveBeenCalledWith("Bold");

    // When a chip is already active, clicking clears it.
    p.onStyleChange.mockClear();
    rerender(<MagicView {...props({ ...p, style: "Bold" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Bold" }));
    expect(p.onStyleChange).toHaveBeenCalledWith(null);
  });

  it("shows the install-a-model prompt and wires Browse models", () => {
    const p = props({ needsModel: true });
    render(<MagicView {...p} />);
    expect(screen.getByText(/Install an image generation model/i)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Browse models" }));
    expect(p.onPickModel).toHaveBeenCalledTimes(1);
  });

  it("renders an error and, when it needs a model, an install action", () => {
    const p = props({ error: "boom", errorNeedsModel: true });
    render(<MagicView {...p} />);
    expect(screen.getByText("boom")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Install a model" }));
    expect(p.onPickModel).toHaveBeenCalledTimes(1);
  });

  it("does not show an install action for a plain error", () => {
    render(<MagicView {...props({ error: "just a message", errorNeedsModel: false })} />);
    expect(screen.getByText("just a message")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Install a model" })).toBeNull();
  });

  it("renders results and fires onUseResult with the picked image", () => {
    const img: GeneratedImage = { id: "g1", url: "/x.png", prompt: "a cat poster" };
    const p = props({ results: [img] });
    render(<MagicView {...p} />);
    const tile = screen.getByAltText("a cat poster");
    fireEvent.click(tile);
    expect(p.onUseResult).toHaveBeenCalledWith(img);
  });
});
