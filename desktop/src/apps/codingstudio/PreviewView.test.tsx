import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { PreviewView } from "./PreviewView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockFetchOnce(response: Partial<Response> & { ok: boolean }) {
  const fetchMock = vi.fn(() => Promise.resolve(response as Response));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("PreviewView", () => {
  it("renders an iframe with the returned HTML as srcDoc on 200", async () => {
    const html = "<html><body>Hello workspace</body></html>";
    mockFetchOnce({
      ok: true,
      status: 200,
      text: () => Promise.resolve(html),
    });

    render(<PreviewView workspaceId="cws-abc123" workspaceName="my-app" />);

    const iframe = await screen.findByTitle("Preview");
    expect(iframe.getAttribute("srcdoc")).toBe(html);
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("shows the no_index empty state on 404", async () => {
    mockFetchOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ error: "no_index" }),
    });

    render(<PreviewView workspaceId="cws-abc123" workspaceName="my-app" />);

    await waitFor(() => {
      expect(screen.getByText(/No index\.html to preview/)).toBeInTheDocument();
    });
    expect(screen.queryByTitle("Preview")).not.toBeInTheDocument();
  });

  it("changes the preview frame width when the device toggle is clicked", async () => {
    const html = "<html><body>Hi</body></html>";
    mockFetchOnce({
      ok: true,
      status: 200,
      text: () => Promise.resolve(html),
    });

    render(<PreviewView workspaceId="cws-abc123" workspaceName="my-app" />);
    await screen.findByTitle("Preview");

    const frame = screen.getByTestId("preview-frame");
    expect(frame.style.width).toBe("100%");

    fireEvent.click(screen.getByRole("button", { name: "phone" }));
    expect(frame.style.width).toBe("390px");

    fireEvent.click(screen.getByRole("button", { name: "tablet" }));
    expect(frame.style.width).toBe("834px");

    fireEvent.click(screen.getByRole("button", { name: "desktop" }));
    expect(frame.style.width).toBe("100%");
  });

  it("shows the no-workspace state when workspaceId is null", () => {
    render(<PreviewView workspaceId={null} />);
    expect(screen.getByText("Select or create a workspace to preview it.")).toBeInTheDocument();
    expect(screen.queryByTitle("Preview")).not.toBeInTheDocument();
  });
});
