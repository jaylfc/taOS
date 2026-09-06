import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadDiskFile, attachmentFromPath } from "./chat-attachments-api";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("uploadDiskFile", () => {
  it("POSTs to /api/chat/upload and returns normalized AttachmentRecord", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "att-1",
        filename: "photo.png",
        content_type: "image/png",
        size: 1024,
        url: "/files/att-1",
      }),
    });
    global.fetch = fetchMock;

    const file = new File(["dummy"], "photo.png", { type: "image/png" });
    const result = await uploadDiskFile(file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/upload");
    expect(opts.method).toBe("POST");
    expect(opts.body).toBeInstanceOf(FormData);

    expect(result).toEqual({
      filename: "photo.png",
      mime_type: "image/png",
      size: 1024,
      url: "/files/att-1",
      source: "disk",
    });
  });

  it("includes channel_id in FormData when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "att-2",
        filename: "doc.pdf",
        content_type: "application/pdf",
        size: 2048,
        url: "/files/att-2",
      }),
    });
    global.fetch = fetchMock;

    const file = new File(["dummy"], "doc.pdf", { type: "application/pdf" });
    await uploadDiskFile(file, "ch-42");

    const [, opts] = fetchMock.mock.calls[0];
    const form = opts.body as FormData;
    expect(form.get("channel_id")).toBe("ch-42");
  });

  it("falls back to mime_type field when content_type is absent", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "att-3",
        filename: "data.bin",
        mime_type: "application/octet-stream",
        size: 512,
        url: "/files/att-3",
      }),
    });

    const file = new File(["dummy"], "data.bin");
    const result = await uploadDiskFile(file);

    expect(result.mime_type).toBe("application/octet-stream");
  });

  it("throws on non-ok response with error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "file too large" }),
    });

    const file = new File(["dummy"], "big.bin");
    await expect(uploadDiskFile(file)).rejects.toThrow("file too large");
  });

  it("throws on non-ok response without error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    const file = new File(["dummy"], "x.bin");
    await expect(uploadDiskFile(file)).rejects.toThrow("HTTP 500");
  });
});

describe("attachmentFromPath", () => {
  it("POSTs to /api/chat/attachments/from-path and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        filename: "readme.md",
        mime_type: "text/markdown",
        size: 4096,
        url: "/workspace/readme.md",
        source: "workspace",
      }),
    });
    global.fetch = fetchMock;

    const result = await attachmentFromPath({
      path: "/workspace/readme.md",
      source: "workspace",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/attachments/from-path");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.path).toBe("/workspace/readme.md");
    expect(body.source).toBe("workspace");

    expect(result).toEqual({
      filename: "readme.md",
      mime_type: "text/markdown",
      size: 4096,
      url: "/workspace/readme.md",
      source: "workspace",
    });
  });

  it("sends slug when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        filename: "notes.txt",
        mime_type: "text/plain",
        size: 128,
        url: "/agent/notes.txt",
        source: "agent-workspace",
      }),
    });
    global.fetch = fetchMock;

    await attachmentFromPath({
      path: "/agent/notes.txt",
      source: "agent-workspace",
      slug: "abc123",
    });

    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.slug).toBe("abc123");
  });

  it("throws on non-ok response with error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "path not found" }),
    });

    await expect(
      attachmentFromPath({ path: "/missing.txt", source: "workspace" }),
    ).rejects.toThrow("path not found");
  });

  it("throws on non-ok response without error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(
      attachmentFromPath({ path: "/x.txt", source: "workspace" }),
    ).rejects.toThrow("HTTP 500");
  });
});
