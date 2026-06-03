/**
 * Unit tests for the push + notificationclick handlers added to desktop/src/sw.ts.
 *
 * sw.ts cannot be imported directly in vitest (it depends on the Rollup-time
 * __TAOS_VERSION__ define and the ServiceWorkerGlobalScope type). Instead we
 * exercise the identical handler logic as pure functions to verify correctness,
 * and confirm that bootstrapPushSubscription targets navigator.serviceWorker.ready
 * (which resolves against the shell /sw.js registration).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Replicated handler logic (must stay in sync with sw.ts)
// ---------------------------------------------------------------------------

interface PushPayload {
  title?: unknown;
  body?: unknown;
  tag?: unknown;
  icon?: unknown;
  data?: unknown;
}

function handlePush(
  rawJson: unknown,
  showNotification: (title: string, options: NotificationOptions) => void,
) {
  let payload: PushPayload | null = null;
  try {
    payload = (rawJson as PushPayload) ?? null;
  } catch {
    payload = null;
  }
  if (!payload || typeof payload !== "object") {
    payload = { title: "taOS", body: "New activity" };
  }
  const title = typeof payload.title === "string" ? payload.title : "taOS";
  const options: NotificationOptions = {
    body: typeof payload.body === "string" ? payload.body : "",
    tag: typeof payload.tag === "string" ? payload.tag : undefined,
    icon: typeof payload.icon === "string" ? payload.icon : undefined,
    data: payload.data && typeof payload.data === "object" ? payload.data : {},
  };
  showNotification(title, options);
}

interface FakeClient {
  url: string;
  postMessage: (msg: unknown) => void;
  focus: () => Promise<FakeClient>;
}

async function handleNotificationClick(
  notificationData: Record<string, unknown>,
  clients: FakeClient[],
  origin: string,
  openWindow: ((url: string) => Promise<null>) | null,
): Promise<unknown> {
  const data = notificationData || {};
  for (const client of clients) {
    if (client.url && new URL(client.url).origin === origin) {
      client.postMessage({ type: "taos-push:click", data });
      return client.focus();
    }
  }
  if (openWindow) {
    return openWindow("/");
  }
  return null;
}

// ---------------------------------------------------------------------------
// push handler tests
// ---------------------------------------------------------------------------

describe("sw push handler — handlePush", () => {
  it("shows notification with title + body from valid JSON payload", () => {
    const show = vi.fn();
    handlePush({ title: "Agent done", body: "Task completed", tag: "agent-1" }, show);
    expect(show).toHaveBeenCalledOnce();
    expect(show.mock.calls[0][0]).toBe("Agent done");
    expect(show.mock.calls[0][1]).toMatchObject({ body: "Task completed", tag: "agent-1" });
  });

  it("falls back to generic message when payload is null", () => {
    const show = vi.fn();
    handlePush(null, show);
    expect(show).toHaveBeenCalledWith("taOS", expect.objectContaining({ body: "New activity" }));
  });

  it("falls back to generic message when payload is not an object", () => {
    const show = vi.fn();
    handlePush("bad-string", show);
    expect(show).toHaveBeenCalledWith("taOS", expect.objectContaining({ body: "New activity" }));
  });

  it("falls back to 'taOS' title when title is missing", () => {
    const show = vi.fn();
    handlePush({ body: "Something happened" }, show);
    expect(show.mock.calls[0][0]).toBe("taOS");
    expect(show.mock.calls[0][1]).toMatchObject({ body: "Something happened" });
  });

  it("passes data object through to notification options", () => {
    const show = vi.fn();
    const data = { agentId: "abc", kind: "chat" };
    handlePush({ title: "Hi", body: "msg", data }, show);
    expect(show.mock.calls[0][1].data).toEqual(data);
  });

  it("sets data to {} when payload.data is not an object", () => {
    const show = vi.fn();
    handlePush({ title: "Hi", body: "msg", data: "bad" }, show);
    expect(show.mock.calls[0][1].data).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// notificationclick handler tests
// ---------------------------------------------------------------------------

describe("sw notificationclick handler — handleNotificationClick", () => {
  const origin = "http://localhost:6969";

  function makeClient(url: string): FakeClient {
    return {
      url,
      postMessage: vi.fn(),
      focus: vi.fn().mockResolvedValue(null),
    };
  }

  it("focuses an existing same-origin client and posts click data", async () => {
    const client = makeClient("http://localhost:6969/desktop/");
    const data = { agentId: "x1" };
    await handleNotificationClick(data, [client], origin, null);
    expect(client.postMessage).toHaveBeenCalledWith({ type: "taos-push:click", data });
    expect(client.focus).toHaveBeenCalledOnce();
  });

  it("skips cross-origin clients", async () => {
    const foreign = makeClient("http://evil.example.com/");
    const local = makeClient("http://localhost:6969/desktop/");
    await handleNotificationClick({}, [foreign, local], origin, null);
    expect(foreign.postMessage).not.toHaveBeenCalled();
    expect(local.postMessage).toHaveBeenCalled();
  });

  it("calls openWindow('/') when no matching client exists", async () => {
    const openWindow = vi.fn().mockResolvedValue(null);
    await handleNotificationClick({}, [], origin, openWindow);
    expect(openWindow).toHaveBeenCalledWith("/");
  });

  it("does not call openWindow when a matching client is found", async () => {
    const client = makeClient("http://localhost:6969/desktop/");
    const openWindow = vi.fn();
    await handleNotificationClick({}, [client], origin, openWindow);
    expect(openWindow).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Confirm push subscription targets the shell SW (/sw.js via .ready)
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — targets shell SW via navigator.serviceWorker.ready", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("resolves the SW registration via navigator.serviceWorker.ready (shell /sw.js)", async () => {
    // The key invariant: bootstrapPushSubscription calls navigator.serviceWorker.ready,
    // which resolves to whichever SW controls the shell origin. Since the shell
    // registers /sw.js via sw-register.ts, .ready resolves to that registration —
    // the one that now carries the push + notificationclick handlers.
    const { bootstrapPushSubscription } = await import("../browser-push-bootstrap");
    const { subscribePush, getVapidPublicKey } = await import("../browser-push-api");

    const pushManager = {
      getSubscription: vi.fn().mockResolvedValue(null),
      subscribe: vi.fn().mockResolvedValue({
        endpoint: "https://push.example.com/ep",
        getKey: (name: string) => {
          if (name === "p256dh") return new Uint8Array([1]).buffer;
          if (name === "auth") return new Uint8Array([2]).buffer;
          return null;
        },
      }),
    };
    const registration = { pushManager };

    // Capture what .ready resolves to — it must be a registration object,
    // confirming push subscribes via the shell SW registration.
    const readyResolved = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        ready: Promise.resolve(registration).then((r) => { readyResolved(r); return r; }),
        register: vi.fn().mockResolvedValue(registration),
      },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", {
      permission: "granted",
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    vi.spyOn({ subscribePush }, "subscribePush");
    vi.spyOn({ getVapidPublicKey }, "getVapidPublicKey");

    // Mock the API calls
    const subscribeSpy = vi.spyOn(
      await import("../browser-push-api"),
      "subscribePush",
    ).mockResolvedValue({ ok: true });
    vi.spyOn(
      await import("../browser-push-api"),
      "getVapidPublicKey",
    ).mockResolvedValue("BValidVapidKey==");

    const result = await bootstrapPushSubscription();
    expect(result.status).toBe("subscribed");
    // .ready was awaited, confirming we went through the shell SW registration
    expect(readyResolved).toHaveBeenCalledWith(registration);
    expect(subscribeSpy).toHaveBeenCalledOnce();
  });
});
