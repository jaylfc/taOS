import { afterEach, describe, expect, it, vi } from "vitest";
import { loadWindows, saveWindows, deleteWindow } from "./browser-windows-api";
import type { BrowserWindowState } from "@/apps/BrowserApp/types";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("loadWindows", () => {
  it("returns the windows array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ windows: [{ window_id: "win-1" }] }),
    });
    const result = await loadWindows();
    expect(result).toEqual([{ window_id: "win-1" }]);
  });

  it("returns [] on 401 silently", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: "unauthorized" }),
    });
    const result = await loadWindows();
    expect(result).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network down"));
    const result = await loadWindows();
    expect(result).toEqual([]);
  });
});

describe("saveWindows", () => {
  it("PUTs JSON body with window list", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    const windows: Record<string, BrowserWindowState> = {
      "win-1": {
        windowId: "win-1",
        profileId: "personal",
        tabs: [],
        activeTabId: "tab-a",
        recentlyClosed: [],
      },
    };

    await saveWindows(windows);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/windows");
    expect(opts.method).toBe("PUT");
    const body = JSON.parse(opts.body);
    expect(body.windows).toHaveLength(1);
    expect(body.windows[0].window_id).toBe("win-1");
    expect(body.windows[0].profile_id).toBe("personal");
    expect(body.windows[0].active_tab_id).toBe("tab-a");
    // state is a JSON string
    const state = JSON.parse(body.windows[0].state);
    expect(state.tabs).toEqual([]);
  });

  it("silently no-ops on 401", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    await saveWindows({});
    expect(warnSpy).not.toHaveBeenCalled();
  });
});

describe("deleteWindow", () => {
  it("issues a DELETE to the keyed URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    global.fetch = fetchMock;

    await deleteWindow("win-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/windows/win-1");
    expect(opts.method).toBe("DELETE");
  });

  it("URL-encodes the window_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    global.fetch = fetchMock;
    await deleteWindow("win/with/slash");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/windows/win%2Fwith%2Fslash");
  });
});
