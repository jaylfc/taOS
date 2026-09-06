import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { AssetsPanel } from "./AssetsPanel";

const TEXTURE_URL = "/api/games/g1/assets/texture";

function fetchOnce(body: unknown, ok = true, status = 200) {
  return vi.fn((input: RequestInfo | URL) => {
    expect(String(input)).toBe(TEXTURE_URL);
    return Promise.resolve({
      ok,
      status,
      json: () => Promise.resolve(body),
    } as Response);
  });
}

describe("AssetsPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the prompt input and a disabled Generate button when empty", () => {
    render(<AssetsPanel gameId="g1" activePath="index.html" onInsert={vi.fn()} />);
    expect(screen.getByLabelText("Describe the texture or sprite")).toBeDefined();
    expect((screen.getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("generates a texture and shows a preview + insert action", async () => {
    const onInsert = vi.fn();
    const fetchMock = fetchOnce({
      available: true,
      status: "generated",
      filename: "texture-1-abcd.png",
      path: "/api/games/g1/preview/texture-1-abcd.png",
      kind: "texture",
      tier: "sdxl",
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<AssetsPanel gameId="g1" activePath="index.html" onInsert={onInsert} />);
    fireEvent.change(screen.getByLabelText("Describe the texture or sprite"), {
      target: { value: "a mossy stone wall" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    const img = (await screen.findByAltText("Generated texture")) as HTMLImageElement;
    expect(img.src).toContain("/api/games/g1/preview/texture-1-abcd.png");

    // Insert calls the callback with the generated filename.
    fireEvent.click(screen.getByRole("button", { name: /Insert into index\.html/ }));
    expect(onInsert).toHaveBeenCalledWith("texture-1-abcd.png");
    // Sending the request used the right body.
    const sentBody = JSON.parse(String((fetchMock.mock.calls[0]![1] as RequestInit).body));
    expect(sentBody.prompt).toBe("a mossy stone wall");
    expect(sentBody.kind).toBe("texture");
  });

  it("shows a 'Needs a GPU worker' state when the tier is unavailable", async () => {
    const fetchMock = fetchOnce({ available: false, reason: "No GPU or NPU worker available." });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<AssetsPanel gameId="g1" activePath="index.html" onInsert={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Describe the texture or sprite"), {
      target: { value: "a texture" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(screen.getByText("Needs a GPU worker")).toBeDefined());
    expect(screen.getByText("No GPU or NPU worker available.")).toBeDefined();
    // No preview / insert button in the unavailable state.
    expect(screen.queryByRole("button", { name: /Insert/ })).toBeNull();
  });

  it("surfaces a backend error", async () => {
    const fetchMock = fetchOnce({ error: "Texture generation failed." }, false, 502);
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<AssetsPanel gameId="g1" activePath="index.html" onInsert={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Describe the texture or sprite"), {
      target: { value: "a texture" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Texture generation failed.");
  });

  it("disables insert when no file is open", async () => {
    const fetchMock = fetchOnce({
      available: true,
      status: "generated",
      filename: "texture-1.png",
      path: "/api/games/g1/preview/texture-1.png",
      kind: "texture",
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<AssetsPanel gameId="g1" activePath={null} onInsert={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Describe the texture or sprite"), {
      target: { value: "a texture" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    const insert = (await screen.findByRole("button", { name: /Open a file to insert/ })) as HTMLButtonElement;
    expect(insert.disabled).toBe(true);
  });
});
