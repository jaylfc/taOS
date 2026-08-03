import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { renderContent, dayLabel } from "../../MessagesApp";

describe("renderContent", () => {
  it("passes plain text through", () => {
    const { container } = render(<div>{renderContent("hello world")}</div>);
    expect(container.textContent).toContain("hello world");
  });

  it("renders markdown bold, italic, and inline code", () => {
    const { container } = render(<div>{renderContent("a **bold** b *italic* c `code`")}</div>);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
    expect(container.querySelector("code")?.textContent).toBe("code");
  });

  it("renders a fenced block as a CodeBlock with a copy button", () => {
    const text = "before\n```\nconst x = 1;\n```\nafter";
    const { container } = render(<div>{renderContent(text)}</div>);
    expect(container.textContent).toContain("const x = 1;");
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("preserves text on both sides of a fence in order", () => {
    const text = "INTRO\n```\ncodeword\n```\nOUTRO";
    const { container } = render(<div>{renderContent(text)}</div>);
    const t = container.textContent || "";
    expect(t.indexOf("INTRO")).toBeGreaterThanOrEqual(0);
    expect(t.indexOf("codeword")).toBeGreaterThan(t.indexOf("INTRO"));
    expect(t.indexOf("OUTRO")).toBeGreaterThan(t.indexOf("codeword"));
  });

  it("renders two fenced blocks as two copy buttons", () => {
    const text = "```\nalpha\n```\nmid\n```\nbeta\n```";
    render(<div>{renderContent(text)}</div>);
    expect(screen.getAllByRole("button", { name: /copy/i })).toHaveLength(2);
  });

  it("does not emit React duplicate-key warnings", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const text = "a *one* b *two* c *three*\n```\ncode\n```\nd *four* e *five* f *six*";
    render(<div>{renderContent(text)}</div>);
    const keyWarned = spy.mock.calls.some((args) =>
      args.some((a) => typeof a === "string" && a.includes("key")),
    );
    expect(keyWarned).toBe(false);
    spy.mockRestore();
  });

  it("renders markdown lists and external links (react-markdown)", () => {
    const { container: list } = render(<div>{renderContent("- one\n- two\n- three")}</div>);
    expect(list.querySelectorAll("li").length).toBeGreaterThanOrEqual(3);

    const { container: link } = render(<div>{renderContent("see [the site](https://example.com)")}</div>);
    const a = link.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("target")).toBe("_blank");
  });

  it("dispatches to content_blocks when non-empty", () => {
    const { container } = render(<div>{renderContent("", [{ kind: "text", text: "hello" }])}</div>);
    expect(container.textContent).toContain("unsupported block: text");
  });

  it("falls through to markdown when content_blocks is empty", () => {
    const { container } = render(<div>{renderContent("hello world", [])}</div>);
    expect(container.textContent).toContain("hello world");
  });

  it("renders a tool_call block via the ToolCallBlock card", () => {
    const { container } = render(
      <div>{renderContent("", [{ kind: "tool_call", call_id: "c1", name: "Bash", status: "done" as const, input_preview: "echo hi" }])}</div>,
    );
    const card = container.querySelector('[data-tool-call="true"]');
    expect(card).not.toBeNull();
    expect(card?.getAttribute("data-status")).toBe("done");
    expect(container.textContent).toContain("Bash");
    expect(container.textContent).toContain("echo hi");
  });

  it("renders a status block via the StatusBlock card", () => {
    const { container } = render(<div>{renderContent("", [{ kind: "status", text: "working" }])}</div>);
    const line = container.querySelector('[data-status-block="true"][data-variant="status"]');
    expect(line).not.toBeNull();
    expect(container.textContent).toContain("working");
  });

  it("renders a question block through StatusBlock with a reply hint", () => {
    const { container } = render(<div>{renderContent("", [{ kind: "question", text: "want more?" }])}</div>);
    const line = container.querySelector('[data-status-block="true"][data-variant="question"]');
    expect(line).not.toBeNull();
    expect(container.textContent).toContain("want more?");
    expect(container.textContent).toContain("reply below");
  });

  it("renders unknown fallback for unrecognized block kinds", () => {
    const { container } = render(<div>{renderContent("", [{ kind: "mystery", text: "x" }])}</div>);
    expect(container.textContent).toContain("unsupported block: mystery");
  });

  it("renders unknown fallback for text and thinking (separate cards)", () => {
    const { container } = render(
      <div>{renderContent("", [
        { kind: "text", text: "hi" },
        { kind: "thinking", text: "thinking...", collapsed: true },
      ])}</div>,
    );
    expect(container.textContent).toContain("unsupported block: text");
    expect(container.textContent).toContain("unsupported block: thinking");
  });

  it("renders one fallback line per block for stub kinds", () => {
    const { container } = render(
      <div>{renderContent("", [
        { kind: "text", text: "a" },
        { kind: "thinking", text: "b" },
      ])}</div>,
    );
    const text = container.textContent || "";
    expect(text.match(/unsupported block/g)?.length).toBe(2);
  });
});

describe("dayLabel", () => {
  afterEach(() => vi.useRealTimers());

  it("labels Today, Yesterday, and older dates", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-13T12:00:00"));
    const today = new Date("2026-06-13T08:00:00").getTime();
    const yesterday = new Date("2026-06-12T08:00:00").getTime();
    const older = new Date("2026-06-01T08:00:00").getTime();
    expect(dayLabel(today)).toBe("Today");
    expect(dayLabel(yesterday)).toBe("Yesterday");
    expect(dayLabel(older)).not.toBe("Today");
    expect(dayLabel(older)).not.toBe("Yesterday");
  });
});
