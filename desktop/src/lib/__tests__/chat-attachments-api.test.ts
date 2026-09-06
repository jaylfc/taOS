import { describe, it, expect, vi, beforeEach } from "vitest";
import { uploadDiskFile, attachmentFromPath } from "../chat-attachments-api";

describe("chat-attachments-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({
          filename: "f.png", mime_type: "image/png", size: 1,
          url: "/api/chat/files/abc-f.png", source: "disk",
        }),
      }),
    ) as unknown as typeof fetch;
  });

  it("uploadDiskFile POSTs multipart to /api/chat/upload", async () => {
    const f = new File(["x"], "f.png", { type: "image/png" });
    const rec = await uploadDiskFile(f);
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/upload",
      expect.objectContaining({ method: "POST" }),
    );
    expect(rec.filename).toBe("f.png");
  });

  it("attachmentFromPath POSTs workspace path", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({
          filename: "r.md", mime_type: "text/markdown", size: 10,
          url: "/api/chat/files/xyz-r.md", source: "workspace",
        }),
      }),
    ) as unknown as typeof fetch;
    const rec = await attachmentFromPath({
      path: "/workspaces/user/r.md", source: "workspace",
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/attachments/from-path",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "/workspaces/user/r.md", source: "workspace" }),
      }),
    );
    expect(rec.source).toBe("workspace");
  });

  it("throws on non-OK with server's error", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false, status: 413,
        json: () => Promise.resolve({ error: "file too large (100 MB max)" }),
      }),
    ) as unknown as typeof fetch;
    const f = new File(["x"], "big.bin");
    await expect(uploadDiskFile(f)).rejects.toThrow("file too large");
  });
});
