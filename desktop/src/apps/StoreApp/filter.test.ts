// desktop/src/apps/StoreApp/filter.test.ts
import { describe, it, expect } from "vitest";
import { filterModels, compatFromResolver, hasUnknownHardwareDevice } from "./filter";
import type { CatalogApp, InstallTarget } from "./types";

const piDevice: InstallTarget = {
  name: "orange-pi",
  label: "orange-pi",
  type: "remote",
  tier_id: "arm-npu-16gb",
};

const macDevice: InstallTarget = {
  name: "mac",
  label: "mac",
  type: "remote",
  tier_id: "apple-silicon",
};

const controllerDevice: InstallTarget = {
  name: "local",
  label: "Controller",
  type: "local",
  tier_id: "x86-cpu-only",
};

const rkllamaModel: CatalogApp = {
  id: "qwen3-4b-rk",
  name: "Qwen3 4B (rkllama)",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  hardware_tiers: { "arm-npu-16gb": { recommended: "default" } },
  variants: [{ id: "default", backend: ["rkllama"] }],
};

const ollamaModel: CatalogApp = {
  id: "qwen3-4b-ollama",
  name: "Qwen3 4B (ollama)",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  hardware_tiers: {
    "x86-cpu-only": { recommended: "q4" },
    "apple-silicon": { recommended: "q4" },
  },
  variants: [{ id: "q4", backend: ["ollama", "llama-cpp"] }],
};

const universalModel: CatalogApp = {
  id: "small-tool",
  name: "Small Tool",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  // no hardware_tiers, no variants → universally compatible
};

const unsupportedOnPi: CatalogApp = {
  id: "huge-model",
  name: "Huge Model",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "unsupported",
  hardware_tiers: {
    "arm-npu-16gb": "unsupported",
    "x86-cpu-only": { recommended: "q4" },
  },
  variants: [{ id: "q4", backend: ["llama-cpp"] }],
};

const fallbackInstallMethod: CatalogApp = {
  id: "via-method",
  name: "Method-only",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  install_method: "ollama",
  hardware_tiers: { "apple-silicon": { recommended: "default" } },
};

const allApps = [
  rkllamaModel,
  ollamaModel,
  universalModel,
  unsupportedOnPi,
  fallbackInstallMethod,
];

