// desktop/src/apps/StoreApp/compat-visuals.test.ts
import { describe, it, expect } from "vitest";
import { compatVisuals } from "./compat-visuals";
import type { ResolveResponse } from "./resolver-types";

describe("compatVisuals", () => {
  it("returns no border when undefined (resolver hasn't classified)", () => {
    const v = compatVisuals(undefined);
    expect(v.borderClass).toBe("");
    expect(v.tooltip).toBe("");
  });

  it("green + ok 'use' → emerald border + accelerated tooltip", () => {
    const resp: ResolveResponse = {
      result: "ok",
      backend_id: "rk-llama-cpp",
      variant_id: "q4_k_m",
      action: "use",
      compat: "green",
    };
    const v = compatVisuals(resp);
    expect(v.borderClass).toContain("border-l-emerald");
    expect(v.tooltip).toMatch(/accelerated/i);
  });

  it("green + install_chain → emerald border + install-first tooltip", () => {
    const resp: ResolveResponse = {
      result: "ok",
      backend_id: "rk-llama-cpp",
      variant_id: "q4_k_m",
      action: "install_chain",
      compat: "green",
    };
    const v = compatVisuals(resp);
    expect(v.borderClass).toContain("border-l-emerald");
    expect(v.tooltip).toMatch(/installed/i);
  });

  it("amber on err → amber border + reason from near_miss.blocked_by", () => {
    const resp: ResolveResponse = {
      result: "err",
      reason: "no compatible variant",
      near_miss: { variant: "q8", blocked_by: "ram", short_by_mb: 2048 },
      suggestions: ["q4_k_m"],
      compat: "amber",
    };
    const v = compatVisuals(resp);
    expect(v.borderClass).toContain("border-l-amber");
    expect(v.tooltip).toMatch(/RAM/);
    expect(v.tooltip).toContain("2048 MB");
  });

  it("amber on ok → amber border + CPU-only tooltip", () => {
    const resp: ResolveResponse = {
      result: "ok",
      backend_id: "llama-cpp",
      variant_id: "q4_k_m",
      action: "use",
      compat: "amber",
    };
    const v = compatVisuals(resp);
    expect(v.borderClass).toContain("border-l-amber");
    expect(v.tooltip).toMatch(/CPU/);
  });

  it("red on err → red border + 'Won't run' tooltip with reason", () => {
    const resp: ResolveResponse = {
      result: "err",
      reason: "no variant fits",
      near_miss: { variant: "q4_k_m", blocked_by: "vram", short_by_mb: 4096 },
      suggestions: [],
      compat: "red",
    };
    const v = compatVisuals(resp);
    expect(v.borderClass).toContain("border-l-red");
    expect(v.tooltip).toMatch(/Won't run/);
    expect(v.tooltip).toMatch(/VRAM/);
    expect(v.tooltip).toContain("4096 MB");
  });

  it("red without short_by_mb → tooltip omits MB suffix", () => {
    const resp: ResolveResponse = {
      result: "err",
      reason: "no compatible target",
      near_miss: { blocked_by: "target" },
      suggestions: [],
      compat: "red",
    };
    const v = compatVisuals(resp);
    expect(v.tooltip).toMatch(/Won't run/);
    expect(v.tooltip).not.toMatch(/MB/);
  });

  it("falls back to reason text when blocked_by is missing", () => {
    const resp: ResolveResponse = {
      result: "err",
      reason: "totally unique failure",
      near_miss: {},
      suggestions: [],
      compat: "red",
    };
    const v = compatVisuals(resp);
    expect(v.tooltip).toContain("totally unique failure");
  });
});
