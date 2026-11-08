import { describe, it, expect, vi, afterEach } from "vitest";
import { formatSize, parseChoice, pickOnDeviceModel, waitForDownload, type CatalogModel } from "./model";

const gguf = (id: string, size_mb: number, compatibility = "green", extra = {}) => ({
  id,
  format: "gguf",
  size_mb,
  compatibility,
  ...extra,
});

const model = (id: string, variants: CatalogModel["variants"], extra: Partial<CatalogModel> = {}): CatalogModel => ({
  id,
  name: id.toUpperCase(),
  capabilities: ["chat"],
  variants,
  ...extra,
});

describe("on-device model pick", () => {
  it("puts the preferred list first, over a smaller model", () => {
    const pick = pickOnDeviceModel(
      [model("tiny-chat", [gguf("q4", 300)]), model("gemma-3-1b", [gguf("q4", 800)])],
      "p",
    );
    expect(pick?.app_id).toBe("gemma-3-1b");
  });

  it("outside the preferred list, takes the smallest model that fits", () => {
    const pick = pickOnDeviceModel(
      [model("big-chat", [gguf("q4", 3000)]), model("small-chat", [gguf("q4", 900)])],
      "p",
    );
    expect(pick?.app_id).toBe("small-chat");
  });

  it("never offers a tight fit, a non-GGUF, an NPU-only build or a non-chat model", () => {
    const pick = pickOnDeviceModel(
      [
        model("qwen3-1.7b", [gguf("q4", 1100, "yellow")]),
        model("gemma-3-1b", [{ id: "mlx", format: "mlx", size_mb: 800, compatibility: "green" }]),
        model("llama-3.2-1b", [gguf("rk", 900, "green", { requires: { backends: [{ id: "rkllama" }] } })]),
        model("embed", [gguf("q4", 100)], { capabilities: ["embedding"] }),
      ],
      "p",
    );
    expect(pick).toBeNull();
  });

  it("uses the variant the catalog recommends for this device profile", () => {
    const pick = pickOnDeviceModel(
      [model("qwen3-1.7b", [gguf("q8", 1900), gguf("q4", 1100)], { hardware_tiers: { p: { recommended: "q4" } } })],
      "p",
    );
    expect(pick).toMatchObject({ variant_id: "q4", size_mb: 1100, downloaded: false });
  });

  it("falls back to the first variant that fits when the recommended one does not", () => {
    const pick = pickOnDeviceModel(
      [
        model("qwen3-1.7b", [gguf("q4", 1100), gguf("q8", 1900, "red")], {
          hardware_tiers: { p: { recommended: "q8" } },
        }),
      ],
      "p",
    );
    expect(pick?.variant_id).toBe("q4");
  });

  it("formats sizes and reads back only a well-formed saved choice", () => {
    expect(formatSize(1100)).toBe("1.1 GB");
    expect(formatSize(640.4)).toBe("640 MB");
    expect(parseChoice({ app_id: "a", variant_id: "b" })).toEqual({
      app_id: "a",
      variant_id: "b",
      name: "a",
      size_mb: 0,
      downloaded: false,
    });
    expect(parseChoice({ app_id: "a" })).toBeUndefined();
    expect(parseChoice("a")).toBeUndefined();
  });
});

describe("waitForDownload", () => {
  afterEach(() => vi.unstubAllGlobals());
  const choice = { app_id: "m", variant_id: "q4", name: "M", size_mb: 900, downloaded: false };
  function tasks(...replies: [number, unknown][]) {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      const [status, body] = replies.length > 1 ? replies.shift()! : replies[0];
      return new Response(JSON.stringify(body), { status });
    }));
    return urls;
  }

  it("resolves only once the download reports complete, with progress on the way", async () => {
    const urls = tasks([200, { status: "downloading", percent: 40 }], [200, { status: "complete", percent: 100 }]);
    const seen: number[] = [];
    await waitForDownload(choice, (p) => seen.push(p), 0);
    expect(urls).toEqual(["/api/models/downloads/m-q4", "/api/models/downloads/m-q4"]);
    expect(seen).toContain(40);
  });

  it("uses the download id the step recorded", async () => {
    const urls = tasks([200, { status: "complete" }]);
    await waitForDownload({ ...choice, download_id: "dl-7" }, () => {}, 0);
    expect(urls).toEqual(["/api/models/downloads/dl-7"]);
  });

  it("names the download's error when it failed", async () => {
    tasks([200, { status: "error", error: "Disk full" }]);
    await expect(waitForDownload(choice, () => {}, 0)).rejects.toThrow(/Disk full/);
  });

  it("does not wait for a model that is already on the device", async () => {
    const urls = tasks([200, { status: "downloading" }]);
    await waitForDownload({ ...choice, downloaded: true }, () => {}, 0);
    expect(urls).toEqual([]);
  });

  it("stops waiting when the server has no record, so the deploy itself decides", async () => {
    tasks([404, { error: "not found" }]);
    await expect(waitForDownload(choice, () => {}, 0)).resolves.toBeUndefined();
  });

  it("keeps the download id through a saved-progress round trip", () => {
    expect(parseChoice({ ...choice, download_id: "dl-7" })?.download_id).toBe("dl-7");
  });

  it("rejects after 20 consecutive 500 polls", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({}), { status: 500 });
    }));
    await expect(waitForDownload(choice, () => {}, 0)).rejects.toThrow(/Couldn't check/);
    expect(fetch).toHaveBeenCalledTimes(20);
  });

  it("rejects after 20 consecutive network errors", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      throw new Error("network error");
    }));
    await expect(waitForDownload(choice, () => {}, 0)).rejects.toThrow(/Couldn't check/);
    expect(fetch).toHaveBeenCalledTimes(20);
  });

  it("resolves when 19 failures then ok then 19 failures then complete (counter resets on good poll)", async () => {
    const urls: string[] = [];
    let fetchCallCount = 0;
    const fetchMock = vi.fn(async (url: string) => {
      urls.push(url);
      fetchCallCount++;
      if (fetchCallCount <= 19) return new Response(JSON.stringify({ status: "downloading", percent: 10 }), { status: 500 });
      if (fetchCallCount === 20) return new Response(JSON.stringify({ status: "downloading", percent: 10 }), { status: 200 });
      if (fetchCallCount <= 39) return new Response(JSON.stringify({ status: "downloading", percent: 10 }), { status: 500 });
      return new Response(JSON.stringify({ status: "complete" }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    await waitForDownload(choice, () => {}, 0);
    expect(fetchMock).toHaveBeenCalledTimes(40);
  });
});
