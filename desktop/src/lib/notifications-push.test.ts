import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./notifications-push-api";
import {
  urlBase64ToUint8Array,
  isPushSupported,
  getPushState,
  enableNotificationsPush,
  disableNotificationsPush,
} from "./notifications-push";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// urlBase64ToUint8Array
// ---------------------------------------------------------------------------

describe("urlBase64ToUint8Array", () => {
  it("decodes a base64url VAPID key to the expected bytes", () => {
    // "AQID" base64 -> [1,2,3]; base64url is identical here.
    expect(Array.from(urlBase64ToUint8Array("AQID"))).toEqual([1, 2, 3]);
  });

  it("pads and translates -_ back to +/ before decoding", () => {
    const b64 = btoa("\xfb\xff\xfe").replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
    expect(Array.from(urlBase64ToUint8Array(b64))).toEqual([251, 255, 254]);
  });
});

// ---------------------------------------------------------------------------
// support detection + state
// ---------------------------------------------------------------------------

function stubSupported(permission: NotificationPermission, existing: unknown) {
  const pushManager = {
    getSubscription: vi.fn().mockResolvedValue(existing),
    subscribe: vi.fn(),
  };
  const registration = { pushManager };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve(registration), register: vi.fn() },
  });
  vi.stubGlobal("PushManager", class PushManager {});
  vi.stubGlobal("Notification", {
    permission,
    requestPermission: vi.fn().mockResolvedValue(permission),
  });
  return { pushManager, registration };
}

describe("isPushSupported", () => {
  it("false when PushManager/Notification are absent", () => {
    vi.stubGlobal("PushManager", undefined);
    // Notification may be defined by jsdom; force it absent for the check.
    vi.stubGlobal("Notification", undefined);
    expect(isPushSupported()).toBe(false);
  });

  it("true when serviceWorker + PushManager + Notification exist", () => {
    stubSupported("default", null);
    expect(isPushSupported()).toBe(true);
  });
});

describe("getPushState", () => {
  it("reports needs-install on iOS Safari before add-to-home-screen", async () => {
    vi.stubGlobal("PushManager", undefined);
    vi.stubGlobal("Notification", undefined);
    Object.defineProperty(navigator, "userAgent", {
      configurable: true,
      value: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) Safari",
    });
    Object.defineProperty(navigator, "standalone", { configurable: true, value: false });
    expect(await getPushState()).toBe("needs-install");
  });

  it("reports disabled when supported but no subscription exists", async () => {
    stubSupported("granted", null);
    expect(await getPushState()).toBe("disabled");
  });

  it("reports enabled when a subscription already exists", async () => {
    stubSupported("granted", { endpoint: "https://push.example.com/ep" });
    expect(await getPushState()).toBe("enabled");
  });

  it("reports denied when permission is denied", async () => {
    stubSupported("denied", null);
    expect(await getPushState()).toBe("denied");
  });
});

// ---------------------------------------------------------------------------
// enable / disable
// ---------------------------------------------------------------------------

describe("enableNotificationsPush", () => {
  it("subscribes and POSTs the subscription to /subscribe", async () => {
    const { pushManager } = stubSupported("granted", null);
    pushManager.subscribe = vi.fn().mockResolvedValue({
      endpoint: "https://push.example.com/ep",
      getKey: (name: string) => {
        if (name === "p256dh") return new Uint8Array([1, 2, 3]).buffer;
        if (name === "auth") return new Uint8Array([4, 5]).buffer;
        return null;
      },
    });
    vi.spyOn(api, "getVapidPublicKey").mockResolvedValue("AQID");
    const subscribeSpy = vi.spyOn(api, "subscribePush").mockResolvedValue({ ok: true });

    const result = await enableNotificationsPush();

    expect(result).toBe("enabled");
    expect(pushManager.subscribe).toHaveBeenCalledWith(
      expect.objectContaining({ userVisibleOnly: true }),
    );
    expect(subscribeSpy).toHaveBeenCalledOnce();
    const arg = subscribeSpy.mock.calls[0][0];
    expect(arg.endpoint).toBe("https://push.example.com/ep");
    expect(arg.keys.p256dh).toBe("AQID"); // base64url of [1,2,3]
  });

  it("returns denied when permission is refused and never subscribes", async () => {
    const { pushManager } = stubSupported("granted", null);
    (Notification.requestPermission as ReturnType<typeof vi.fn>).mockResolvedValue("denied");
    const subscribeSpy = vi.spyOn(api, "subscribePush");

    const result = await enableNotificationsPush();

    expect(result).toBe("denied");
    expect(pushManager.subscribe).not.toHaveBeenCalled();
    expect(subscribeSpy).not.toHaveBeenCalled();
  });
});

describe("disableNotificationsPush", () => {
  it("unsubscribes the SW and tells the server to drop the endpoint", async () => {
    const unsub = vi.fn().mockResolvedValue(true);
    const { pushManager } = stubSupported("granted", {
      endpoint: "https://push.example.com/ep",
      unsubscribe: unsub,
    });
    void pushManager;
    const unsubSpy = vi.spyOn(api, "unsubscribePush").mockResolvedValue({ ok: true });

    const result = await disableNotificationsPush();

    expect(result).toBe("disabled");
    expect(unsub).toHaveBeenCalledOnce();
    expect(unsubSpy).toHaveBeenCalledWith("https://push.example.com/ep");
  });
});
