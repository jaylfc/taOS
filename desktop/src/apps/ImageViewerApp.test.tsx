import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ImageViewerApp } from "./ImageViewerApp";

// Follows the mockFetch + flush pattern from
// desktop/src/apps/ProjectsApp/ProjectDecisions.test.tsx. ImageViewerApp does
// not fetch on mount, but we stub fetch so any unrelated on-mount integration
// in the shared environment cannot interfere with the assertions.
function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function makeFile(name: string) {
  return new File([new ArrayBuffer(8)], name, { type: "image/png" });
}

// The empty-state input is unmounted once an image is loaded, so the live
// input must be re-queried after each render rather than reused.
function getFileInput(container: HTMLElement) {
  return container.querySelector('input[type="file"]') as HTMLInputElement;
}

describe("ImageViewerApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", mockFetch({ "*": { ok: true, body: {} } }));
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock-url");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  });

  it("shows the empty state before an image is chosen", () => {
    render(<ImageViewerApp windowId="win-1" />);
    expect(screen.getByText(/no image loaded/i)).toBeInTheDocument();
    expect(screen.getByText(/open image/i)).toBeInTheDocument();
  });

  it("loads a selected file, shows its name, and reveals the toolbar", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    fireEvent.change(input, { target: { files: [makeFile("sunset.png")] } });

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(screen.getByText("sunset.png")).toBeInTheDocument();
    expect(screen.getByLabelText(/zoom in/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/zoom out/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/rotate 90 degrees/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/fit to view/i)).toBeInTheDocument();
  });

  it("starts at 100% zoom and zooms in by 25% steps", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("a.png")] } });

    expect(
      screen.getByLabelText(/zoom level 100%/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/zoom in/i));
    expect(
      screen.getByLabelText(/zoom level 125%/i),
    ).toBeInTheDocument();
  });

  it("zooms out and never drops below the minimum zoom", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("a.png")] } });

    fireEvent.click(screen.getByLabelText(/zoom out/i));
    expect(
      screen.getByLabelText(/zoom level 75%/i),
    ).toBeInTheDocument();

    // Crank zoom out well past the MIN_ZOOM floor (0.1 -> 10%).
    for (let i = 0; i < 20; i++) {
      fireEvent.click(screen.getByLabelText(/zoom out/i));
    }
    expect(
      screen.getByLabelText(/zoom level 10%/i),
    ).toBeInTheDocument();
  });

  it("clamps zoom at the maximum (500%)", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("a.png")] } });

    for (let i = 0; i < 40; i++) {
      fireEvent.click(screen.getByLabelText(/zoom in/i));
    }
    expect(
      screen.getByLabelText(/zoom level 500%/i),
    ).toBeInTheDocument();
  });

  it("rotates the image 90 degrees clockwise on each click", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("a.png")] } });

    const img = container.querySelector("img") as HTMLImageElement;
    fireEvent.click(screen.getByLabelText(/rotate 90 degrees/i));
    expect(img.style.transform).toContain("rotate(90deg)");
    fireEvent.click(screen.getByLabelText(/rotate 90 degrees/i));
    expect(img.style.transform).toContain("rotate(180deg)");
  });

  it("resets zoom and rotation when a new image is selected", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("first.png")] },
    });

    fireEvent.click(screen.getByLabelText(/zoom in/i));
    fireEvent.click(screen.getByLabelText(/rotate 90 degrees/i));
    expect(screen.getByLabelText(/zoom level 125%/i)).toBeInTheDocument();

    // The empty-state input is unmounted after the first load, so re-query
    // the now-mounted toolbar input for the second selection.
    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("second.png")] },
    });

    expect(screen.getByText("second.png")).toBeInTheDocument();
    expect(
      screen.getByLabelText(/zoom level 100%/i),
    ).toBeInTheDocument();
    const img = container.querySelector("img") as HTMLImageElement;
    expect(img.style.transform).toContain("rotate(0deg)");
  });

  it("revokes the previous object URL when switching images", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);

    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("first.png")] },
    });
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("second.png")] },
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("ignores a file picker cancel (no files)", () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    fireEvent.change(input, { target: { files: [] } });

    expect(screen.getByText(/no image loaded/i)).toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("does not crash when fit to view runs with no measurable image", async () => {
    const { container } = render(<ImageViewerApp windowId="win-1" />);
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("a.png")] } });

    await flush();
    expect(() =>
      fireEvent.click(screen.getByLabelText(/fit to view/i)),
    ).not.toThrow();
  });
});
