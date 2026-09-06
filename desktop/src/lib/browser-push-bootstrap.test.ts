import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bootstrapPushSubscription } from "./browser-push-bootstrap";
import * as pushApi from "./browser-push-api";

const DEVICE_ID_KEY = "taos-browser:push-device-id";

// ---------------------------------------------------------------------------
// Helpers to build mock push subscriptions
// ---------------------------------------------------------------------------

function makeMockSubscription(opts: { endpoint?: string } = {}) {
  const endpoint = opts.endpoint ?? "https://push.example.com/endpoint-1";
  return {
    endpoint,
    getKey: vi.fn((name: string) => {
      if (name === "p256dh") return new Uint8Array([1, 2, 3]).buffer;
      if (name === "auth") return new Uint8Array([4, 5, 6]).buffer;
      return null;
    }),
  };
}

function makePushManager(existingSubscription: unknown = null) {
  return {
    getSubscription: vi.fn().mockResolvedValue(existingSubscription),
    subscribe: vi.fn().mockResolvedValue(makeMockSubscription()),
  };
}

function makeServiceWorkerRegistration(pushManager: ReturnType<typeof makePushManager>) {
  return { pushManager };
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

const originalFetch = global.fetch;

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  global.fetch = originalFetch;
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Utility: set up navigator + Notification mocks
// ---------------------------------------------------------------------------

function stubSupported(permission: NotificationPermission, existingSubscription: unknown = null) {
  const pushManager = makePushManager(existingSubscription);
  const registration = makeServiceWorkerRegistration(pushManager);

  // Stub serviceWorker
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      ready: Promise.resolve(registration),
      register: vi.fn().mockResolvedValue(registration),
    },
  });

  // Stub PushManager in window
  vi.stubGlobal("PushManager", class PushManager {});

  // Stub Notification
  vi.stubGlobal("Notification", {
    permission,
    requestPermission: vi.fn().mockResolvedValue(permission),
  });

  return { pushManager, registration };
}

function stubUnsupported() {
  // Remove serviceWorker from navigator
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: undefined,
  });
  // Remove PushManager
  vi.stubGlobal("PushManager", undefined);
}

// ---------------------------------------------------------------------------
// Tests — unsupported
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — unsupported", () => {
  it("returns unsupported when serviceWorker is absent", async () => {
    stubUnsupported();
    const result = await bootstrapPushSubscription();
    expect(result).toEqual({ status: "unsupported" });
  });
});

// ---------------------------------------------------------------------------
// Tests — no permission
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — no permission", () => {
  it("returns no-permission with reason=default when permission is 'default'", async () => {
    stubSupported("default");
    const result = await bootstrapPushSubscription();
    expect(result).toEqual({ status: "no-permission", reason: "default" });
  });

  it("makes no network calls when permission is 'default'", async () => {
    stubSupported("default");
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    await bootstrapPushSubscription();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns no-permission with reason=denied when permission is 'denied'", async () => {
    stubSupported("denied");
    const result = await bootstrapPushSubscription();
    expect(result).toEqual({ status: "no-permission", reason: "denied" });
  });

  it("makes no network calls when permission is 'denied'", async () => {
    stubSupported("denied");
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    await bootstrapPushSubscription();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Tests — granted + no existing subscription
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — granted, no existing subscription", () => {
  it("calls pushManager.subscribe and POSTs to /push/subscribe, returns subscribed", async () => {
    const { pushManager } = stubSupported("granted", null);
    const subscribePushSpy = vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");

    const result = await bootstrapPushSubscription();

    expect(pushManager.subscribe).toHaveBeenCalledOnce();
    expect(pushManager.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: expect.any(Uint8Array),
    });
    expect(subscribePushSpy).toHaveBeenCalledOnce();
    const callArgs = subscribePushSpy.mock.calls[0][0];
    expect(callArgs.endpoint).toBe("https://push.example.com/endpoint-1");
    expect(typeof callArgs.device_id).toBe("string");
    expect(callArgs.device_id).toBeTruthy();
    expect(result.status).toBe("subscribed");
    if (result.status === "subscribed") {
      expect(typeof result.device_id).toBe("string");
    }
  });

  it("generates and stores a new device_id in localStorage on first call", async () => {
    stubSupported("granted", null);
    vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");

    expect(localStorage.getItem(DEVICE_ID_KEY)).toBeNull();
    const result = await bootstrapPushSubscription();

    const stored = localStorage.getItem(DEVICE_ID_KEY);
    expect(stored).toBeTruthy();
    if (result.status === "subscribed") {
      expect(result.device_id).toBe(stored);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests — granted + existing subscription (re-POST path)
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — granted, existing subscription", () => {
  it("does NOT call pushManager.subscribe again when subscription already exists", async () => {
    const existing = makeMockSubscription({ endpoint: "https://push.example.com/existing" });
    const { pushManager } = stubSupported("granted", existing);
    const subscribePushSpy = vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");

    const result = await bootstrapPushSubscription();

    expect(pushManager.subscribe).not.toHaveBeenCalled();
    expect(subscribePushSpy).toHaveBeenCalledOnce();
    const callArgs = subscribePushSpy.mock.calls[0][0];
    expect(callArgs.endpoint).toBe("https://push.example.com/existing");
    expect(result.status).toBe("subscribed");
  });

  it("re-POSTs with the existing subscription's endpoint to refresh last_seen_at", async () => {
    const existing = makeMockSubscription({ endpoint: "https://push.example.com/existing" });
    stubSupported("granted", existing);
    const subscribePushSpy = vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });

    await bootstrapPushSubscription();

    expect(subscribePushSpy).toHaveBeenCalledOnce();
    expect(subscribePushSpy.mock.calls[0][0].endpoint).toBe(
      "https://push.example.com/existing",
    );
  });
});

