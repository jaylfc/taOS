import { describe, it, expect } from "vitest";
import {
  MODEL_EVENT_LOAD,
  mergeModelActivityEvents,
  formatDuration,
  formatTokens,
  modelActivityQuery,
  modelActivityStreamUrl,
  modelIconKind,
  modelEventLabel,
  type ModelActivityEvent,
} from "./model-activity-api";

function ev(overrides: Partial<ModelActivityEvent> = {}): ModelActivityEvent {
  return {
    seq: 1,
    ts: 1_700_000_000,
    event: MODEL_EVENT_LOAD,
    model: "qwen3-8b",
    worker: "controller",
    backend: "rkllama",
    duration_ms: null,
    tokens_in: null,
    tokens_out: null,
    token_rate: null,
    reason: null,
    detail: {},
    ...overrides,
  };
}

describe("formatDuration", () => {
  it("renders sub-second, sub-minute and minute-spanning durations", () => {
    expect(formatDuration(250)).toBe("250 ms");
    expect(formatDuration(2500)).toBe("2.5 s");
    expect(formatDuration(42_000)).toBe("42 s");
    expect(formatDuration(90_000)).toBe("1m 30s");
    expect(formatDuration(4 * 60_000 + 32_000)).toBe("4m 32s");
  });

  it("never emits an out-of-range seconds field when rounding up", () => {
    // 59500-59999 ms used to render as "59m 60s".
    expect(formatDuration(59_500)).toBe("1m 0s");
    expect(formatDuration(59_999)).toBe("1m 0s");
    expect(formatDuration(119_999)).toBe("2m 0s");
  });

  it("returns an empty string for a missing duration", () => {
    expect(formatDuration(null)).toBe("");
    expect(formatDuration(undefined)).toBe("");
  });
});

describe("formatTokens", () => {
  it("omits the rate when the backend did not report one", () => {
    expect(formatTokens(ev({ tokens_out: 12 }))).toBe("12 tok");
  });

  it("includes the output token rate when present", () => {
    expect(formatTokens(ev({ tokens_out: 12, token_rate: 20.5 }))).toBe("12 tok · 20.5 tok/s");
  });

  it("is empty when no output tokens were recorded", () => {
    expect(formatTokens(ev())).toBe("");
  });
});

describe("mergeModelActivityEvents", () => {
  it("de-duplicates by seq and orders newest first", () => {
    const merged = mergeModelActivityEvents(
      [ev({ seq: 3, model: "c" }), ev({ seq: 1, model: "a" })],
      [ev({ seq: 2, model: "b" }), ev({ seq: 1, model: "a" })],
    );
    expect(merged.map((e) => e.seq)).toEqual([3, 2, 1]);
  });

  it("caps the merged feed at the requested size", () => {
    const many = Array.from({ length: 10 }, (_, i) => ev({ seq: i + 1 }));
    expect(mergeModelActivityEvents(many, [], 4).map((e) => e.seq)).toEqual([10, 9, 8, 7]);
  });

  it("keeps a live frame when the history window arrives without it", () => {
    const merged = mergeModelActivityEvents(
      [ev({ seq: 1, model: "from-history" })],
      [ev({ seq: 9, model: "live-only" })],
    );
    expect(merged.map((e) => e.model)).toEqual(["live-only", "from-history"]);
  });
});

describe("query building", () => {
  it("emits limit=0 (a meaningful value: no SSE replay)", () => {
    expect(modelActivityStreamUrl({ limit: 0 })).toBe("/api/activity/models/stream?limit=0");
  });

  it("omits unset filters and sets the rest", () => {
    expect(modelActivityQuery({})).toBe("");
    expect(modelActivityQuery({ worker: "pi-4", limit: 200 })).toBe("?worker=pi-4&limit=200");
  });
});

describe("modelIconKind", () => {
  it("picks an embedding icon for embedding models and a brain otherwise", () => {
    expect(modelIconKind("bge-large-en")).toBe("vector");
    expect(modelIconKind("nomic-embed-text")).toBe("vector");
    expect(modelIconKind("qwen3-8b")).toBe("brain");
  });

  it("recognises the other catalog purposes", () => {
    expect(modelIconKind("flux-schnell")).toBe("image");
    expect(modelIconKind("whisper-large")).toBe("audio");
    expect(modelIconKind("llava-1.6")).toBe("vision");
    expect(modelIconKind("qwen-coder-7b")).toBe("code");
  });
});

describe("modelEventLabel", () => {
  it("labels known event types and falls back to the raw string", () => {
    expect(modelEventLabel("model.load")).toBe("Loaded");
    expect(modelEventLabel("model.something-new")).toBe("model.something-new");
  });
});