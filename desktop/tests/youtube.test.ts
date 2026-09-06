import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  ingestVideo,
  downloadVideo,
  getDownloadStatus,
  getTranscript,
  formatTimestamp,
} from "../src/lib/youtube";
import type { DownloadStatus, TranscriptSegment } from "../src/lib/youtube";

/* ------------------------------------------------------------------ */
/*  Mock helpers                                                       */
/* ------------------------------------------------------------------ */

function mockFetchJson(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(data),
  });
}

function mockFetchFail() {
  return vi.fn().mockRejectedValue(new Error("network error"));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ */
/*  ingestVideo                                                        */
/* ------------------------------------------------------------------ */

describe("ingestVideo", () => {
  it("posts the URL and returns id + status", async () => {
    globalThis.fetch = mockFetchJson({ id: "yt-001", status: "pending" });
    const result = await ingestVideo("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    expect(result).toEqual({ id: "yt-001", status: "pending" });

    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/youtube/ingest");
    const body = JSON.parse(call[1].body as string);
    expect(body.url).toBe("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  });

  it("returns null on non-ok response", async () => {
    globalThis.fetch = mockFetchJson({ error: "invalid url" }, 400);
    const result = await ingestVideo("bad-url");
    expect(result).toBeNull();
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await ingestVideo("https://www.youtube.com/watch?v=test");
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  downloadVideo                                                      */
/* ------------------------------------------------------------------ */

describe("downloadVideo", () => {
  it("posts item_id and quality", async () => {
    globalThis.fetch = mockFetchJson({ status: "downloading" });
    const result = await downloadVideo("yt-001", "720p");
    expect(result).toEqual({ status: "downloading" });

    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/youtube/download");
    const body = JSON.parse(call[1].body as string);
    expect(body.item_id).toBe("yt-001");
    expect(body.quality).toBe("720p");
  });

  it("returns null on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await downloadVideo("yt-001", "1080p");
    expect(result).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  getDownloadStatus                                                  */
/* ------------------------------------------------------------------ */

describe("getDownloadStatus", () => {
  it("returns download status with file_size and path", async () => {
    const mockStatus: DownloadStatus = {
      status: "complete",
      file_size: "450 MB",
      path: "/media/yt-001.mp4",
    };
    globalThis.fetch = mockFetchJson(mockStatus);
    const result = await getDownloadStatus("yt-001");
    expect(result.status).toBe("complete");
    expect(result.file_size).toBe("450 MB");
    expect(result.path).toBe("/media/yt-001.mp4");

    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/youtube/download-status/yt-001");
  });

  it("returns idle status on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getDownloadStatus("yt-001");
    expect(result.status).toBe("idle");
  });

  it("URL-encodes the item ID", async () => {
    globalThis.fetch = mockFetchJson({ status: "idle" });
    await getDownloadStatus("item/with/slashes");
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toContain("item%2Fwith%2Fslashes");
  });
});

/* ------------------------------------------------------------------ */
/*  getTranscript                                                      */
/* ------------------------------------------------------------------ */

describe("getTranscript", () => {
  const MOCK_SEGMENTS: TranscriptSegment[] = [
    { start: 0, end: 5, text: "Hello world" },
    { start: 5, end: 12, text: "This is a test" },
  ];

  it("returns transcript segments", async () => {
    globalThis.fetch = mockFetchJson({ segments: MOCK_SEGMENTS });
    const result = await getTranscript("yt-001");
    expect(result).toHaveLength(2);
    expect(result[0]?.text).toBe("Hello world");
    expect(result[1]?.start).toBe(5);

    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/youtube/transcript/yt-001");
  });

  it("returns empty array on error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getTranscript("yt-001");
    expect(result).toEqual([]);
  });

  it("returns empty array when segments is missing", async () => {
    globalThis.fetch = mockFetchJson({ error: "no transcript" }, 404);
    const result = await getTranscript("yt-001");
    expect(result).toEqual([]);
  });
});

/* ------------------------------------------------------------------ */
/*  formatTimestamp                                                    */
/* ------------------------------------------------------------------ */

describe("formatTimestamp", () => {
  it("formats seconds under a minute as MM:SS", () => {
    expect(formatTimestamp(0)).toBe("00:00");
    expect(formatTimestamp(5)).toBe("00:05");
    expect(formatTimestamp(59)).toBe("00:59");
  });

  it("formats minutes as MM:SS", () => {
    expect(formatTimestamp(60)).toBe("01:00");
    expect(formatTimestamp(90)).toBe("01:30");
    expect(formatTimestamp(3599)).toBe("59:59");
  });

  it("formats hours as HH:MM:SS", () => {
    expect(formatTimestamp(3600)).toBe("1:00:00");
    expect(formatTimestamp(3661)).toBe("1:01:01");
    expect(formatTimestamp(7322)).toBe("2:02:02");
  });

  it("floors fractional seconds", () => {
    expect(formatTimestamp(90.9)).toBe("01:30");
  });
});
