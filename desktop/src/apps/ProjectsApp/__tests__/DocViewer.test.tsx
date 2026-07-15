import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

// Mermaid is lazy-loaded and pulls a heavy lib; stub it so the suite stays
// fast and deterministic (no real mermaid chunk / dynamic import in jsdom).
vi.mock("../files/MermaidBlock", () => ({
  default: ({ code }: { code: string }) => <div data-testid="mermaid-block">{code}</div>,
}));

import { DocViewer } from "../files/DocViewer";

const SAMPLE = `# Title

Some intro paragraph with a [link](https://example.com).

## Section A

- one
- two
- three

### Subsection

> a blockquote line

\`\`\`js
const x = 1;
\`\`\`

| Name | Value |
| ---- | ----- |
| alpha | 1 |
| beta | 2 |

## Section B

\`\`\`mermaid
graph TD; A-->B;
\`\`\`
`;

beforeEach(() => {
  vi.restoreAllMocks();
  global.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    text: async () => SAMPLE,
  })) as unknown as typeof fetch;
});

describe("DocViewer — slice 1 markdown reader", () => {
  it("fetches the document from the provided url", async () => {
    render(<DocViewer url="/api/projects/p1/files/doc.md" title="doc.md" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: "Title" })).toBeInTheDocument(),
    );


    // list
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("three")).toBeInTheDocument();

    // blockquote
    expect(screen.getByText("a blockquote line")).toBeInTheDocument();

    // table
    const table = screen.getByRole("table");
    expect(within(table).getByText("Name")).toBeInTheDocument();
    expect(within(table).getByText("beta")).toBeInTheDocument();

    // link (relative to full href)
    const link = screen.getByRole("link", { name: "link" });
    expect(link).toHaveAttribute("href", "https://example.com");

    // fenced code block (rehype-highlight tokenises it into spans, so assert
    // on the rendered <pre> text content rather than a single text node)
    const codeBlock = await waitFor(() => {
      const el = document.querySelector("pre");
      if (!el) throw new Error("no pre yet");
      return el;
    });
    expect(codeBlock.textContent).toContain("const x = 1;");
  });

  it("renders a lazily-loaded mermaid block", async () => {
    render(<DocViewer url="/api/projects/p1/files/doc.md" title="doc.md" />);
    await waitFor(() => expect(screen.getByTestId("mermaid-block")).toBeInTheDocument());
    expect(screen.getByTestId("mermaid-block")).toHaveTextContent("graph TD; A-->B;");
  });

  it("clicking an outline item scrolls to the heading", async () => {
    const scrollSpy = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    render(<DocViewer url="/api/projects/p1/files/doc.md" title="doc.md" />);
    const outline = await waitFor(() => screen.findByLabelText("Document outline"));

    fireEvent.click(within(outline).getByText("Section B"));

    expect(scrollSpy).toHaveBeenCalled();
  });

  it("shows an error when the fetch fails", async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      text: async () => "",
    })) as unknown as typeof fetch;

    render(<DocViewer url="/api/projects/p1/files/missing.md" title="missing.md" />);
    await waitFor(() =>
      expect(screen.getByText(/Failed to load document \(404\)/)).toBeInTheDocument(),
    );
  });

  it("invokes onClose when Close is pressed", async () => {
    const onClose = vi.fn();
    render(<DocViewer url="/api/projects/p1/files/doc.md" title="doc.md" onClose={onClose} />);
    await waitFor(() => expect(screen.getByText("Title")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Close document"));
    expect(onClose).toHaveBeenCalled();
  });
});
