import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TextBlock, ThinkingBlock } from "../../MessagesApp";

describe("TextBlock", () => {
  it("renders the block text through the markdown renderer", () => {
    const { container } = render(
      <TextBlock block={{ kind: "text", text: "hello **bold** world" }} index={0} />,
    );
    expect(container.textContent).toContain("hello");
    expect(container.textContent).toContain("bold");
    expect(container.textContent).toContain("world");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
  });

  it("renders inline code with the dim code styling", () => {
    const { container } = render(
      <TextBlock block={{ kind: "text", text: "see `code` here" }} index={0} />,
    );
    const code = container.querySelector("code");
    expect(code?.textContent).toBe("code");
  });

  it("renders markdown lists", () => {
    const { container } = render(
      <TextBlock block={{ kind: "text", text: "- one\n- two\n- three" }} index={0} />,
    );
    expect(container.querySelectorAll("li").length).toBeGreaterThanOrEqual(3);
  });
});

describe("ThinkingBlock", () => {
  it("is collapsed by default and hides its content", () => {
    render(
      <ThinkingBlock block={{ kind: "thinking", text: "deep thoughts", collapsed: true }} index={0} />,
    );
    const button = screen.getByRole("button", { name: /thinking/i });
    expect(button).toHaveAttribute("aria-expanded", "false");
    const controlsId = button.getAttribute("aria-controls");
    expect(controlsId).toBeTruthy();
    const panel = document.getElementById(controlsId!);
    expect(panel).not.toBeNull();
    expect(panel?.hasAttribute("hidden")).toBe(true);
    expect(panel?.getAttribute("aria-labelledby")).toBe(button.getAttribute("id"));
    expect(screen.getByText("Thinking")).toBeInTheDocument();
  });

  it("starts collapsed even when collapsed is not specified", () => {
    render(
      <ThinkingBlock block={{ kind: "thinking", text: "hidden" }} index={0} />,
    );
    const button = screen.getByRole("button", { name: /thinking/i });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(button.getAttribute("aria-controls")!)?.hasAttribute("hidden")).toBe(true);
  });

  it("starts open only when collapsed is explicitly false", () => {
    render(
      <ThinkingBlock block={{ kind: "thinking", text: "visible thoughts", collapsed: false }} index={0} />,
    );
    const button = screen.getByRole("button", { name: /thinking/i });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(button.getAttribute("aria-controls")!)?.hasAttribute("hidden")).toBe(false);
    expect(screen.getByText("visible thoughts")).toBeInTheDocument();
  });

  it("toggles open and updates ARIA + hidden state", () => {
    render(
      <ThinkingBlock block={{ kind: "thinking", text: "deep thoughts" }} index={0} />,
    );
    const button = screen.getByRole("button", { name: /thinking/i });
    const controlsId = button.getAttribute("aria-controls")!;

    // collapsed -> content hidden
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(controlsId)?.hasAttribute("hidden")).toBe(true);

    // expand
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(controlsId)?.hasAttribute("hidden")).toBe(false);
    expect(screen.getByText("deep thoughts")).toBeInTheDocument();

    // collapse again
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(controlsId)?.hasAttribute("hidden")).toBe(true);
  });

  it("renders the thinking body as markdown once expanded", () => {
    render(
      <ThinkingBlock block={{ kind: "thinking", text: "a **bold** plan" }} index={0} />,
    );
    const button = screen.getByRole("button", { name: /thinking/i });
    fireEvent.click(button);
    const strong = screen.getByText("bold");
    expect(strong.tagName).toBe("STRONG");
  });

  it("uses distinct ids per instance and links via aria-labelledby", () => {
    render(
      <div>
        <ThinkingBlock block={{ kind: "thinking", text: "first" }} index={0} />
        <ThinkingBlock block={{ kind: "thinking", text: "second" }} index={1} />
      </div>,
    );
    const buttons = screen.getAllByRole("button", { name: /thinking/i });
    const controls = buttons.map((b) => b.getAttribute("aria-controls")!);
    expect(controls[0]).not.toBe(controls[1]);
    buttons.forEach((b) => {
      const panel = document.getElementById(b.getAttribute("aria-controls")!);
      expect(panel).not.toBeNull();
      expect(panel?.getAttribute("aria-labelledby")).toBe(b.getAttribute("id"));
    });
  });

  it("has dim styling on the toggle and container", () => {
    const { container } = render(
      <ThinkingBlock block={{ kind: "thinking", text: "x" }} index={0} />,
    );
    const button = screen.getByRole("button", { name: /thinking/i });
    expect(button.className).toMatch(/text-shell-text-tertiary/);
    const card = container.firstElementChild;
    expect(card?.className).toMatch(/border-shell-border/);
    expect(card?.className).toMatch(/bg-shell-surface/);
  });
});
