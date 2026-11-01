import { describe, it, expect } from "vitest";
import { formatSize, parseChoice, pickOnDeviceModel, type CatalogModel } from "./model";

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
