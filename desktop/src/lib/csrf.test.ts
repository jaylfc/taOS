import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCsrfToken, withCsrf } from "./csrf";

describe("getCsrfToken", () => {
  const originalDocument = global.document;

  afterEach(() => {
    vi.stubGlobal("document", originalDocument);
    vi.restoreAllMocks();
  });

  it("returns the token when csrf_token cookie is present", () => {
    vi.stubGlobal("document", {
      cookie: "session=abc123; csrf_token=token456; other=val",
    } as unknown as Document);
    expect(getCsrfToken()).toBe("token456");
  });

  it("returns null when no csrf_token cookie exists", () => {
    vi.stubGlobal("document", {
      cookie: "session=abc123; other=val",
    } as unknown as Document);
    expect(getCsrfToken()).toBeNull();
  });

  it("returns null when document is undefined (SSR)", () => {
    vi.stubGlobal("document", undefined);
    expect(getCsrfToken()).toBeNull();
  });

  it("returns null for empty cookie string", () => {
    vi.stubGlobal("document", { cookie: "" } as unknown as Document);
    expect(getCsrfToken()).toBeNull();
  });

  it("decodes a URL-encoded token", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=abc%2Fdef%3D",
    } as unknown as Document);
    expect(getCsrfToken()).toBe("abc/def=");
  });

  it("returns null when csrf_token value is empty", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=; other=val",
    } as unknown as Document);
    expect(getCsrfToken()).toBeNull();
  });
});

describe("withCsrf", () => {
  const originalDocument = global.document;

  afterEach(() => {
    vi.stubGlobal("document", originalDocument);
    vi.restoreAllMocks();
  });

  it("attaches X-CSRF-Token header on POST when token exists", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=mytoken",
    } as unknown as Document);
    const result = withCsrf({ method: "POST" });
    expect(result).toBeDefined();
    expect(result!.headers).toBeInstanceOf(Headers);
    expect(result!.headers.get("X-CSRF-Token")).toBe("mytoken");
  });

  it("returns init unchanged for GET even with token present", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=mytoken",
    } as unknown as Document);
    const init: RequestInit = { method: "GET" };
    expect(withCsrf(init)).toBe(init);
  });

  it("returns init unchanged when no csrf_token cookie", () => {
    vi.stubGlobal("document", {
      cookie: "session=abc",
    } as unknown as Document);
    const init: RequestInit = { method: "POST" };
    expect(withCsrf(init)).toBe(init);
  });

  it("attaches header for PUT method", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=puttoken",
    } as unknown as Document);
    const result = withCsrf({ method: "PUT" });
    expect(result!.headers.get("X-CSRF-Token")).toBe("puttoken");
  });

  it("attaches header for PATCH method", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=patchtoken",
    } as unknown as Document);
    const result = withCsrf({ method: "PATCH" });
    expect(result!.headers.get("X-CSRF-Token")).toBe("patchtoken");
  });

  it("attaches header for DELETE method", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=deletetoken",
    } as unknown as Document);
    const result = withCsrf({ method: "DELETE" });
    expect(result!.headers.get("X-CSRF-Token")).toBe("deletetoken");
  });

  it("defaults to GET when no method specified", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=mytoken",
    } as unknown as Document);
    const init: RequestInit = {};
    expect(withCsrf(init)).toBe(init);
  });

  it("preserves existing headers when adding CSRF token", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=mytoken",
    } as unknown as Document);
    const init: RequestInit = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    };
    const result = withCsrf(init);
    expect(result!.headers.get("Content-Type")).toBe("application/json");
    expect(result!.headers.get("X-CSRF-Token")).toBe("mytoken");
  });

  it("does not overwrite an existing X-CSRF-Token header", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=cookietoken",
    } as unknown as Document);
    const init: RequestInit = {
      method: "POST",
      headers: { "X-CSRF-Token": "existing-token" },
    };
    const result = withCsrf(init);
    expect(result!.headers.get("X-CSRF-Token")).toBe("existing-token");
  });

  it("returns undefined when init is undefined and method defaults to GET", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=mytoken",
    } as unknown as Document);
    expect(withCsrf(undefined)).toBeUndefined();
  });

  it("handles lowercase method by uppercasing it", () => {
    vi.stubGlobal("document", {
      cookie: "csrf_token=mytoken",
    } as unknown as Document);
    const result = withCsrf({ method: "post" });
    expect(result!.headers.get("X-CSRF-Token")).toBe("mytoken");
  });
});
