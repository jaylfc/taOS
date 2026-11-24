import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import {
  TextBlock, ThinkingBlock, DiffBlock, TableBlock,
  renderContent, getContentBlockRenderer, registerContentBlockRenderer,
} from "../../MessagesApp";

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

describe("content block renderer registry (slice 2)", () => {
  it("has a renderer registered for every built-in kind", () => {
    for (const kind of [
      "text", "thinking", "tool_call", "status", "question", "decision",
      "code", "diff", "table", "image", "file",
    ]) {
      expect(getContentBlockRenderer(kind)).toBeTypeOf("function");
    }
  });

  it("falls back to the unsupported-block label for unregistered kinds", () => {
    const { container } = render(
      <div>{renderContent("", [{ kind: "mystery" }])}</div>,
    );
    expect(container.textContent).toContain("unsupported block: mystery");
  });

  it("dispatches through a renderer registered at runtime", () => {
    registerContentBlockRenderer("test-custom", (block) => (
      <div>custom:{String((block as { text?: string }).text ?? "")}</div>
    ));
    const { container } = render(
      <div>{renderContent("", [{ kind: "test-custom", text: "hi" }])}</div>,
    );
    expect(container.textContent).toContain("custom:hi");
  });

  it("renders a mixed block list in order", () => {
    const { container } = render(
      <div>
        {renderContent("", [
          { kind: "text", text: "before" },
          { kind: "status", text: "working" },
          { kind: "text", text: "after" },
        ])}
      </div>,
    );
    const text = container.textContent ?? "";
    expect(text.indexOf("before")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("before")).toBeLessThan(text.indexOf("working"));
    expect(text.indexOf("working")).toBeLessThan(text.indexOf("after"));
  });
});

describe("DiffBlock", () => {
  const sampleDiff = [
    "--- a/app.ts",
    "+++ b/app.ts",
    "@@ -1,2 +1,2 @@",
    "-old line",
    "+new line",
    " context",
  ].join("\n");

  it("renders the filename and the added/removed counts", () => {
    const { container } = render(
      <DiffBlock block={{ kind: "diff", filename: "app.ts", diff: sampleDiff }} />,
    );
    expect(container.textContent).toContain("app.ts");
    expect(container.textContent).toContain("+1");
    expect(container.textContent).toContain("-1");
  });

  it("classifies added, removed, hunk and context lines", () => {
    const { container } = render(
      <DiffBlock block={{ kind: "diff", diff: sampleDiff }} />,
    );
    expect(container.querySelector('[data-diff-line="add"]')?.textContent).toBe("+new line");
    expect(container.querySelector('[data-diff-line="del"]')?.textContent).toBe("-old line");
    expect(container.querySelector('[data-diff-line="hunk"]')?.textContent).toContain("@@");
    expect(container.querySelector('[data-diff-line="context"]')?.textContent).toBe(" context");
  });

  it("renders an empty diff as a no-changes note", () => {
    const { container } = render(<DiffBlock block={{ kind: "diff", diff: "" }} />);
    expect(container.textContent).toContain("no changes");
  });

  it("classifies hunk-body lines that look like file headers as add/del", () => {
    // Replacing the line "--old" with "++new" produces hunk-body lines
    // "---old" / "+++new": inside a hunk the first character is the
    // indicator, so these are a deletion and an addition, not headers.
    const tricky = [
      "--- a/app.ts",
      "+++ b/app.ts",
      "@@ -1,1 +1,1 @@",
      "---old",
      "+++new",
    ].join("\n");
    const { container } = render(
      <DiffBlock block={{ kind: "diff", filename: "app.ts", diff: tricky }} />,
    );
    expect(container.querySelectorAll('[data-diff-line="header"]')).toHaveLength(2);
    expect(container.querySelector('[data-diff-line="del"]')?.textContent).toBe("---old");
    expect(container.querySelector('[data-diff-line="add"]')?.textContent).toBe("+++new");
    expect(container.textContent).toContain("+1");
    expect(container.textContent).toContain("-1");
  });
});

describe("TableBlock", () => {
  it("renders caption, headers and rows", () => {
    const { container } = render(
      <TableBlock
        block={{
          kind: "table",
          caption: "Sizes",
          headers: ["Name", "Size"],
          rows: [["a.txt", "3 KB"], ["b.txt", "5 KB"]],
        }}
      />,
    );
    expect(container.textContent).toContain("Sizes");
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("td")).toHaveLength(4);
    expect(container.textContent).toContain("a.txt");
  });

  it("renders rows without headers", () => {
    const { container } = render(
      <TableBlock block={{ kind: "table", rows: [["only"]] }} />,
    );
    expect(container.querySelectorAll("th")).toHaveLength(0);
    expect(container.querySelector("td")?.textContent).toBe("only");
  });
});

describe("code and media blocks through renderContent", () => {
  it("renders a code block with the shared CodeBlock", () => {
    const { container } = render(
      <div>{renderContent("", [{ kind: "code", code: "const x = 1;", language: "ts" }])}</div>,
    );
    expect(container.querySelector("pre")?.textContent).toContain("const x = 1;");
  });

  it("renders an image block as an inline image", () => {
    const { container } = render(
      <div>
        {renderContent("", [
          { kind: "image", url: "https://x.test/pic.png", filename: "pic.png" },
        ])}
      </div>,
    );
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe("https://x.test/pic.png");
    expect(img?.getAttribute("alt")).toBe("pic.png");
  });

  it("falls back to a kind name when the URL basename is empty", () => {
    const { container } = render(
      <div>
        {renderContent("", [
          { kind: "file", url: "https://x.test/dir/" },
          { kind: "image", url: "https://x.test/" },
        ])}
      </div>,
    );
    const link = container.querySelector("a");
    expect(link?.textContent).toContain("file");
    const img = container.querySelector("img");
    expect(img?.getAttribute("alt")).toBe("image");
  });

  it("renders a file block as a download link", () => {
    const { container } = render(
      <div>
        {renderContent("", [
          { kind: "file", url: "https://x.test/report.pdf", filename: "report.pdf", size: 2048 },
        ])}
      </div>,
    );
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://x.test/report.pdf");
    expect(link?.textContent).toContain("report.pdf");
  });
});
