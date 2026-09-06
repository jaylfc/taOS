import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchAccount,
  login,
  register,
  logout,
  isAuthError,
  checkSubdomain,
  claimSubdomain,
  releaseSubdomain,
} from "./account-client";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchAccount", () => {
  it("returns signed-in state with account on 200", async () => {
    const account = {
      user_id: "u-1",
      email: "a@b.test",
      taosgo: { status: "active" },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => account,
    });
    global.fetch = fetchMock;

    const result = await fetchAccount();
    expect(result).toEqual({ kind: "signed-in", account });

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/me");
    expect(opts.method).toBe("GET");
    expect(opts.credentials).toBe("include");
  });

  it("returns signed-out on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await fetchAccount()).toEqual({ kind: "signed-out" });
  });

  it("returns unavailable on 500", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await fetchAccount()).toEqual({ kind: "unavailable" });
  });

  it("returns unavailable on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    expect(await fetchAccount()).toEqual({ kind: "unavailable" });
  });

  it("returns unavailable when body is not a valid account", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ user_id: 123 }),
    });
    expect(await fetchAccount()).toEqual({ kind: "unavailable" });
  });

  it("returns unavailable when json parse fails", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new Error("bad json");
      },
    });
    expect(await fetchAccount()).toEqual({ kind: "unavailable" });
  });
});

describe("login", () => {
  it("returns account on 200", async () => {
    const account = {
      user_id: "u-1",
      email: "a@b.test",
      taosgo: { status: "active" },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => account,
    });
    global.fetch = fetchMock;

    const result = await login("a@b.test", "pw");
    expect(result).toEqual(account);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/login");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(opts.credentials).toBe("include");
    expect(JSON.parse(opts.body)).toEqual({ email: "a@b.test", password: "pw" });
  });

  it("returns AuthError on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: "Invalid credentials" }),
    });
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Invalid credentials");
    }
  });

  it("returns AuthError on 404", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("The account service is not available yet.");
    }
  });

  it("returns AuthError on 503", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503 });
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("The account service is not available yet.");
    }
  });

  it("returns AuthError on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Could not reach the account service. Check your connection.");
    }
  });

  it("returns AuthError when response body is not a valid account", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ user_id: 123 }),
    });
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Unexpected response from the account service.");
    }
  });

  it("returns AuthError using detail when error is absent", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "bad input" }),
    });
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("bad input");
    }
  });

  it("returns AuthError with status code default when body parse fails", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => {
        throw new Error("bad json");
      },
    });
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Request failed (400).");
    }
  });
});

describe("register", () => {
  it("returns account on 200", async () => {
    const account = {
      user_id: "u-2",
      email: "new@b.test",
      taosgo: { status: "trialing", trial_ends_at: "2026-07-01" },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => account,
    });
    global.fetch = fetchMock;

    const result = await register("new@b.test", "pw");
    expect(result).toEqual(account);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/register");
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
    expect(JSON.parse(opts.body)).toEqual({ email: "new@b.test", password: "pw" });
  });

  it("returns AuthError on 401", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: "Email already in use" }),
    });
    const result = await register("new@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Email already in use");
    }
  });

  it("returns AuthError on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await register("new@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Could not reach the account service. Check your connection.");
    }
  });
});

describe("logout", () => {
  it("posts to /logout with empty body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    global.fetch = fetchMock;

    await logout();

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/logout");
    expect(opts.method).toBe("POST");
    expect(opts.credentials).toBe("include");
    expect(JSON.parse(opts.body)).toEqual({});
  });

  it("does not throw on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    await expect(logout()).resolves.toBeUndefined();
  });

  it("does not throw on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    await expect(logout()).resolves.toBeUndefined();
  });
});

describe("checkSubdomain", () => {
  it("returns the availability result on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ available: true }),
    });
    global.fetch = fetchMock;

    const result = await checkSubdomain("mybiz");
    expect(isAuthError(result)).toBe(false);
    if (!isAuthError(result)) {
      expect(result).toEqual({ available: true });
    }
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/subdomains/check?name=mybiz");
    expect(opts.method).toBe("GET");
    expect(opts.credentials).toBe("include");
  });

  it("returns AuthError on 503 (service not live)", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503 });
    const result = await checkSubdomain("mybiz");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("The account service is not available yet.");
    }
  });

  it("returns AuthError on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    const result = await checkSubdomain("mybiz");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("Could not reach the account service. Check your connection.");
    }
  });
});

describe("claimSubdomain", () => {
  it("returns the new claim on 200", async () => {
    const claim = { id: "s1", account_id: "u1", name: "mybiz", status: "active" as const };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => claim,
    });
    global.fetch = fetchMock;

    const result = await claimSubdomain("mybiz");
    expect(isAuthError(result)).toBe(false);
    if (!isAuthError(result)) {
      expect(result).toEqual(claim);
    }
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/subdomains/claim");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ name: "mybiz" });
  });

  it("returns AuthError with the server message on refusal", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ error: "name taken" }),
    });
    const result = await claimSubdomain("mybiz");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("name taken");
    }
  });
});

describe("releaseSubdomain", () => {
  it("returns the released marker on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ released: true }),
    });
    global.fetch = fetchMock;

    const result = await releaseSubdomain("mybiz");
    expect(isAuthError(result)).toBe(false);
    if (!isAuthError(result)) {
      expect(result).toEqual({ released: true });
    }
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/subdomains/release");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ name: "mybiz" });
  });

  it("returns AuthError on 404", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "not found" }),
    });
    const result = await releaseSubdomain("mybiz");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.message).toBe("The account service is not available yet.");
    }
  });
});
