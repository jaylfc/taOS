import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useProcessStore } from "@/stores/process-store";
import {
  _resetMobileShellForTests,
  primeDeviceClass,
  SHELL_LAUNCH_TIMEOUT_MS,
  SHELL_LAUNCH_URL,
} from "./mobile-shell";

// Every test drives the real choke point, useProcessStore.openWindow, with a
// fetch stub that answers the config endpoint and the device shell separately.

const CONFIG_URL = "/api/taos-agent/config";
const SIZE = { w: 800, h: 600 };

type ShellAnswer = () => Promise<Response>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(deviceClass: string | null, shell: ShellAnswer) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === CONFIG_URL) {
      return Promise.resolve(jsonResponse({ model: null, permitted_models: [], device_class: deviceClass }));
    }
    if (url === SHELL_LAUNCH_URL) return shell();
    return Promise.reject(new Error(`unexpected fetch ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function shellCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([u]) => String(u) === SHELL_LAUNCH_URL);
}

function configCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([u]) => String(u) === CONFIG_URL);
}

function windows() {
  return useProcessStore.getState().windows;
}

async function settle() {
  // Let the launch promise chain (fetch -> json -> fallback) run out.
  for (let i = 0; i < 10; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
}

describe("mobile shell launch (via process-store openWindow)", () => {
  let activations: string[];
  const onActivate = (e: Event) => activations.push((e as CustomEvent<{ windowId: string }>).detail.windowId);

  beforeEach(() => {
    _resetMobileShellForTests();
    useProcessStore.setState({ windows: [], nextZIndex: 1 });
    activations = [];
    window.addEventListener("taos:activate-window", onActivate);
  });

  afterEach(() => {
    window.removeEventListener("taos:activate-window", onActivate);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("not mobile: never contacts the shell and opens in-page synchronously", async () => {
    const fetchMock = stubFetch("desktop", () => Promise.resolve(jsonResponse({ ok: true })));
    await primeDeviceClass();
    const wid = useProcessStore.getState().openWindow("files", SIZE);
    expect(wid).not.toBe("");
    expect(windows().map((w) => w.appId)).toEqual(["files"]);
    await settle();
    expect(shellCalls(fetchMock)).toHaveLength(0);
  });

  it("device class unknown (not primed): opens in-page and fetches nothing", async () => {
    const fetchMock = stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true })));
    useProcessStore.getState().openWindow("files", SIZE);
    await settle();
    expect(windows()).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("mobile + shell ok: POSTs the registry id to the shell and opens NO in-page window", async () => {
    const fetchMock = stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true })));
    await primeDeviceClass();
    const wid = useProcessStore.getState().openWindow("coding-studio", SIZE);
    expect(wid).toBe("");
    await settle();
    expect(windows()).toHaveLength(0);
    expect(activations).toEqual([]);
    const calls = shellCalls(fetchMock);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("http://127.0.0.1:6973/launch");
    expect(calls[0][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json", "X-taOS-Shell": "1" },
        body: JSON.stringify({ app: "coding-studio" }),
      }),
    );
  });

  it("mobile + shell reports already-open: still no in-page window", async () => {
    stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true, already: true })));
    await primeDeviceClass();
    useProcessStore.getState().openWindow("files", SIZE);
    await settle();
    expect(windows()).toHaveLength(0);
  });

  const failures: Array<[string, ShellAnswer]> = [
    ["network error", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["HTTP 500", () => Promise.resolve(jsonResponse({ ok: true }, 500))],
    ["non-JSON body", () => Promise.resolve(new Response("<html>nope</html>", { status: 200 }))],
    ['{"ok": false}', () => Promise.resolve(jsonResponse({ ok: false }))],
    ['{"ok": "true"} (not strictly true)', () => Promise.resolve(jsonResponse({ ok: "true" }))],
    ["JSON null", () => Promise.resolve(jsonResponse(null))],
  ];

  for (const [name, answer] of failures) {
    it(`mobile + ${name}: falls back to the in-page window and surfaces it`, async () => {
      stubFetch("mobile", answer);
      await primeDeviceClass();
      const wid = useProcessStore.getState().openWindow("files", SIZE, undefined);
      expect(wid).toBe("");
      await settle();
      expect(windows().map((w) => w.appId)).toEqual(["files"]);
      expect(activations).toEqual([windows()[0].id]);
    });
  }

  it("mobile + shell never answers: falls back after the timeout, not before", async () => {
    stubFetch("mobile", () => new Promise<Response>(() => {}));
    await primeDeviceClass();
    vi.useFakeTimers();
    useProcessStore.getState().openWindow("files", SIZE);
    await vi.advanceTimersByTimeAsync(SHELL_LAUNCH_TIMEOUT_MS - 100);
    expect(windows()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(200);
    expect(windows().map((w) => w.appId)).toEqual(["files"]);
    expect(SHELL_LAUNCH_TIMEOUT_MS).toBeLessThanOrEqual(1500);
  });

  it("device_class is fetched once, not per open", async () => {
    const fetchMock = stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true })));
    await Promise.all([primeDeviceClass(), primeDeviceClass()]);
    await primeDeviceClass();
    const open = useProcessStore.getState().openWindow;
    open("files", SIZE);
    open("notes", SIZE);
    open("calculator", SIZE);
    await settle();
    expect(configCalls(fetchMock)).toHaveLength(1);
    expect(shellCalls(fetchMock)).toHaveLength(3);
  });

  it("a failed device_class fetch is retried, then cached", async () => {
    let configFails = true;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === CONFIG_URL) {
        return configFails
          ? Promise.reject(new TypeError("offline"))
          : Promise.resolve(jsonResponse({ device_class: "mobile" }));
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);
    await primeDeviceClass();
    configFails = false;
    useProcessStore.getState().openWindow("files", SIZE); // unknown: in-page, triggers the retry
    await settle();
    expect(windows()).toHaveLength(1);
    useProcessStore.getState().openWindow("notes", SIZE);
    useProcessStore.getState().openWindow("calculator", SIZE);
    await settle();
    expect(configCalls(fetchMock)).toHaveLength(2);
    expect(shellCalls(fetchMock)).toHaveLength(2);
  });

  it("an existing in-page window does not pin the app in-page: a confirmed launch supersedes it", async () => {
    const fetchMock = stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true })));
    await primeDeviceClass();
    // e.g. restored from the saved layout at boot, or left by an earlier fallback
    useProcessStore.getState().openWindow("files", SIZE, undefined, { inPage: true });
    useProcessStore.getState().openWindow("notes", SIZE, undefined, { inPage: true });
    const wid = useProcessStore.getState().openWindow("files", SIZE);
    expect(wid).toBe("");
    await settle();
    expect(shellCalls(fetchMock)).toHaveLength(1);
    // Removed outright (not left half-closed), and other apps untouched.
    expect(windows().map((w) => w.appId)).toEqual(["notes"]);
  });

  it("an existing in-page window is restored and surfaced when the launch fails", async () => {
    stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: false })));
    await primeDeviceClass();
    const first = useProcessStore.getState().openWindow("files", SIZE, undefined, { inPage: true });
    useProcessStore.getState().minimizeWindow(first);
    useProcessStore.getState().openWindow("files", SIZE);
    await settle();
    expect(windows().map((w) => w.id)).toEqual([first]);
    expect(windows()[0].minimized).toBe(false);
    expect(activations).toEqual([first]);
  });

  it("a config endpoint that keeps failing is retried a bounded number of times, not per open", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new TypeError("offline")));
    vi.stubGlobal("fetch", fetchMock);
    await primeDeviceClass();
    for (let i = 0; i < 10; i++) {
      useProcessStore.getState().openWindow(`app-${i}`, SIZE);
      await settle();
    }
    expect(configCalls(fetchMock).length).toBeLessThanOrEqual(3);
    expect(windows()).toHaveLength(10);
  });

  it("passes the app id exactly as the registry id (no mangling or encoding)", async () => {
    const fetchMock = stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true })));
    await primeDeviceClass();
    for (const id of ["youtube-library", "x-monitor", "taos-agent"]) {
      useProcessStore.getState().openWindow(id, SIZE);
    }
    await settle();
    const sent = shellCalls(fetchMock).map(([, init]) => JSON.parse(String((init as RequestInit).body)).app);
    expect(sent).toEqual(["youtube-library", "x-monitor", "taos-agent"]);
  });

  describe("opens that stay in-page on the handset", () => {
    let fetchMock: ReturnType<typeof vi.fn>;
    beforeEach(async () => {
      fetchMock = stubFetch("mobile", () => Promise.resolve(jsonResponse({ ok: true })));
      await primeDeviceClass();
    });

    it("Settings (a shell system surface)", async () => {
      useProcessStore.getState().openWindow("settings", SIZE);
      await settle();
      expect(windows().map((w) => w.appId)).toEqual(["settings"]);
      expect(shellCalls(fetchMock)).toHaveLength(0);
    });

    it("an open carrying props (a deep link app.html cannot carry)", async () => {
      useProcessStore.getState().openWindow("files", SIZE, { path: "/home" });
      await settle();
      expect(windows()[0].props).toEqual({ path: "/home" });
      expect(shellCalls(fetchMock)).toHaveLength(0);
    });

    it("an explicit second window (forceNew)", async () => {
      useProcessStore.getState().openWindow("files", SIZE, undefined, { forceNew: true });
      await settle();
      expect(windows()).toHaveLength(1);
      expect(shellCalls(fetchMock)).toHaveLength(0);
    });

    it("an explicit inPage open (session restore, agent window control)", async () => {
      const wid = useProcessStore.getState().openWindow("files", SIZE, undefined, { inPage: true });
      expect(wid).not.toBe("");
      await settle();
      expect(shellCalls(fetchMock)).toHaveLength(0);
    });

    it("an in-page open whose app is already open focuses it, without the shell", async () => {
      const first = useProcessStore.getState().openWindow("files", SIZE, undefined, { inPage: true });
      const again = useProcessStore.getState().openWindow("files", SIZE, undefined, { inPage: true });
      await settle();
      expect(again).toBe(first);
      expect(shellCalls(fetchMock)).toHaveLength(0);
    });
  });
});
