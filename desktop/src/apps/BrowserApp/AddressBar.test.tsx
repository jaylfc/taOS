import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AddressBar } from "./AddressBar";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";
const originalFetch = global.fetch;

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ suggestions: [] }),
  });
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("AddressBar — input behavior", () => {
  it("renders an input with the active tab's URL", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    expect(input.value).toBe("https://a.test/");
  });

  it("typing updates the input but does not navigate yet", () => {
    const navSpy = vi.spyOn(useBrowserStore.getState(), "navigateTab");
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "github.com" } });
    expect(input.value).toBe("github.com");
    expect(navSpy).not.toHaveBeenCalled();
  });

  it("Enter commits the navigation with https:// prepended for a bare domain", () => {
    const navSpy = vi.spyOn(useBrowserStore.getState(), "navigateTab");
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "github.com" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(navSpy).toHaveBeenCalledWith(
      TEST_WINDOW_ID,
      expect.any(String),
      "https://github.com",
    );
  });

  it("Enter on a search query routes to DuckDuckGo", () => {
    const navSpy = vi.spyOn(useBrowserStore.getState(), "navigateTab");
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "playwright tests" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(navSpy).toHaveBeenCalled();
    const navUrl = navSpy.mock.calls[0][2];
    expect(navUrl).toContain("duckduckgo.com");
    expect(navUrl).toContain("playwright");
  });

  it("Escape reverts the input to the tab's URL", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "garbage" } });
    expect(input.value).toBe("garbage");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("https://a.test/");
  });
});

describe("AddressBar — suggest popover", () => {
  it("debounced fetch fires after typing 2+ chars (focused)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        suggestions: [
          { url: "https://a.test/", title: "A", source: "history", score: 1 },
        ],
      }),
    });
    global.fetch = fetchMock;

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ab" } });

    // Wait for the debounced fetch
    await waitFor(() => expect(fetchMock).toHaveBeenCalled(), {
      timeout: 500,
    });
  });

  it("@-prefixed query does NOT fire suggest fetch (PR 4 stub)", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock;

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "@john" } });

    // Give the debounce a chance to fire
    await new Promise((r) => setTimeout(r, 250));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("AddressBar — graceful handling", () => {
  it("renders nothing when window doesn't exist", () => {
    const { container } = render(<AddressBar windowId="missing" />);
    expect(container.querySelector("input")).toBeNull();
  });
});

describe("AddressBar — focus event", () => {
  it("focuses the input when taos-browser:focus-address fires for this window", async () => {
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address");
    expect(document.activeElement).not.toBe(input);

    window.dispatchEvent(
      new CustomEvent("taos-browser:focus-address", {
        detail: { windowId: TEST_WINDOW_ID },
      }),
    );

    // React batches the focus call into the effect cycle
    await new Promise((r) => setTimeout(r, 0));
    expect(document.activeElement).toBe(input);
  });

  it("ignores focus events for other windows", async () => {
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address");
    expect(document.activeElement).not.toBe(input);

    window.dispatchEvent(
      new CustomEvent("taos-browser:focus-address", {
        detail: { windowId: "other-window" },
      }),
    );

    await new Promise((r) => setTimeout(r, 0));
    expect(document.activeElement).not.toBe(input);
  });
});
