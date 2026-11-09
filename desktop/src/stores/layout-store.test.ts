import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  useLayoutStore,
  waitForLayoutSync,
  __resetLayoutStoreForTests,
} from "./layout-store";
import { useNotificationStore } from "@/stores/notification-store";

const KEY = "taos-layout-fullscreen-launch";

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response;
}

describe("layout-store", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    localStorage.clear();
    __resetLayoutStoreForTests();
    useNotificationStore.setState({ notifications: [] });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("defaults to on with empty storage and reads a stored off", () => {
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(true);
    localStorage.setItem(KEY, "off");
    __resetLayoutStoreForTests();
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
  });

  it("setter applies immediately, caches locally and PUTs the preference", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("off");

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/preferences/layout");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ fullscreen_on_launch: false });
  });

  it("serializes rapid changes so the last choice is PUT last", async () => {
    const first = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    useLayoutStore.getState().setFullscreenOnLaunch(true);
    await Promise.resolve();
    // Second PUT must not start while the first is still in flight.
    expect(fetchMock).toHaveBeenCalledTimes(1);

    first.resolve(jsonResponse({ ok: true }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const bodies = fetchMock.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(bodies).toEqual([{ fullscreen_on_launch: false }, { fullscreen_on_launch: true }]);
  });

  it("reports a failed server save once and keeps the local value", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: "nope" }, false, 403));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    useLayoutStore.getState().setFullscreenOnLaunch(true);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("on");
    const notes = useNotificationStore.getState().notifications;
    expect(notes).toHaveLength(1);
    expect(notes[0]?.level).toBe("warning");
    expect(notes[0]?.body).toContain("HTTP 403");
  });

  it("syncFromServer applies the server value and marks the sync done", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ fullscreen_on_launch: false })) as unknown as typeof fetch;
    const p = useLayoutStore.getState().syncFromServer();
    expect(useLayoutStore.getState().syncState).toBe("pending");
    await p;
    expect(useLayoutStore.getState().syncState).toBe("done");
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("off");
  });

  it("discards a server response made stale by a local change during the request", async () => {
    const get = deferred<Response>();
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
      init?.method === "PUT" ? Promise.resolve(jsonResponse({ ok: true })) : get.promise,
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const p = useLayoutStore.getState().syncFromServer();
    useLayoutStore.getState().setFullscreenOnLaunch(false); // user changes while GET in flight
    get.resolve(jsonResponse({ fullscreen_on_launch: true })); // stale server copy
    await p;

    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("off");
  });

  it("keeps the local value when the server is unreachable", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) as unknown as typeof fetch;
    await useLayoutStore.getState().syncFromServer();
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(true);
    expect(useLayoutStore.getState().syncState).toBe("done");
  });

  it("waitForLayoutSync resolves immediately when no sync is pending", async () => {
    await expect(waitForLayoutSync(50)).resolves.toBeUndefined();
  });

  it("waitForLayoutSync resolves when a pending sync finishes, or on timeout", async () => {
    const get = deferred<Response>();
    globalThis.fetch = vi.fn().mockReturnValue(get.promise) as unknown as typeof fetch;
    void useLayoutStore.getState().syncFromServer();

    let resolved = false;
    const wait = waitForLayoutSync(10_000).then(() => { resolved = true; });
    await Promise.resolve();
    expect(resolved).toBe(false);
    get.resolve(jsonResponse({ fullscreen_on_launch: true }));
    await wait;
    expect(resolved).toBe(true);

    // Timeout path: a sync that never answers must not block the caller.
    __resetLayoutStoreForTests();
    globalThis.fetch = vi.fn().mockReturnValue(new Promise(() => {})) as unknown as typeof fetch;
    void useLayoutStore.getState().syncFromServer();
    await expect(waitForLayoutSync(20)).resolves.toBeUndefined();
  });
});
