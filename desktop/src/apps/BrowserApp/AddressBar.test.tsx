import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AddressBar, READER_MIN_WORD_COUNT } from "./AddressBar";
import { useBrowserStore } from "@/stores/browser-store";
import * as extractApi from "@/lib/browser-extract-api";

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

  it("does NOT navigate when input starts with @ (PR 4 stub)", () => {
    const navSpy = vi.spyOn(useBrowserStore.getState(), "navigateTab");
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "@john" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(navSpy).not.toHaveBeenCalled();
  });

  it("does NOT navigate when input starts with ! (PR 4 stub)", () => {
    const navSpy = vi.spyOn(useBrowserStore.getState(), "navigateTab");
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "!work" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(navSpy).not.toHaveBeenCalled();
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

describe("AddressBar — reader toggle", () => {
  it("does NOT render reader toggle when readerAvailable is not true", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");
    // readerAvailable is undefined by default after navigateTab

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    expect(
      screen.queryByRole("button", { name: /toggle reader mode/i }),
    ).toBeNull();
  });

  it("renders reader toggle when readerAvailable is true", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");
    useBrowserStore.getState().setTabReader(TEST_WINDOW_ID, tabId, {
      readerAvailable: true,
      readerExtract: {
        title: "Test",
        text: "content",
        html: "<p>content</p>",
        word_count: 300,
      },
    });

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    expect(
      screen.getByRole("button", { name: /toggle reader mode/i }),
    ).toBeInTheDocument();
  });

  it("clicking the reader toggle flips readerActive in the store", () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");
    useBrowserStore.getState().setTabReader(TEST_WINDOW_ID, tabId, {
      readerAvailable: true,
      readerActive: false,
      readerExtract: {
        title: "Test",
        text: "content",
        html: "<p>content</p>",
        word_count: 300,
      },
    });

    const setTabReaderSpy = vi.spyOn(useBrowserStore.getState(), "setTabReader");
    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /toggle reader mode/i }));

    expect(setTabReaderSpy).toHaveBeenCalledWith(
      TEST_WINDOW_ID,
      tabId,
      { readerActive: true },
    );
  });

  it("focusing address bar with readerAvailable=undefined triggers extractReadable", async () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");

    const extractSpy = vi.spyOn(extractApi, "extractReadable").mockResolvedValue({
      title: "Article",
      text: "some content",
      html: "<p>some content</p>",
      word_count: 250,
    });

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    await act(async () => {
      fireEvent.focus(input);
    });

    expect(extractSpy).toHaveBeenCalledWith("personal", "https://a.test/");
  });

  it("stale extract result is discarded when URL changes before the fetch resolves", async () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://original.test/");

    // Deferred promise — we control when it resolves
    let resolveExtract!: (v: Awaited<ReturnType<typeof extractApi.extractReadable>>) => void;
    const deferredPromise = new Promise<Awaited<ReturnType<typeof extractApi.extractReadable>>>(
      (res) => { resolveExtract = res; },
    );
    vi.spyOn(extractApi, "extractReadable").mockReturnValue(deferredPromise);

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;

    // Focus triggers the fetch for https://original.test/
    await act(async () => {
      fireEvent.focus(input);
    });

    // Navigate to a different URL before the fetch resolves
    await act(async () => {
      useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://new.test/");
    });

    // Now resolve the extract with data for the OLD url
    await act(async () => {
      resolveExtract({
        title: "Stale Article",
        text: "stale content",
        html: "<p>stale content</p>",
        word_count: READER_MIN_WORD_COUNT + 50,
      });
    });

    // The tab should NOT have readerExtract set (stale write discarded)
    const tab = useBrowserStore.getState().windows[TEST_WINDOW_ID]?.tabs.find(
      (t) => t.id === tabId,
    );
    expect(tab?.readerExtract).toBeFalsy();
  });

  it("focusing address bar with readerAvailable already set does NOT re-trigger extractReadable", async () => {
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");
    useBrowserStore.getState().setTabReader(TEST_WINDOW_ID, tabId, {
      readerAvailable: true,
      readerExtract: {
        title: "Article",
        text: "content",
        html: "<p>content</p>",
        word_count: 300,
      },
    });

    const extractSpy = vi.spyOn(extractApi, "extractReadable").mockResolvedValue(null);

    render(<AddressBar windowId={TEST_WINDOW_ID} />);
    const input = screen.getByLabelText("Address") as HTMLInputElement;
    fireEvent.focus(input);

    await new Promise((r) => setTimeout(r, 50));
    expect(extractSpy).not.toHaveBeenCalled();
  });
});
