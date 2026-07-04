import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { EditView } from "./EditView";
import type { GeneratedImage } from "./types";

/* ------------------------------------------------------------------ */
/*  Trust bug regression: the "Quality" edit tier can silently          */
/*  downgrade to the fast, prompt-ignoring iopaint eraser. The backend  */
/*  response carries `degraded`/`backend`; these tests confirm the UI   */
/*  surfaces that instead of dropping it, and hides "Quality" when its   */
/*  own backend isn't healthy.                                          */
/* ------------------------------------------------------------------ */

// jsdom has no real canvas backend installed; EditView's outpaint path
// exports a throwaway canvas via toDataURL, which would otherwise throw.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.toDataURL = () => "data:image/png;base64,AAAA";
}

const mockImage: GeneratedImage = {
  id: "src.png",
  url: "/images/src.png",
  prompt: "a cat on a rug",
  model: "flux",
  size: 512,
  steps: 4,
  seed: 1,
  guidance: 7.5,
  createdAt: new Date().toISOString(),
};

function makeFetchMock(opts: {
  qualityHealthy: boolean;
  editResponse: Record<string, unknown>;
}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/images/edit/capabilities" && method === "GET") {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            image_editing: true,
            background_removal: true,
            upscale: true,
            image_editing_tiers: {
              quality: opts.qualityHealthy,
              fast: true,
            },
          }),
      } as Response);
    }
    if (url === "/api/images/edit" && method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(opts.editResponse),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

async function applyOutpaint() {
  // The "Extend" tool posts a throwaway mask itself, so it exercises
  // callEdit without needing to paint on the mask canvas.
  fireEvent.click(screen.getByRole("tab", { name: "Extend" }));
  const applyButton = await screen.findByRole("button", { name: "Apply changes" });
  fireEvent.click(applyButton);
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("EditView — quality tier downgrade honesty", () => {
  it("shows a downgrade notice when the result is degraded", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock({
        qualityHealthy: true,
        editResponse: {
          url: "/images/result.png",
          image_ref: "result.png",
          degraded: true,
          backend: "iopaint",
        },
      }),
    );

    render(<EditView image={mockImage} onApplyAdjust={vi.fn()} onEdited={vi.fn()} />);
    await applyOutpaint();

    expect(
      await screen.findByText(/Served by the fast eraser/i),
    ).toBeInTheDocument();
  });

  it("shows no notice when the result is not degraded", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock({
        qualityHealthy: true,
        editResponse: {
          url: "/images/result.png",
          image_ref: "result.png",
          degraded: false,
          backend: "iopaint",
        },
      }),
    );

    const onEdited = vi.fn();
    render(<EditView image={mockImage} onApplyAdjust={vi.fn()} onEdited={onEdited} />);
    await applyOutpaint();

    await waitFor(() => expect(onEdited).toHaveBeenCalled());
    expect(screen.queryByText(/Served by the fast eraser/i)).not.toBeInTheDocument();
  });

  it("disables the Quality tier option when only the fast backend is healthy", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock({
        qualityHealthy: false,
        editResponse: { url: "/images/result.png", image_ref: "result.png" },
      }),
    );

    render(<EditView image={mockImage} onApplyAdjust={vi.fn()} onEdited={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Extend" }));

    // aria-disabled (not the native disabled attribute) so the option stays
    // hoverable/focusable and its "Quality model not installed" title tooltip
    // actually surfaces to the user.
    const qualityOption = await screen.findByRole("radio", { name: "Quality" });
    await waitFor(() =>
      expect(qualityOption).toHaveAttribute("aria-disabled", "true"),
    );
    expect(qualityOption).toHaveAttribute("title", "Quality model not installed");

    // Clicking the disabled option must not switch the tier.
    fireEvent.click(qualityOption);
    expect(qualityOption).toHaveAttribute("aria-checked", "false");

    const fastOption = screen.getByRole("radio", { name: "Fast" });
    expect(fastOption).not.toHaveAttribute("aria-disabled");
  });

  it("leaves the Quality tier enabled when its backend is healthy", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock({
        qualityHealthy: true,
        editResponse: { url: "/images/result.png", image_ref: "result.png" },
      }),
    );

    render(<EditView image={mockImage} onApplyAdjust={vi.fn()} onEdited={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Extend" }));

    const qualityOption = await screen.findByRole("radio", { name: "Quality" });
    await waitFor(() =>
      expect(qualityOption).not.toHaveAttribute("aria-disabled"),
    );
  });
});
