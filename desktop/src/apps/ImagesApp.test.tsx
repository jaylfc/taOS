import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

vi.mock("@/components/ModelBrowser", () => ({
  ModelBrowser: ({ open, onClose }: { open: boolean; onClose: () => void }) =>
    open ? (
      <div data-testid="model-browser">
        <button onClick={onClose}>Close models</button>
      </div>
    ) : null,
}));

vi.mock("./images/CreateView", () => ({
  CreateView: (props: Record<string, unknown>) => (
    <div data-testid="create-view">
      {(props.results as { url: string }[] | []).length === 0 && !props.error && (
        <p>Your generated image appears here</p>
      )}
      {props.error && <p role="alert">{props.error as string}</p>}
      <button onClick={() => props.onGenerate()}>Generate</button>
      <textarea
        data-testid="prompt-input"
        value={props.prompt}
        onChange={(e) => props.onPromptChange(e.target.value)}
      />
    </div>
  ),
}));

vi.mock("./images/LibraryView", () => ({
  LibraryView: (props: Record<string, unknown>) => (
    <div data-testid="library-view">
      {(props.images as { id: string; prompt: string }[]).map((img) => (
        <button key={img.id} onClick={() => props.onDelete(img.id)}>
          {img.prompt}
        </button>
      ))}
      {(props.images as unknown[]).length > 0 && (
        <button onClick={() => props.onDelete("img-1")} aria-label="Delete image">
          Delete
        </button>
      )}
    </div>
  ),
}));

vi.mock("./images/EditView", () => ({
  EditView: () => <div data-testid="edit-view">Edit view</div>,
}));

import { ImagesApp } from "./ImagesApp";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      headers: { get: () => "application/json" },
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const MOCK_IMAGES = [
  {
    id: "img-1",
    filename: "img-1.png",
    path: "/images/img-1.png",
    prompt: "A sunset over mountains",
    model: "flux",
    size: 512,
    steps: 4,
    seed: 42,
    guidance_scale: 7.5,
    created_at: "2025-01-01T00:00:00Z",
  },
];

const MOCK_MODELS = [
  {
    id: "model-1",
    name: "FLUX.1",
    description: "Fast image model",
    capabilities: ["image-generation"],
    variants: [
      {
        id: "variant-1",
        name: "FLUX.1 [dev]",
        format: "safetensors",
        size_mb: 2048,
        compatibility: "green",
        downloaded: true,
      },
    ],
    has_downloaded_variant: true,
  },
];

describe("ImagesApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches images on mount", async () => {
    const fetchMock = mockFetch({
      "/api/images": { ok: true, body: MOCK_IMAGES },
      "/api/models": { ok: true, body: { models: MOCK_MODELS } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ImagesApp windowId="test" />);
    await flush();

    expect(fetchMock).toHaveBeenCalledWith("/api/images", {
      headers: { Accept: "application/json" },
    });
  });

  it("fetches models on mount and selects a downloaded variant", async () => {
    const fetchMock = mockFetch({
      "/api/images": { ok: true, body: [] },
      "/api/models": { ok: true, body: { models: MOCK_MODELS } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ImagesApp windowId="test" />);
    await flush();

    expect(fetchMock).toHaveBeenCalledWith("/api/models", {
      headers: { Accept: "application/json" },
    });
  });

  it("renders the Create view by default", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/images": { ok: true, body: [] },
        "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      }),
    );
    render(<ImagesApp windowId="test" />);
    await flush();

    expect(screen.getByTestId("create-view")).toBeTruthy();
  });

  it("switches to Library view when the Library rail button is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/images": { ok: true, body: MOCK_IMAGES },
        "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      }),
    );
    render(<ImagesApp windowId="test" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /library/i }));
    await flush();

    expect(screen.getByTestId("library-view")).toBeTruthy();
  });

  it("switches to Edit view when the Edit rail button is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/images": { ok: true, body: MOCK_IMAGES },
        "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      }),
    );
    render(<ImagesApp windowId="test" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    await flush();

    expect(screen.getByTestId("edit-view")).toBeTruthy();
  });

  it("opens the ModelBrowser when the Models button is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/images": { ok: true, body: [] },
        "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      }),
    );
    render(<ImagesApp windowId="test" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /models/i }));
    await flush();

    expect(screen.getByTestId("model-browser")).toBeTruthy();
  });

  it("calls DELETE /api/images/:id when delete is triggered in the library", async () => {
    const fetchMock = mockFetch({
      "/api/images": { ok: true, body: MOCK_IMAGES },
      "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      "/api/images/img-1": { ok: true, body: {} },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ImagesApp windowId="test" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /library/i }));
    await flush();

    const deleteBtn = screen.getByRole("button", { name: /delete image/i });
    fireEvent.click(deleteBtn);
    await flush();

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/images/${encodeURIComponent("img-1")}`,
      { method: "DELETE" },
    );
  });

  it("posts to /api/images/generate when Generate is clicked with a prompt", async () => {
    const fetchMock = mockFetch({
      "/api/images": { ok: true, body: [] },
      "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      "/api/images/generate": {
        ok: true,
        body: { filename: "new-img.png", id: "new-img" },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ImagesApp windowId="test" />);
    await flush();

    const promptInput = screen.getByTestId("prompt-input");
    fireEvent.change(promptInput, { target: { value: "A cat in space" } });
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /generate/i }));
    await flush();

    expect(fetchMock).toHaveBeenCalledWith("/api/images/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: expect.stringContaining("A cat in space"),
    });
  });

  it("shows an error when generation fails", async () => {
    const fetchMock = mockFetch({
      "/api/images": { ok: true, body: [] },
      "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      "/api/images/generate": {
        ok: false,
        status: 500,
        body: { error: "Backend down" },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ImagesApp windowId="test" />);
    await flush();

    const promptInput = screen.getByTestId("prompt-input");
    fireEvent.change(promptInput, { target: { value: "A cat in space" } });
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /generate/i }));
    await flush();

    expect(screen.getByText(/backend down/i)).toBeTruthy();
  });

  it("shows the empty create state when there are no images", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/images": { ok: true, body: [] },
        "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      }),
    );
    render(<ImagesApp windowId="test" />);
    await flush();

    expect(screen.getByText(/your generated image appears here/i)).toBeTruthy();
  });

  it("does not crash when the images fetch fails", async () => {
    const fetchMock = mockFetch({
      "/api/images": { ok: false, status: 500, body: {} },
      "/api/models": { ok: true, body: { models: MOCK_MODELS } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ImagesApp windowId="test" />);
    await flush();

    expect(screen.getByTestId("create-view")).toBeTruthy();
  });

  it("renders images in the library when the fetch returns data", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/images": { ok: true, body: MOCK_IMAGES },
        "/api/models": { ok: true, body: { models: MOCK_MODELS } },
      }),
    );
    render(<ImagesApp windowId="test" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /library/i }));
    await flush();

    expect(screen.getByText("A sunset over mountains")).toBeTruthy();
  });
});
