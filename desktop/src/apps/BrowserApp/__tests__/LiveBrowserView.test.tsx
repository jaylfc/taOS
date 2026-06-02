import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { LiveBrowserView } from "../LiveBrowserView";
import { EscalateButton } from "../EscalateButton";
import { useBrowserStore } from "@/stores/browser-store";

const originalFetch = global.fetch;

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow("win-1", "personal");
  const tabId = useBrowserStore.getState().getWindow("win-1")!.tabs[0].id;
  useBrowserStore.getState().navigateTab("win-1", tabId, "https://example.com/");
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
  cleanup();
});

// ── Test A: LiveBrowserView renders iframe with nekoUrl + streamToken ──────────

describe("LiveBrowserView", () => {
  it("renders an iframe whose src contains the nekoUrl and streamToken", () => {
    const { container } = render(
      <LiveBrowserView
        nekoUrl="http://node:8080/room"
        streamToken="tok123"
      />,
    );
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    expect(iframe!.src).toContain("http://node:8080/room");
    expect(iframe!.src).toContain("tok123");
  });

  it("renders with title 'Full browser'", () => {
    const { container } = render(
      <LiveBrowserView nekoUrl="http://node:8080/room" streamToken="tok123" />,
    );
    const iframe = container.querySelector("iframe");
    expect(iframe!.title).toBe("Full browser");
  });

  it("fills its container (100% width and height, no border)", () => {
    const { container } = render(
      <LiveBrowserView nekoUrl="http://node:8080/room" streamToken="tok123" />,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe.style.width).toBe("100%");
    expect(iframe.style.height).toBe("100%");
    // jsdom normalises "border:none" → borderWidth=0, borderStyle=none
    expect(iframe.style.borderStyle).toBe("none");
  });
});

// ── Test B: EscalateButton gate banner on 409 no_capable_node ─────────────────

describe("EscalateButton — no-node gate", () => {
  it("shows the gate banner when POST returns 409 no_capable_node", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ error: "no_capable_node" }),
    } as Response);

    const tabUrl = useBrowserStore.getState().getWindow("win-1")!.tabs[0].url;
    render(<EscalateButton tabUrl={tabUrl} />);

    fireEvent.click(screen.getByRole("button", { name: /open in full browser/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/A full browser needs a more capable device/i),
      ).toBeTruthy();
    });

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(
      "A full browser needs a more capable device on your taOS. Add one to enable this.",
    );
  });

  it("gate banner is dismissible", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ error: "no_capable_node" }),
    } as Response);

    render(<EscalateButton tabUrl="https://example.com/" />);
    fireEvent.click(screen.getByRole("button", { name: /open in full browser/i }));

    await waitFor(() => screen.getByRole("alert"));
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows 'Starting full browser…' state after a successful 201", async () => {
    // First call: POST 201 (session pending). Second call: GET poll — stays pending,
    // but we only care about the "Starting full browser…" UI transition here.
    global.fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({ session: { id: "sess-1", status: "pending", neko_url: null } }),
      } as Response)
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ id: "sess-1", status: "pending", neko_url: null }),
      } as Response);

    render(<EscalateButton tabUrl="https://example.com/" />);
    fireEvent.click(screen.getByRole("button", { name: /open in full browser/i }));

    await waitFor(() => {
      expect(screen.getByText(/Starting full browser/i)).toBeTruthy();
    });
  });
});
