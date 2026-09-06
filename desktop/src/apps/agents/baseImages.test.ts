import { describe, it, expect } from "vitest";
import {
  formatBytes,
  frameworkLabel,
  parseBaseImagesResponse,
} from "./baseImages";

describe("formatBytes", () => {
  it("renders 0 B for zero, negative and non-finite input", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(-5)).toBe("0 B");
    expect(formatBytes(NaN)).toBe("0 B");
    expect(formatBytes(Infinity)).toBe("0 B");
  });

  it("renders whole bytes without a decimal", () => {
    expect(formatBytes(512)).toBe("512 B");
  });

  it("scales into KB, MB and GB with one decimal", () => {
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(2.25 * 1024 * 1024 * 1024)).toBe("2.3 GB");
  });
});

describe("frameworkLabel", () => {
  it("returns the framework name when present", () => {
    expect(frameworkLabel("hermes")).toBe("hermes");
  });

  it("falls back to 'generic' for the generic base", () => {
    expect(frameworkLabel(null)).toBe("generic");
  });
});

describe("parseBaseImagesResponse", () => {
  it("maps a well-formed response into rows and aggregates", () => {
    const view = parseBaseImagesResponse({
      images: [
        {
          alias: "taos-hermes-base",
          architecture: "aarch64",
          size: "412.50MiB",
          size_bytes: 432537600,
          uploaded_at: "2026/06/20 10:11 UTC",
          framework: "hermes",
        },
        {
          alias: "taos-base",
          architecture: "aarch64",
          size: "300MiB",
          size_bytes: 314572800,
          uploaded_at: "2026/06/19 09:00 UTC",
          framework: null,
        },
      ],
      total_size_bytes: 747110400,
      prefetch_enabled: true,
      incus_available: true,
    });

    expect(view.images).toHaveLength(2);
    expect(view.images[0]).toEqual({
      alias: "taos-hermes-base",
      architecture: "aarch64",
      size: "412.50MiB",
      sizeBytes: 432537600,
      uploadedAt: "2026/06/20 10:11 UTC",
      framework: "hermes",
    });
    expect(view.images[1]!.framework).toBeNull();
    expect(view.totalSizeBytes).toBe(747110400);
    expect(view.prefetchEnabled).toBe(true);
    expect(view.incusAvailable).toBe(true);
  });

  it("degrades a missing/incus-unavailable response to safe defaults", () => {
    const view = parseBaseImagesResponse({
      images: [],
      total_size_bytes: 0,
      prefetch_enabled: false,
      incus_available: false,
    });
    expect(view.images).toEqual([]);
    expect(view.totalSizeBytes).toBe(0);
    expect(view.prefetchEnabled).toBe(false);
    expect(view.incusAvailable).toBe(false);
  });

  it("tolerates entirely malformed input without throwing", () => {
    expect(parseBaseImagesResponse(null).images).toEqual([]);
    expect(parseBaseImagesResponse("nope").totalSizeBytes).toBe(0);
    expect(parseBaseImagesResponse({}).incusAvailable).toBe(false);
  });

  it("fills defaults for partial image entries", () => {
    const view = parseBaseImagesResponse({ images: [{ alias: "taos-base" }] });
    expect(view.images[0]).toEqual({
      alias: "taos-base",
      architecture: "",
      size: "",
      sizeBytes: 0,
      uploadedAt: "",
      framework: null,
    });
  });
});
