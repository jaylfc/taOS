import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ToolCallBlock } from "../ToolCallBlock";
import type { ToolCallContentBlock } from "@/apps/MessagesApp";

describe("ToolCallBlock", () => {
  function blockFor(overrides: Partial<ToolCallContentBlock>): ToolCallContentBlock {
    return {
      kind: "tool_call",
      call_id: "c1",
      name: "Bash",
      status: "running",
      ...overrides,
    };
  }

  it("renders a compact card with the tool name", () => {
    const { container } = render(
      <ToolCallBlock block={blockFor({ status: "running", input_preview: "echo hi" })} />,
    );
    expect(container.querySelector('[data-tool-call="true"]')).not.toBeNull();
    expect(container.textContent).toContain("Bash");
    expect(container.textContent).toContain("echo hi");
  });

  it("shows a running spinner when status is running", () => {
    const { container } = render(<ToolCallBlock block={blockFor({ status: "running" })} />);
    const card = container.querySelector('[data-tool-call="true"]');
    expect(card?.getAttribute("data-status")).toBe("running");
    expect(card?.querySelector(".animate-spin")).not.toBeNull();
    expect(card?.querySelector(".lucide-check")).toBeNull();
  });

  it("shows a done check when status is done", () => {
    const { container } = render(<ToolCallBlock block={blockFor({ status: "done" })} />);
    const card = container.querySelector('[data-tool-call="true"]');
    expect(card?.getAttribute("data-status")).toBe("done");
    expect(card?.querySelector(".lucide-check")).not.toBeNull();
    expect(card?.querySelector(".animate-spin")).toBeNull();
  });

  it("shows an error icon when status is error", () => {
    const { container } = render(<ToolCallBlock block={blockFor({ status: "error" })} />);
    const card = container.querySelector('[data-tool-call="true"]');
    expect(card?.getAttribute("data-status")).toBe("error");
    expect(card?.querySelector(".lucide-triangle-alert")).not.toBeNull();
  });

  it("renders input_preview as a preview block", () => {
    const { container } = render(
      <ToolCallBlock
        block={blockFor({ status: "running", input_preview: "ls -la /tmp" })}
      />,
    );
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toContain("ls -la /tmp");
  });

  it("renders result_preview once the call is done", () => {
    const { container } = render(
      <ToolCallBlock block={blockFor({ status: "done", result_preview: "total 0" })} />,
    );
    expect(container.textContent).toContain("total 0");
  });

  it("tints result_preview red when status is error", () => {
    const { container } = render(
      <ToolCallBlock
        block={blockFor({ status: "error", result_preview: "boom" })}
      />,
    );
    const result = container.querySelector('[aria-label="result"]');
    expect(result).not.toBeNull();
    expect(result?.textContent).toContain("boom");
    expect(result).toHaveClass("text-red-300");
  });

  it("does not render result_preview while running", () => {
    const { container } = render(
      <ToolCallBlock
        block={blockFor({ status: "running", result_preview: "nope" })}
      />,
    );
    expect(container.querySelector('[aria-label="result"]')).toBeNull();
  });
});
