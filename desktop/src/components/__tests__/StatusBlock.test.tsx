import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBlock } from "../StatusBlock";
import type { StatusContentBlock, QuestionContentBlock } from "@/apps/MessagesApp";

describe("StatusBlock", () => {
  it("renders a muted status line", () => {
    const block: StatusContentBlock = { kind: "status", text: "working" };
    const { container } = render(<StatusBlock block={block} />);
    const line = container.querySelector(
      '[data-status-block="true"][data-variant="status"]',
    );
    expect(line).not.toBeNull();
    expect(line?.className).toContain("text-shell-text-tertiary");
    expect(container.textContent).toContain("working");
  });

  it("does not apply the question accent to a plain status", () => {
    const block: StatusContentBlock = { kind: "status", text: "ok" };
    const { container } = render(<StatusBlock block={block} />);
    expect(container.querySelector('[data-variant="status"]')).not.toBeNull();
    expect(container.querySelector('[data-variant="question"]')).toBeNull();
  });

  it("renders the question variant with an accent border and reply hint", () => {
    const block: QuestionContentBlock = { kind: "question", text: "more?" };
    const { container } = render(<StatusBlock block={block} />);
    const line = container.querySelector(
      '[data-status-block="true"][data-variant="question"]',
    );
    expect(line).not.toBeNull();
    expect(line?.className).toContain("border-accent-line");
    expect(container.textContent).toContain("more?");
    expect(container.textContent).toContain("reply below");
  });

  it("renders question text without turning options into interactive controls", () => {
    const block: QuestionContentBlock = {
      kind: "question",
      text: "pick one",
      options: ["a", "b"],
    };
    const { container } = render(<StatusBlock block={block} />);
    expect(container.textContent).toContain("pick one");
    expect(container.textContent).toContain("reply below");
    // s1 questions are passive: no option buttons are rendered.
    expect(container.querySelector("button")).toBeNull();
  });
});
