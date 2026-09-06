import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ingestVideo,
  downloadVideo,
  getDownloadStatus,
  getTranscript,
  formatTimestamp,
} from "./youtube";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("ingestVideo", () => {
  it("returns id and status on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "abc", status: "queued" }),
    });
    global.fetch = fetchMock;

    const result = await ingestVideo("https://youtu.be/abc");
    expect(result).toEqual({ id: "abc", status: "queued" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await ingestVideo("https://youtu.be/abc")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await ingestVideo("https://youtu.be/abc")).toBeNull();
  });

  it("posts JSON body with url to /api/youtube/ingest", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ id: "x", status: "queued" }),
    });
    global.fetch = fetchMock;

    await ingestVideo("https://youtu.be/xyz");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/youtube/ingest");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.url).toBe("https://youtu.be/xyz");
  });
});

describe("downloadVideo", () => {
  it("returns status on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ status: "downloading" }),
    });
    global.fetch = fetchMock;

    const result = await downloadVideo("item-1", "best");
    expect(result).toEqual({ status: "downloading" });
  });

  it("returns null on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    expect(await downloadVideo("item-1", "best")).toBeNull();
  });

  it("returns null on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network failure"));
    expect(await downloadVideo("item-1", "best")).toBeNull();
  });

  it("posts item_id and quality to /api/youtube/download", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ status: "ok" }),
    });
    global.fetch = fetchMock;

    await downloadVideo("item-99", "720p");

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/youtube/download");
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    expect(body.item_id).toBe("item-99");
    expect(body.quality).toBe("720p");
  });
});

describe("getDownloadStatus", () => {
  it("returns download status on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ status: "downloading", file_size: "12MB" }),
    });
    global.fetch = fetchMock;

    const result = await getDownloadStatus("item-1");
    expect(result).toEqual({ status: "downloading", file_size: "12MB" });
  });

  it("returns idle fallback on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    expect(await getDownloadStatus("item-1")).toEqual({ status: "idle" });
  });

  it("returns idle fallback on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await getDownloadStatus("item-1")).toEqual({ status: "idle" });
  });

  it("encodes item_id in the URL path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ status: "idle" }),
    });
    global.fetch = fetchMock;

    await getDownloadStatus("item/with spaces");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("item%2Fwith%20spaces");
  });
});

describe("getTranscript", () => {
  it("returns segments array on 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        segments: [
          { start: 0, end: 5, text: "hello" },
          { start: 5, end: 10, text: "world" },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await getTranscript("item-1");
    expect(result).toHaveLength(2);
    expect(result[0].text).toBe("hello");
    expect(result[1].end).toBe(10);
  });

  it("returns [] on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    expect(await getTranscript("item-1")).toEqual([]);
  });

  it("returns [] on network error", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await getTranscript("item-1")).toEqual([]);
  });

  it("returns [] when segments is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ segments: null }),
    });
    expect(await getTranscript("item-1")).toEqual([]);
  });

  it("encodes item_id in the URL path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({ segments: [] }),
    });
    global.fetch = fetchMock;

    await getDownloadStatus("item/special");
    await getTranscript("item/special");

    const [url] = fetchMock.mock.calls[1];
    expect(url).toContain("item%2Fspecial");
  });
});

describe("formatTimestamp", () => {
  it("formats seconds under an hour as mm:ss", () => {
    expect(formatTimestamp(0)).toBe("00:00");
    expect(formatTimestamp(65)).toBe("01:05");
    expect(formatTimestamp(3599)).toBe("59:59");
  });

  it("formats seconds over an hour as h:mm:ss", () => {
    expect(formatTimestamp(3600)).toBe("1:00:00");
    expect(formatTimestamp(3661)).toBe("1:01:01");
  });

  it("floors fractional seconds", () => {
    expect(formatTimestamp(5.9)).toBe("00:05");
  });
});
