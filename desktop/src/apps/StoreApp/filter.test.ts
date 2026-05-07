// desktop/src/apps/StoreApp/filter.test.ts
import { describe, it, expect } from "vitest";
import { filterCatalog, compatFromResolver } from "./filter";
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

const x86VulkanDevice: InstallTarget = {
  name: "x86-gpu",
  label: "x86 GPU",
  type: "remote",
  tier_id: "x86-vulkan-8gb",
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

describe("filterCatalog", () => {
  it("returns all apps as compatible when no filters are applied", () => {
    const { compatible, incompatible } = filterCatalog(allApps, [], []);
    expect(compatible).toEqual(allApps);
    expect(incompatible).toEqual([]);
  });

  it("filters to a single device's compatible models", () => {
    const { compatible } = filterCatalog(allApps, [piDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");
    expect(ids).toContain("small-tool"); // no hardware_tiers → universal
    expect(ids).not.toContain("qwen3-4b-ollama"); // no arm-npu-16gb tier
  });

  it("excludes models with explicit 'unsupported' tier", () => {
    const { compatible, incompatible } = filterCatalog(allApps, [piDevice], []);
    expect(compatible.find((a) => a.id === "huge-model")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "huge-model")).toBeDefined();
  });

  it("union semantics across multiple devices", () => {
    const { compatible } = filterCatalog(allApps, [piDevice, macDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk"); // matches Pi
    expect(ids).toContain("qwen3-4b-ollama"); // matches Mac
    expect(ids).toContain("small-tool"); // universal
  });

  it("backend filter narrows further (intersection with device match)", () => {
    const { compatible } = filterCatalog(
      allApps,
      [piDevice, macDevice],
      ["rkllama"]
    );
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");
    expect(ids).not.toContain("qwen3-4b-ollama"); // ollama, not rkllama
  });

  it("falls back to install_method when variants[].backend is absent", () => {
    const { compatible } = filterCatalog(
      [fallbackInstallMethod],
      [macDevice],
      ["ollama"]
    );
    expect(compatible.map((a) => a.id)).toEqual(["via-method"]);
  });

  it("model with no hardware_tiers and no variants passes any device filter", () => {
    const { compatible } = filterCatalog(
      [universalModel],
      [piDevice],
      []
    );
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  it("model with no backend constraint passes any backend filter", () => {
    const { compatible } = filterCatalog(
      [universalModel],
      [],
      ["rkllama"]
    );
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  it("controller-only filter excludes Pi-only models into incompatible", () => {
    const { compatible, incompatible } = filterCatalog(
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
    const { compatible } = filterCatalog(
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
    const { compatible } = filterCatalog(allApps, [noTierDevice], []);
    // device has no tier_id → contributes nothing to the tier set;
    // selectedDevices is non-empty so deviceOk=false except for universal
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  // --- Non-model app type tests ---

  it("service with hardware_tiers is correctly filtered by device", () => {
    const armService: CatalogApp = {
      id: "arm-inference-svc",
      name: "ARM Inference Service",
      type: "service",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
      hardware_tiers: { "arm-npu-16gb": { recommended: "default" } },
      variants: [{ id: "default", backend: ["rkllama"] }],
    };
    const { compatible, incompatible } = filterCatalog([armService], [controllerDevice], []);
    expect(compatible.find((a) => a.id === "arm-inference-svc")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "arm-inference-svc")).toBeDefined();
  });

  it("agent-framework with hardware_tiers is filtered when tier doesn't match", () => {
    const npuFramework: CatalogApp = {
      id: "npu-agent-fw",
      name: "NPU Agent Framework",
      type: "agent-framework",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
      hardware_tiers: {
        "arm-npu-16gb": { recommended: "default" },
        "arm-npu-8gb": { recommended: "default" },
      },
      variants: [{ id: "default", backend: ["rkllama"] }],
    };
    const { compatible, incompatible } = filterCatalog([npuFramework], [macDevice], []);
    expect(compatible.find((a) => a.id === "npu-agent-fw")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "npu-agent-fw")).toBeDefined();
  });

  it("mcp server with hardware_tiers is filtered when tier doesn't match", () => {
    const armMcp: CatalogApp = {
      id: "arm-mcp-server",
      name: "ARM MCP Server",
      type: "mcp",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
      hardware_tiers: { "arm-npu-16gb": { recommended: "default" } },
    };
    const { compatible, incompatible } = filterCatalog([armMcp], [x86VulkanDevice], []);
    expect(compatible.find((a) => a.id === "arm-mcp-server")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "arm-mcp-server")).toBeDefined();
  });

  it("service without hardware_tiers is universally compatible (passes any device filter)", () => {
    const universalService: CatalogApp = {
      id: "universal-svc",
      name: "Universal Service",
      type: "service",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
      // no hardware_tiers → runs anywhere
    };
    const { compatible } = filterCatalog([universalService], [piDevice], []);
    expect(compatible.map((a) => a.id)).toEqual(["universal-svc"]);
  });

  it("agent-framework without hardware_tiers passes any device filter", () => {
    const universalFw: CatalogApp = {
      id: "universal-fw",
      name: "Universal Framework",
      type: "agent-framework",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
    };
    const { compatible } = filterCatalog([universalFw], [x86VulkanDevice], []);
    expect(compatible.map((a) => a.id)).toEqual(["universal-fw"]);
  });

  it("mcp server without hardware_tiers passes any device filter", () => {
    const universalMcp: CatalogApp = {
      id: "universal-mcp",
      name: "Universal MCP",
      type: "mcp",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
    };
    const { compatible } = filterCatalog([universalMcp], [controllerDevice], []);
    expect(compatible.map((a) => a.id)).toEqual(["universal-mcp"]);
  });

  // --- LLM Runtime specific test (johny / N100 case from #312) ---

  it("LLM runtime restricted to arm-npu tiers is incompatible on x86-vulkan", () => {
    // This is the exact case from #312: johny on an N100 (x86-vulkan-*) was
    // able to install rk-llama-cpp because the runtime tab had no filter.
    const rkLlamaCpp: CatalogApp = {
      id: "rk-llama-cpp",
      name: "rk-llama-cpp Runtime",
      type: "llm-runtime",
      version: "1",
      description: "",
      installed: false,
      compat: "green",
      hardware_tiers: {
        "arm-npu-16gb": { recommended: "default" },
        "arm-npu-8gb": { recommended: "default" },
        "cpu-only": { recommended: "default" },
      },
      variants: [{ id: "default", backend: ["rk-llama-cpp"] }],
    };
    const { compatible, incompatible } = filterCatalog([rkLlamaCpp], [x86VulkanDevice], []);
    expect(compatible.find((a) => a.id === "rk-llama-cpp")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "rk-llama-cpp")).toBeDefined();
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
