import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { BrowserModeToggle } from "./BrowserModeToggle";
import { useBrowserStore } from "@/stores/browser-store";

const originalFetch = global.fetch;
const WIN_ID = "win-1";

function activeTabId() {
  return useBrowserStore.getState().getWindow(WIN_ID)!.activeTabId;
}

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(WIN_ID, "personal");
  const tabId = activeTabId();
  useBrowserStore.getState().navigateTab(WIN_ID, tabId, "https://example.com/");
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
  cleanup();
});

describe("BrowserModeToggle - segmented engine control", () => {
  it("renders Proxy and Streamed radios with Proxy selected by default", () => {
    render(<BrowserModeToggle windowId={WIN_ID} />);
    const proxy = screen.getByRole("radio", { name: /proxy browser/i });
    const streamed = screen.getByRole("radio", { name: /streamed browser/i });
    expect(proxy.getAttribute("aria-checked")).toBe("true");
    expect(streamed.getAttribute("aria-checked")).toBe("false");
  });

  it("marks Streamed selected when the active tab has a liveSession", () => {
    useBrowserStore.getState().setTabLiveSession(WIN_ID, activeTabId(), {
      nekoUrl: "http://neko.local:8080/room",
      streamToken: "tok-1",
    });
    render(<BrowserModeToggle windowId={WIN_ID} />);
    expect(
      screen.getByRole("radio", { name: /streamed browser/i }).getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("clicking Streamed posts a session and sets liveSession on a running response", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        session: {
          id: "sess-1",
          status: "running",
          neko_url: "http://neko.local:8080/room",
          stream_token: "tok-xyz",
        },
      }),
    } as Response);

    const tabId = activeTabId();
    render(<BrowserModeToggle windowId={WIN_ID} />);
    fireEvent.click(screen.getByRole("radio", { name: /streamed browser/i }));

    await waitFor(() => {
      const tab = useBrowserStore
        .getState()
        .getWindow(WIN_ID)!
        .tabs.find((t) => t.id === tabId);
      expect(tab?.liveSession).toEqual({
        nekoUrl: "http://neko.local:8080/room",
        streamToken: "tok-xyz",
      });
    });
  });

  it("clicking Proxy clears an existing liveSession", () => {
    const tabId = activeTabId();
    useBrowserStore.getState().setTabLiveSession(WIN_ID, tabId, {
      nekoUrl: "http://neko.local:8080/room",
      streamToken: "tok-1",
    });
    render(<BrowserModeToggle windowId={WIN_ID} />);
    fireEvent.click(screen.getByRole("radio", { name: /proxy browser/i }));
    const tab = useBrowserStore
      .getState()
      .getWindow(WIN_ID)!
      .tabs.find((t) => t.id === tabId);
    expect(tab?.liveSession).toBeUndefined();
  });

  it("shows a gate hint when the host has no capable node (409)", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ error: "no_capable_node" }),
    } as Response);

    render(<BrowserModeToggle windowId={WIN_ID} />);
    fireEvent.click(screen.getByRole("radio", { name: /streamed browser/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(
        /streamed browser needs a more capable device/i,
      );
    });
  });
});