// ---------------------------------------------------------------------------
// Tests — device_id persistence
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — device_id persistence", () => {
  it("reuses the existing device_id from localStorage on subsequent calls", async () => {
    const stored_id = "my-stable-device-uuid";
    localStorage.setItem(DEVICE_ID_KEY, stored_id);

    stubSupported("granted", null);
    const subscribePushSpy = vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");

    const result = await bootstrapPushSubscription();

    expect(subscribePushSpy.mock.calls[0][0].device_id).toBe(stored_id);
    if (result.status === "subscribed") {
      expect(result.device_id).toBe(stored_id);
    }
    // localStorage should still have the same id (not regenerated)
    expect(localStorage.getItem(DEVICE_ID_KEY)).toBe(stored_id);
  });

  it("generates different device_ids for two fresh starts", async () => {
    stubSupported("granted", null);
    vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");

    // First call
    await bootstrapPushSubscription();
    const firstId = localStorage.getItem(DEVICE_ID_KEY);

    // Reset localStorage to simulate a new first-time run
    localStorage.clear();

    // Re-stub because vi.restoreAllMocks clears previous stubs inline
    const { pushManager: pm2 } = stubSupported("granted", null);
    pm2.getSubscription.mockResolvedValue(null);
    vi.spyOn(pushApi, "subscribePush").mockResolvedValue({ ok: true });
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");

    await bootstrapPushSubscription();
    const secondId = localStorage.getItem(DEVICE_ID_KEY);

    expect(firstId).toBeTruthy();
    expect(secondId).toBeTruthy();
    expect(firstId).not.toBe(secondId);
  });
});

// ---------------------------------------------------------------------------
// Tests — error path
// ---------------------------------------------------------------------------

describe("bootstrapPushSubscription — error handling", () => {
  it("returns { status: 'error' } rather than throwing when subscribePush fails", async () => {
    stubSupported("granted", null);
    vi.spyOn(pushApi, "getVapidPublicKey").mockResolvedValue("BValidVapidKey==");
    vi.spyOn(pushApi, "subscribePush").mockRejectedValue(new Error("subscribe failed"));

    const result = await bootstrapPushSubscription();
    expect(result.status).toBe("error");
    if (result.status === "error") {
      expect(result.error.message).toContain("subscribe failed");
    }
  });

  it("returns { status: 'error' } when getVapidPublicKey fails", async () => {
    stubSupported("granted", null);
    vi.spyOn(pushApi, "getVapidPublicKey").mockRejectedValue(new Error("vapid error"));

    const result = await bootstrapPushSubscription();
    expect(result.status).toBe("error");
  });
});
