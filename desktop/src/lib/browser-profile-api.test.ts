import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listProfiles,
  createProfile,
  renameProfile,
  deleteProfile,
} from "./browser-profile-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("listProfiles", () => {
  it("returns the profiles array on 200", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        profiles: [
          { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
        ],
      }),
    });
    const out = await listProfiles();
    expect(out.length).toBe(1);
    expect(out[0].profile_id).toBe("personal");
  });

  it("returns [] on 401 silently", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await listProfiles()).toEqual([]);
  });
});

describe("createProfile", () => {
  it("POSTs name + color and returns the new profile", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ profile_id: "research", name: "Research", color: "#abc123", created_at: 1 }),
    });
    global.fetch = fetchMock;

    const out = await createProfile({ name: "Research", color: "#abc123" });
    expect(out?.profile_id).toBe("research");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/profiles");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.name).toBe("Research");
    expect(body.color).toBe("#abc123");
  });

  it("returns null on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 400 });
    expect(await createProfile({ name: "" })).toBeNull();
  });
});

describe("renameProfile", () => {
  it("PATCHes name + color to the keyed URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ profile_id: "personal", name: "New Name", color: null, created_at: 0 }),
    });
    global.fetch = fetchMock;

    await renameProfile("personal", { name: "New Name" });

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/profiles/personal");
    expect(opts.method).toBe("PATCH");
    expect(JSON.parse(opts.body)).toEqual({ name: "New Name" });
  });

  it("URL-encodes the profileId", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock;
    await renameProfile("with space", { name: "x" });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/profiles/with%20space");
  });
});

describe("deleteProfile", () => {
  it("issues DELETE and returns true on 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    global.fetch = fetchMock;
    expect(await deleteProfile("temp")).toBe(true);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/desktop/browser/profiles/temp");
    expect(opts.method).toBe("DELETE");
  });

  it("returns false on non-ok", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 400 });
    expect(await deleteProfile("personal")).toBe(false);
  });
});
