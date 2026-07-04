import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { GenerateView } from "./GenerateView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function chatStreamResponse(text: string): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(JSON.stringify({ delta: text }) + "\n"));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

const VALID_SITE_JSON = JSON.stringify({
  title: "Fresh Cafe",
  theme: { palette: "sand", font: "serif" },
  sections: [
    {
      id: "hero-1",
      type: "hero",
      content: {
        eyebrow: "Now open",
        heading: "Coffee worth the walk",
        subheading: "A neighborhood cafe.",
        ctaLabel: "See the menu",
        image: "",
      },
    },
    {
      id: "footer-1",
      type: "footer",
      content: { businessName: "Fresh Cafe", tagline: "" },
    },
  ],
});

describe("GenerateView", () => {
  it("loads a valid AI-generated Site into the editor with no fallback notice", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => chatStreamResponse(`\`\`\`json\n${VALID_SITE_JSON}\n\`\`\``)),
    );
    const onGenerate = vi.fn();
    render(<GenerateView onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("What are you building?"), {
      target: { value: "a cafe landing page" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Generate site/i }));

    await waitFor(() => expect(onGenerate).toHaveBeenCalledTimes(1));
    const [site, wasAiGenerated] = onGenerate.mock.calls[0];
    expect(site.title).toBe("Fresh Cafe");
    // A real parse must report wasAiGenerated=true so it's tagged ai-generated.
    expect(wasAiGenerated).toBe(true);
    expect(screen.queryByText(/could not be parsed/)).not.toBeInTheDocument();
  });

  it("falls back to matchTemplate and shows a notice when the response has no parseable JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => chatStreamResponse("Sorry, I can't help with that request.")),
    );
    const onGenerate = vi.fn();
    render(<GenerateView onGenerate={onGenerate} />);

    fireEvent.click(screen.getByRole("button", { name: /Generate site/i }));

    await waitFor(() => expect(onGenerate).toHaveBeenCalledTimes(1));
    const [site, wasAiGenerated] = onGenerate.mock.calls[0];
    expect(site.sections.length).toBeGreaterThan(0);
    // A matched-template fallback must report wasAiGenerated=false so the
    // caller tags it user-uploaded, not ai-generated.
    expect(wasAiGenerated).toBe(false);
    expect(screen.getByText(/could not be parsed/)).toBeInTheDocument();
  });

  it("surfaces a stream error instead of silently falling back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, text: async () => "agent unreachable" })),
    );
    const onGenerate = vi.fn();
    render(<GenerateView onGenerate={onGenerate} />);

    fireEvent.click(screen.getByRole("button", { name: /Generate site/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(onGenerate).not.toHaveBeenCalled();
  });
});
