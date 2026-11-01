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

describe("login and register against taos.my's real response shapes", () => {
  const account = { user_id: "u-9", email: "a@b.test", taosgo: { status: "none" } };
  const res = (status: number, body: unknown) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });

  it("login: taos.my answers {user_id}, the account comes from /me", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(res(200, { user_id: "u-9" }))
      .mockResolvedValueOnce(res(200, account));
    global.fetch = fetchMock;
    expect(await login("a@b.test", "pw")).toEqual(account);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/account/me");
  });

  it("login: a 2fa_required answer is an error, not a sign-in", async () => {
    global.fetch = vi.fn().mockResolvedValue(res(200, { status: "2fa_required", pending: "p" }));
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result) && result.code).toBe("2fa_required");
  });

  it("login: invalid_credentials gets readable text and keeps the code", async () => {
    global.fetch = vi.fn().mockResolvedValue(res(401, { detail: "invalid_credentials" }));
    const result = await login("a@b.test", "pw");
    expect(isAuthError(result)).toBe(true);
    if (isAuthError(result)) {
      expect(result.code).toBe("invalid_credentials");
      expect(result.message).toBe("That email and password don't match a taOS account.");
    }
  });

  it("register: taos.my answers {user_id} with a session, the account comes from /me", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(res(200, { user_id: "u-9" }))
      .mockResolvedValueOnce(res(200, account));
    global.fetch = fetchMock;
    expect(await register("a@b.test", "pw")).toEqual(account);
    // No second POST to /login: the password is sent once.
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/api/account/register",
      "/api/account/me",
    ]);
  });

  it("register: signups_closed keeps its code for the caller to show", async () => {
    global.fetch = vi.fn().mockResolvedValue(res(403, { detail: "signups_closed" }));
    const result = await register("a@b.test", "pw");
    expect(isAuthError(result) && result.code).toBe("signups_closed");
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
