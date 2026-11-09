import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  useLayoutStore,
  waitForLayoutSync,
  SAVE_TIMEOUT_MS,
  __resetLayoutStoreForTests,
} from "./layout-store";
import { useNotificationStore } from "@/stores/notification-store";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

const KEY = "taos-layout-fullscreen-launch";
const UNSAVED_KEY = "taos-layout-fullscreen-launch-unsaved";

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response;
}

/** A fetch mock whose promise rejects with AbortError when the request's signal aborts. */
function abortableNeverResolving(): typeof fetch {
  return vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }),
  ) as unknown as typeof fetch;
}

function putBodies(fetchMock: ReturnType<typeof vi.fn>): unknown[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT")
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

describe("layout-store", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    localStorage.clear();
    document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    __resetLayoutStoreForTests();
    useNotificationStore.setState({ notifications: [] });
    useAuthReadyStore.setState({ ready: true });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("defaults to on with empty storage and reads a stored off", () => {
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(true);
    localStorage.setItem(KEY, "off");
    __resetLayoutStoreForTests();
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
  });

  it("setter applies immediately, caches locally and PUTs the preference with the CSRF header", async () => {
    document.cookie = "csrf_token=test-csrf-token";
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("off");
    expect(localStorage.getItem(UNSAVED_KEY)).toBe("1");

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/preferences/layout");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ fullscreen_on_launch: false });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("test-csrf-token");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    // Confirmed by the server: the unsaved marker is cleared.
    await vi.waitFor(() => expect(localStorage.getItem(UNSAVED_KEY)).toBeNull());
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
    expect(putBodies(fetchMock)).toEqual([{ fullscreen_on_launch: false }, { fullscreen_on_launch: true }]);
  });

  it("reports a failed server save once, keeps the local value and leaves it marked unsaved", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: "nope" }, false, 403));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    useLayoutStore.getState().setFullscreenOnLaunch(true);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("on");
    expect(localStorage.getItem(UNSAVED_KEY)).toBe("1");
    const notes = useNotificationStore.getState().notifications;
    expect(notes).toHaveLength(1);
    expect(notes[0]?.level).toBe("warning");
    expect(notes[0]?.body).toContain("HTTP 403");
  });

  it("aborts a stalled PUT after the timeout, reports it, and lets the next save proceed in order", async () => {
    vi.useFakeTimers();
    const fetchMock = abortableNeverResolving();
    globalThis.fetch = fetchMock;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    useLayoutStore.getState().setFullscreenOnLaunch(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1); // second save waits behind the stalled first

    await vi.advanceTimersByTimeAsync(SAVE_TIMEOUT_MS + 1);
    const notes = useNotificationStore.getState().notifications;
    expect(notes).toHaveLength(1);
    expect(notes[0]?.body).toContain("timed out");
    // Chain recovered: the second (latest) choice is sent after the first was aborted.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(putBodies(fetchMock as unknown as ReturnType<typeof vi.fn>)).toEqual([
      { fullscreen_on_launch: false },
      { fullscreen_on_launch: true },
    ]);
  });

  it("drops saves queued before a logout instead of replaying them under the next session", async () => {
    const first = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    useLayoutStore.getState().setFullscreenOnLaunch(false);
    useLayoutStore.getState().setFullscreenOnLaunch(true); // queued behind the first
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1)); // first PUT in flight
    useAuthReadyStore.setState({ ready: false }); // logout while it is in flight
    first.resolve(jsonResponse({ ok: true }));
    await vi.waitFor(() => expect(localStorage.getItem(UNSAVED_KEY)).toBeNull());
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledTimes(1); // the queued save was skipped
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(true); // local choice intact
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

  it("after a failed save, a later sync keeps the unsaved local choice and re-pushes it", async () => {
    // Session 1: save fails, page is then reloaded.
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, false, 500)) as unknown as typeof fetch;
    useLayoutStore.getState().setFullscreenOnLaunch(false);
    await vi.waitFor(() => expect(localStorage.getItem(UNSAVED_KEY)).toBe("1"));
    __resetLayoutStoreForTests(); // simulated reload: state rebuilt from storage
    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);

    // Session 2: server still holds the old "on"; sync must not apply it.
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
      Promise.resolve(init?.method === "PUT" ? jsonResponse({ ok: true }) : jsonResponse({ fullscreen_on_launch: true })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await useLayoutStore.getState().syncFromServer();

    expect(useLayoutStore.getState().fullscreenOnLaunch).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("off");
    await vi.waitFor(() => expect(putBodies(fetchMock)).toEqual([{ fullscreen_on_launch: false }]));
    await vi.waitFor(() => expect(localStorage.getItem(UNSAVED_KEY)).toBeNull());
    expect(fetchMock.mock.calls.some(([, init]) => !(init as RequestInit | undefined)?.method)).toBe(false); // no GET issued
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
