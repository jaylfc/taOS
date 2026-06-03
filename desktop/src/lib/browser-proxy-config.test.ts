import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import {
  __resetProxyConfigCache,
  buildProxiedPath,
  buildProxyOrigin,
  buildRedeemUrl,
  getBrowserProxyOrigin,
  getBrowserProxyPort,
  mintProxyTicket,
} from "./browser-proxy-config";

beforeEach(() => {
  __resetProxyConfigCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getBrowserProxyPort", () => {
  it("returns the port reported by the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify({ port: 6970 }), { status: 200 })),
      ),
    );
    expect(await getBrowserProxyPort()).toBe(6970);
  });

  it("caches the port (single fetch across calls)", async () => {
    const f = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ port: 6970 }), { status: 200 })),
    );
    vi.stubGlobal("fetch", f);
    await getBrowserProxyPort();
    await getBrowserProxyPort();
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("returns 0 on a non-ok response (single-port fallback)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("x", { status: 500 }))));
    expect(await getBrowserProxyPort()).toBe(0);
  });

  it("returns 0 on a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    expect(await getBrowserProxyPort()).toBe(0);
  });

  it("treats port 0 as single-port", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ port: 0 }), { status: 200 }))),
    );
    expect(await getBrowserProxyPort()).toBe(0);
  });
});

describe("buildProxyOrigin", () => {
  it("returns the current origin in single-port mode (port 0)", () => {
    expect(buildProxyOrigin(0)).toBe(window.location.origin);
  });

  it("returns the current origin when the proxy port equals the main port", () => {
    const samePort = window.location.port || "80";
    expect(buildProxyOrigin(Number(samePort))).toBe(window.location.origin);
  });

  it("builds a cross-origin URL from the current host + proxy port", () => {
    const origin = buildProxyOrigin(6970);
    const u = new URL(origin);
    expect(u.hostname).toBe(window.location.hostname);
    expect(u.port).toBe("6970");
    expect(u.protocol).toBe(window.location.protocol);
  });
});

describe("getBrowserProxyOrigin", () => {
  it("resolves to the cross-origin proxy origin when a port is set", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ port: 6970 }), { status: 200 }))),
    );
    const u = new URL(await getBrowserProxyOrigin());
    expect(u.port).toBe("6970");
  });
});

describe("buildProxiedPath", () => {
  it("builds the proxy path with profile_id, url, tab_id", () => {
    const p = buildProxiedPath("personal", "https://example.com/", "tab-1");
    expect(p).toContain("/api/desktop/browser/proxy?");
    expect(p).toContain("profile_id=personal");
    expect(p).toContain("tab_id=tab-1");
    expect(p).toContain(encodeURIComponent("https://example.com/"));
  });

  it("returns empty for about:blank / about: urls", () => {
    expect(buildProxiedPath("personal", "about:blank", "t")).toBe("");
    expect(buildProxiedPath("personal", "about:config", "t")).toBe("");
    expect(buildProxiedPath("personal", "", "t")).toBe("");
  });
});

describe("buildRedeemUrl", () => {
  it("puts the ticket on the redeem URL and the proxied path in encoded next", () => {
    const proxied = buildProxiedPath("personal", "https://example.com/", "tab-1");
    const redeem = buildRedeemUrl("https://host:6970", "tok-abc", proxied);
    const u = new URL(redeem);
    expect(u.origin).toBe("https://host:6970");
    expect(u.pathname).toBe("/__taos/redeem");
    expect(u.searchParams.get("ticket")).toBe("tok-abc");
    // next is the full proxied path, round-trips back to the proxy endpoint.
    expect(u.searchParams.get("next")).toBe(proxied);
  });
});

describe("mintProxyTicket", () => {
  it("returns the ticket token on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ ticket: "tok-xyz", expires_in: 30 }), { status: 200 }),
        ),
      ),
    );
    expect(await mintProxyTicket()).toBe("tok-xyz");
  });

  it("returns null on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("x", { status: 500 }))));
    expect(await mintProxyTicket()).toBeNull();
  });

  it("returns null on a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    expect(await mintProxyTicket()).toBeNull();
  });
});
