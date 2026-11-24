import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { assistantNameError, deployFirstChat, DeployUnanswered } from "./first-chat";
import { HopError } from "./agent";

describe("assistantNameError", () => {
  it("accepts a name with a letter or digit, in any script", () => {
    for (const n of ["Assistant", "R2", "  Sage ", "Élodie", "助手", "x".repeat(64)]) {
      expect(assistantNameError(n)).toBe("");
    }
  });

  it("refuses a blank, an over-long or a symbol-only name", () => {
    expect(assistantNameError("   ")).toBe("Give your assistant a name.");
    expect(assistantNameError("x".repeat(65))).toBe("Use 64 characters or fewer.");
    expect(assistantNameError("!!! ---")).toBe("Use at least one letter or number.");
  });
});

interface Call {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: string;
}

const calls: Call[] = [];
const order: string[] = [];

beforeEach(() => {
  calls.length = 0;
  order.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(response: (url: string, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      order.push(`fetch:${url}`);
      calls.push({ url, method: init?.method, headers: init?.headers as Record<string, string>, body: init?.body as string });
      return response(url, init);
    }),
  );
}

const BODY = { name: "Assistant", framework: "linux", model: "m1", soul_md: "soul" };

describe("deployFirstChat", () => {
  it("saves the key before the POST and the POST carries it", async () => {
    const saveLog: string[] = [];
    stubFetch(async () => new Response(JSON.stringify({ name: "agent-1" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const key = { current: "k-1" };
    const name = await deployFirstChat(key, async (k) => { saveLog.push(k); order.push(`save:${k}`); return true; }, BODY);
    expect(name).toBe("agent-1");
    expect(saveLog).toEqual(["k-1"]);
    const deploy = calls.find((c) => c.url === "/api/agents/deploy");
    expect(deploy).toBeTruthy();
    expect(deploy?.headers?.["Idempotency-Key"]).toBe("k-1");
    expect(order.indexOf(`save:k-1`)).toBeLessThan(order.indexOf("fetch:/api/agents/deploy"));
  });

  it("save resolving false throws HopError and sends no POST", async () => {
    stubFetch(async () => { throw new Error("fetch must not be called"); });
    const key = { current: "k-1" };
    await expect(deployFirstChat(key, async () => false, BODY)).rejects.toBeInstanceOf(HopError);
    expect(calls.find((c) => c.url === "/api/agents/deploy")).toBeFalsy();
    expect(key.current).toBe("k-1");
  });

  it("a 409 answer rotates the key, saves it again, and rethrows", async () => {
    stubFetch(async () => new Response(JSON.stringify({ detail: "conflict" }), { status: 409 }));
    const key = { current: "k-1" };
    const saveLog: string[] = [];
    await expect(deployFirstChat(key, async (k) => { saveLog.push(k); return true; }, BODY)).rejects.toBeInstanceOf(HopError);
    expect(key.current).not.toBe("k-1");
    // The full save sequence: the original key is saved before the POST, and the
    // rotated key is saved again after the refusal, in that order.
    expect(saveLog).toEqual(["k-1", key.current]);
  });

  it("a fetch rejection (DeployUnanswered) leaves the key alone", async () => {
    stubFetch(async () => { throw new Error("network down"); });
    const key = { current: "k-1" };
    const saveLog: string[] = [];
    await expect(deployFirstChat(key, async (k) => { saveLog.push(k); return true; }, BODY)).rejects.toBeInstanceOf(DeployUnanswered);
    expect(key.current).toBe("k-1");
    expect(saveLog).toEqual(["k-1"]);
  });
});