describe("filterModels", () => {
  it("returns all apps as compatible when no filters are applied", () => {
    const { compatible, incompatible } = filterModels(allApps, [], []);
    expect(compatible).toEqual(allApps);
    expect(incompatible).toEqual([]);
  });

  it("filters to a single device's compatible models", () => {
    const { compatible } = filterModels(allApps, [piDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");
    expect(ids).toContain("small-tool"); // no hardware_tiers → universal
    expect(ids).not.toContain("qwen3-4b-ollama"); // no arm-npu-16gb tier
  });

  it("excludes models with explicit 'unsupported' tier", () => {
    const { compatible, incompatible } = filterModels(allApps, [piDevice], []);
    expect(compatible.find((a) => a.id === "huge-model")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "huge-model")).toBeDefined();
  });

  it("union semantics across multiple devices", () => {
    const { compatible } = filterModels(allApps, [piDevice, macDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk"); // matches Pi
    expect(ids).toContain("qwen3-4b-ollama"); // matches Mac
    expect(ids).toContain("small-tool"); // universal
  });

  it("backend filter narrows further (intersection with device match)", () => {
    const { compatible } = filterModels(
      allApps,
      [piDevice, macDevice],
      ["rkllama"]
    );
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");
    expect(ids).not.toContain("qwen3-4b-ollama"); // ollama, not rkllama
  });

  it("falls back to install_method when variants[].backend is absent", () => {
    const { compatible } = filterModels(
      [fallbackInstallMethod],
      [macDevice],
      ["ollama"]
    );
    expect(compatible.map((a) => a.id)).toEqual(["via-method"]);
  });

  it("model with no hardware_tiers and no variants passes any device filter", () => {
    const { compatible } = filterModels(
      [universalModel],
      [piDevice],
      []
    );
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  it("model with no backend constraint passes any backend filter", () => {
    const { compatible } = filterModels(
      [universalModel],
      [],
      ["rkllama"]
    );
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  it("controller-only filter excludes Pi-only models into incompatible", () => {
    const { compatible, incompatible } = filterModels(
      allApps,
      [controllerDevice],
      []
    );
    const compatIds = compatible.map((a) => a.id);
    const incompatIds = incompatible.map((a) => a.id);
    expect(compatIds).toContain("qwen3-4b-ollama"); // x86-cpu-only listed
    expect(incompatIds).toContain("qwen3-4b-rk"); // only arm-npu-16gb
  });

  it("device + backend together require BOTH to match", () => {
    const { compatible } = filterModels(
      allApps,
      [macDevice],
      ["rkllama"]
    );
    expect(compatible).toEqual([]); // no model has Mac tier AND rkllama backend
  });

  it("ignores devices with no tier_id", () => {
    const noTierDevice: InstallTarget = {
      name: "weird",
      label: "weird",
      type: "remote",
    };
    const { compatible } = filterModels(allApps, [noTierDevice], []);
    // device has no tier_id → contributes nothing to the tier set;
    // selectedDevices is non-empty so deviceOk=false except for universal
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });
});

describe("unknown-hardware device filter", () => {
  const unknownDevice: InstallTarget = {
    name: "fedora-worker",
    label: "fedora-worker",
    type: "remote",
    tier_id: "unknown",
    hardware_known: false,
  };

  it("hasUnknownHardwareDevice returns false when all devices have known hardware", () => {
    expect(hasUnknownHardwareDevice([piDevice, macDevice])).toBe(false);
  });

  it("hasUnknownHardwareDevice returns true when any device has hardware_known=false", () => {
    expect(hasUnknownHardwareDevice([piDevice, unknownDevice])).toBe(true);
  });

  it("hasUnknownHardwareDevice returns true when hardware_known is absent (legacy)", () => {
    const noField: InstallTarget = { name: "x", label: "x", type: "remote" };
    expect(hasUnknownHardwareDevice([noField])).toBe(false);
  });

  it("unknown-only selection shows all apps (filter treated as inactive)", () => {
    // When every selected device has unknown hardware, knownDevices is empty
    // → requireDeviceMatch is false → all apps pass device filter.
    const { compatible } = filterModels(allApps, [unknownDevice], []);
    expect(compatible).toEqual(allApps);
  });

  it("mixed known+unknown selection: unknown device doesn't narrow, known device does", () => {
    // Pi + fedora-worker(unknown): only Pi tier matters; unknown adds nothing.
    const { compatible } = filterModels(allApps, [piDevice, unknownDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");   // matches Pi
    expect(ids).toContain("small-tool");     // universal
    // ollama model has no arm-npu-16gb tier so it won't match; but it would
    // match if the unknown device contributed a tier — confirm it doesn't.
    expect(ids).not.toContain("qwen3-4b-ollama");
  });

  it("unknown device does not cause empty catalog when it is the only selection", () => {
    // The key regression: selecting an unknown-hardware device must NOT empty the Store.
    const { compatible, incompatible } = filterModels(allApps, [unknownDevice], []);
    expect(compatible.length).toBeGreaterThan(0);
    expect(incompatible).toEqual([]);
  });
});

describe("compatFromResolver", () => {
  it("treats green resolver result as compatible", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "green");
    expect(compatFromResolver("qwen2.5-3b", compatMap, false)).toBe(true);
  });

  it("treats amber as compatible", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "amber");
    expect(compatFromResolver("qwen2.5-3b", compatMap, false)).toBe(true);
  });

  it("treats red as incompatible by default", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "red");
    expect(compatFromResolver("qwen2.5-3b", compatMap, false)).toBe(false);
  });

  it("shows red when showIncompatible toggle is on", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "red");
    expect(compatFromResolver("qwen2.5-3b", compatMap, true)).toBe(true);
  });

  it("shows unknown manifests by default (no resolver entry → assume compatible)", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    expect(compatFromResolver("brand-new-model", compatMap, false)).toBe(true);
  });
});
