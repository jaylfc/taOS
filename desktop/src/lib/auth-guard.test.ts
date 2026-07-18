import { describe, it, expect, vi } from "vitest";
import { installAuthGuard } from "./auth-guard";

describe("installAuthGuard CSRF wiring", () => {
  it("attaches X-CSRF-Token to same-origin mutating requests only", async () => {
    document.cookie = "csrf_token=tok123";
    const spy = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    // installAuthGuard captures window.fetch as the original at install time, so
    // set the spy first, then install: subsequent window.fetch calls go through
    // the patched wrapper into the spy.
    window.fetch = spy as unknown as typeof window.fetch;
    installAuthGuard();

    // Same-origin mutating request: the double-submit header is attached so the
    // backend's router-wide verify_csrf gate is satisfied.
    await window.fetch("/api/projects", { method: "POST", body: "{}" });
    const postInit = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(postInit.headers).get("X-CSRF-Token")).toBe("tok123");

    // Non-mutating request: no header (verify_csrf only gates mutations).
    await window.fetch("/api/projects");
    const getInit = spy.mock.calls.at(-1)?.[1] as RequestInit | undefined;
    expect(getInit ? new Headers(getInit.headers).get("X-CSRF-Token") : null).toBeNull();

    // External origin: the token is never sent off-site.
    await window.fetch("https://evil.example/x", { method: "POST" });
    const extInit = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(extInit.headers).get("X-CSRF-Token")).toBeNull();

    // Protocol-relative and lookalike-host URLs must NOT be treated as
    // same-origin (a prefix check would leak the token to these).
    await window.fetch("//evil.example/x", { method: "POST" });
    const protoRel = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(protoRel.headers).get("X-CSRF-Token")).toBeNull();

    await window.fetch(`${window.location.origin}.evil.com/x`, { method: "POST" });
    const lookalike = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(lookalike.headers).get("X-CSRF-Token")).toBeNull();

    // Request-object input for a same-origin mutating call: the wrapper rebuilds
    // the Request with the header attached (first arg is the Request).
    await window.fetch(new Request(`${window.location.origin}/api/x`, { method: "POST" }));
    const reqArg = spy.mock.calls.at(-1)?.[0] as Request;
    expect(reqArg.headers.get("X-CSRF-Token")).toBe("tok123");
  });
});
