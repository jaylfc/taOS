import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { BrowserApp } from "./BrowserApp";
import { useBrowserStore } from "@/stores/browser-store";
import { useProcessStore } from "@/stores/process-store";

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: vi.fn(() => false),
}));
import { useIsMobile } from "@/hooks/use-is-mobile";

const TEST_WINDOW_ID = "win-test";

const originalFetch = global.fetch;

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useProcessStore.setState({ windows: [], nextZIndex: 1 });
  global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 204 });
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("BrowserApp — composition", () => {
  it("auto-creates the window in browser-store on mount", () => {
    render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    const win = useBrowserStore.getState().getWindow(TEST_WINDOW_ID);
    expect(win).toBeDefined();
    expect(win?.profileId).toBe("personal");
    expect(win?.tabs.length).toBe(1);
  });

  it("renders Chrome (back/forward/refresh + profile chip)", () => {
    render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    expect(screen.getByRole("button", { name: /back/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /forward/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /refresh|reload/i })).toBeTruthy();
    expect(screen.getByLabelText(/profile/i)).toBeTruthy();
  });

  it("renders TabStrip with at least one tab", () => {
    render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    expect(screen.getAllByRole("tab").length).toBeGreaterThanOrEqual(1);
  });

  it("renders TabRenderer with one iframe (the default new-tab)", () => {
    const { container } = render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    expect(container.querySelectorAll("iframe").length).toBe(1);
  });

  it("renders AddressBar input", () => {
    render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    expect(screen.getByLabelText("Address")).toBeTruthy();
  });

  it("does not duplicate window if already in store (idempotent on mount)", () => {
    useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "work");
    render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    const win = useBrowserStore.getState().getWindow(TEST_WINDOW_ID);
    expect(win?.profileId).toBe("work"); // Existing window preserved, NOT overwritten with "personal"
  });
});

describe("BrowserApp — mobile layout", () => {
  beforeEach(() => {
    vi.mocked(useIsMobile).mockReturnValue(true);
  });
  afterEach(() => {
    vi.mocked(useIsMobile).mockReturnValue(false);
  });

  it("renders the iframe inside a flex container so TabRenderer fills height", () => {
    // Regression: the mobile content wrapper must be `display:flex`, otherwise
    // TabRenderer's `flex flex-1` root collapses to 0 height and the page area
    // renders blank on mobile/PWA.
    const { container } = render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    const iframe = container.querySelector("iframe");
    expect(iframe).toBeTruthy();

    // The content wrapper holds TabRenderer (`flex flex-1` root). It is the
    // div that is both `flex-1 relative` and the parent of TabRenderer's root.
    const wrapper = container.querySelector(".flex-1.relative.overflow-hidden");
    expect(wrapper).toBeTruthy();
    // Must be a flex container so TabRenderer's `flex-1` root fills the height.
    expect(wrapper?.classList.contains("flex")).toBe(true);
  });
});

describe("BrowserApp — cleanup on unmount", () => {
  it("calls removeWindow on unmount", () => {
    const removeSpy = vi.spyOn(useBrowserStore.getState(), "removeWindow");
    const { unmount } = render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    expect(useBrowserStore.getState().getWindow(TEST_WINDOW_ID)).toBeDefined();
    unmount();
    expect(removeSpy).toHaveBeenCalledWith(TEST_WINDOW_ID);
  });

  it("calls server deleteWindow on unmount", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    global.fetch = fetchMock;
    const { unmount } = render(<BrowserApp windowId={TEST_WINDOW_ID} />);
    unmount();
    // The fetch call from deleteWindow uses URL with the windowId
    expect(fetchMock).toHaveBeenCalled();
    const [url, opts] = fetchMock.mock.calls.find(
      (c: any) => typeof c[0] === "string" && c[0].includes("/windows/"),
    ) ?? [];
    expect(url).toContain(`/windows/${TEST_WINDOW_ID}`);
    expect(opts?.method).toBe("DELETE");
  });
});

describe("BrowserApp — keyboard hook focus scoping", () => {
  it("does not fire keyboard shortcuts when window is not focused", async () => {
    // Create the BrowserApp window in process-store but not focused
    useProcessStore.setState({
      windows: [
        {
          id: TEST_WINDOW_ID,
          appId: "browser",
          position: { x: 0, y: 0 },
          size: { w: 800, h: 600 },
          zIndex: 1,
          minimized: false,
          maximized: false,
          snapped: null,
          focused: false,
          launchNonce: 0,
        },
      ],
      nextZIndex: 2,
    });

    const addSpy = vi.spyOn(useBrowserStore.getState(), "addTab");
    render(<BrowserApp windowId={TEST_WINDOW_ID} />);

    // Fire Cmd+T — should NOT add a tab because hasFocus=false
    const event = new KeyboardEvent("keydown", { key: "t", metaKey: true, bubbles: true });
    window.dispatchEvent(event);
    expect(addSpy).not.toHaveBeenCalled();
  });
});
