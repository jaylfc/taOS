import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { listAgentGrants, revokeAgentProjectGrants } from "./agent-grants";

/* ------------------------------------------------------------------ */
/*  agent-grants API client — exercises the real revoke-agent wiring    */
/* ------------------------------------------------------------------ */

function ok(response: { ok: boolean; json: () => Promise<unknown> }) {
  return response;
}

function jsonOk(body: unknown) {
  return ok({ ok: true, json: () => Promise.resolve(body) });
}

function jsonErr(status: number, body: unknown) {
  return ok({ ok: false, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listAgentGrants", () => {
  it("GETs the grants feed narrowed to the agent and parses the grants array", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonOk({
        grants: [
          {
            canonical_id: "c1",
            scope: "project_tasks",
            tier: "once",
            project_id: "p1",
            granted_at: "2026-01-01T00:00:00Z",
            expires_at: null,
          },
        ],
      }) as never,
    );

    const grants = await listAgentGrants("c1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/agents/registry/grants?canonical_id=c1",
      { credentials: "include" },
    );
    expect(grants).toHaveLength(1);
    expect(grants[0].scope).toBe("project_tasks");
    expect(grants[0].project_id).toBe("p1");
  });
});

describe("revokeAgentProjectGrants", () => {
  it("POSTs to the revoke-agent route with the CSRF header and the posted body", async () => {
    document.cookie = "csrf_token=tok123";
    vi.mocked(fetch).mockResolvedValue(
      jsonOk({
        canonical_id: "c1",
        project_id: "p1",
        revoked_scopes: ["project_tasks"],
        active_scopes: [],
      }) as never,
    );

    const res = await revokeAgentProjectGrants("p1", "c1", ["project_tasks"]);

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/projects/p1/members/revoke-agent");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    const headers = new Headers(init.headers);
    expect(headers.get("X-CSRF-Token")).toBe("tok123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      canonical_id: "c1",
      scopes: ["project_tasks"],
    });
    expect(res.revoked_scopes).toEqual(["project_tasks"]);
  });

  it("defaults to an empty scopes list so an omitted list revokes everything on the project", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonOk({
        canonical_id: "c1",
        project_id: "p1",
        revoked_scopes: [],
        active_scopes: [],
      }) as never,
    );

    await revokeAgentProjectGrants("p1", "c1");

    const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      canonical_id: "c1",
      scopes: [],
    });
  });

  it("surfaces the server detail message on a non-ok response", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonErr(403, { detail: "forbidden" }) as never);

    await expect(revokeAgentProjectGrants("p1", "c1")).rejects.toThrow("forbidden");
  });
});