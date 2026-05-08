import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { installAuthGuard, SESSION_EXPIRED_EVENT } from "../auth-guard";

describe("auth-guard", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = window.fetch;
    // Reset the module-level `installed` flag by re-importing — Vitest
    // caches the module, so each test starts with the wrapper already
    // potentially installed from a previous test. We work around that
    // by resetting the modules. Cheaper alternative for this single
    // test file: run the test cases serially and accept that
    // installAuthGuard is idempotent (assertion below verifies that).
  });

  afterEach(() => {
    window.fetch = originalFetch;
    vi.resetModules();
  });

  it("dispatches taos-session-expired on 401 from /api/* paths", async () => {
    vi.resetModules();
    const { installAuthGuard: install, SESSION_EXPIRED_EVENT: evt } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(evt, handler);
    await window.fetch("/api/store/catalog");
    window.removeEventListener(evt, handler);

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not dispatch on 401 from /auth/* paths", async () => {
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await window.fetch("/auth/login");
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).not.toHaveBeenCalled();
  });

  it("does not dispatch on 200", async () => {
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await window.fetch("/api/health");
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).not.toHaveBeenCalled();
  });

  it("throttles bursts to one event per 2s", async () => {
    vi.resetModules();
    const { installAuthGuard: install } = await import("../auth-guard");
    window.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    install();

    const handler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, handler);
    await Promise.all([
      window.fetch("/api/a"),
      window.fetch("/api/b"),
      window.fetch("/api/c"),
    ]);
    window.removeEventListener(SESSION_EXPIRED_EVENT, handler);

    expect(handler).toHaveBeenCalledTimes(1);
  });
});
