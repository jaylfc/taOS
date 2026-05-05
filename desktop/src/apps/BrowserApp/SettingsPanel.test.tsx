import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { SettingsPanel } from "./SettingsPanel";
import { TabRenderer, DISCARD_TIMEOUT_MS } from "./TabRenderer";
import { useBrowserSettingsStore } from "@/stores/browser-settings-store";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-settings-test";

beforeEach(() => {
  useBrowserSettingsStore.setState({
    discardTimeoutMs: 10 * 60 * 1000,
    maxLiveTabs: 12,
    searchEngine: "duckduckgo",
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SettingsPanel — rendering", () => {
  it("renders with role=dialog and aria-label", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeTruthy();
    expect(dialog.getAttribute("aria-label")).toMatch(/browser settings/i);
  });

  it("renders close button", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    expect(screen.getByRole("button", { name: /close/i })).toBeTruthy();
  });

  it("renders discard timeout slider with accessible label", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const slider = screen.getByRole("slider");
    expect(slider).toBeTruthy();
    expect(slider.getAttribute("aria-label") ?? "").toMatch(/discard timeout/i);
    expect(slider.getAttribute("aria-valuemin")).toBe("1");
    expect(slider.getAttribute("aria-valuemax")).toBe("60");
  });

  it("renders max live tabs number input", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const input = screen.getByRole("spinbutton");
    expect(input).toBeTruthy();
  });

  it("renders search engine dropdown", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const select = screen.getByRole("combobox");
    expect(select).toBeTruthy();
  });
});

describe("SettingsPanel — interactions", () => {
  it("moving slider updates discardTimeoutMs in the store", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const slider = screen.getByRole("slider");
    // slider is in minutes (1–60); setting to 5 → 5*60*1000 ms
    fireEvent.change(slider, { target: { value: "5" } });
    expect(useBrowserSettingsStore.getState().discardTimeoutMs).toBe(5 * 60 * 1000);
  });

  it("slider reflects current store value", () => {
    useBrowserSettingsStore.setState({ discardTimeoutMs: 3 * 60 * 1000 });
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const slider = screen.getByRole("slider");
    expect(slider.getAttribute("aria-valuenow")).toBe("3");
    expect((slider as HTMLInputElement).value).toBe("3");
  });

  it("number input updates maxLiveTabs in the store", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "20" } });
    expect(useBrowserSettingsStore.getState().maxLiveTabs).toBe(20);
  });

  it("number input clamped via store setter: value > 50 becomes 50", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "99" } });
    expect(useBrowserSettingsStore.getState().maxLiveTabs).toBe(50);
  });

  it("dropdown updates searchEngine in the store", () => {
    render(<SettingsPanel profileId="prof-test" onClose={() => {}} />);
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "google" } });
    expect(useBrowserSettingsStore.getState().searchEngine).toBe("google");
  });

  it("close button calls onClose", () => {
    const onClose = vi.fn();
    render(<SettingsPanel profileId="prof-test" onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("SettingsPanel — TabRenderer wire-through (discard scheduler reads from store)", () => {
  beforeEach(() => {
    useBrowserStore.setState({ windows: {} });
    useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
    vi.useFakeTimers();
  });

  it("discards a tab idle past the CUSTOM timeout (shorter than default)", () => {
    // Set a 2-minute custom timeout
    const customTimeoutMs = 2 * 60 * 1000;
    useBrowserSettingsStore.setState({ discardTimeoutMs: customTimeoutMs });

    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().addTab(TEST_WINDOW_ID, "https://b.test/");

    // tabA idle past the custom timeout but NOT past the default (10 min)
    // This proves TabRenderer reads from the store, not the DISCARD_TIMEOUT_MS constant
    const idleTime = customTimeoutMs + 1000; // older than custom, newer than default
    expect(idleTime).toBeLessThan(DISCARD_TIMEOUT_MS); // sanity check
    useBrowserStore.setState((s) => {
      const win = s.windows[TEST_WINDOW_ID];
      const tabs = win.tabs.map((t) =>
        t.id === tabA
          ? { ...t, lastActiveAt: Date.now() - idleTime }
          : t,
      );
      return { windows: { ...s.windows, [TEST_WINDOW_ID]: { ...win, tabs } } };
    });

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    const tab = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs.find(
      (t) => t.id === tabA,
    );
    expect(tab?.state).toBe("discarded");
  });

  it("does NOT discard tab idle past custom timeout when custom timeout is longer than idle time", () => {
    // Set a very long custom timeout (30 minutes)
    useBrowserSettingsStore.setState({ discardTimeoutMs: 30 * 60 * 1000 });

    const tabA = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
    useBrowserStore.getState().addTab(TEST_WINDOW_ID, "https://b.test/");

    // tabA idle for only 11 minutes — past default (10 min) but within new 30 min timeout
    useBrowserStore.setState((s) => {
      const win = s.windows[TEST_WINDOW_ID];
      const tabs = win.tabs.map((t) =>
        t.id === tabA
          ? { ...t, lastActiveAt: Date.now() - 11 * 60 * 1000 }
          : t,
      );
      return { windows: { ...s.windows, [TEST_WINDOW_ID]: { ...win, tabs } } };
    });

    render(<TabRenderer windowId={TEST_WINDOW_ID} />);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    const tab = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs.find(
      (t) => t.id === tabA,
    );
    // Should NOT be discarded yet — only 11 min idle, 30 min timeout
    expect(tab?.state).toBe("live");
  });
});
