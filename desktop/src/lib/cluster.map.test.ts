import { describe, expect, it } from "vitest";
import {
  capabilityNodeNames,
  capabilityNodeState,
  capabilitySummary,
  formatVram,
  placementForCapability,
  type ClusterMapCapability,
  type ClusterMapNode,
} from "./cluster";

const cap: ClusterMapCapability = {
  capability: "chat",
  active_nodes: ["gpu-box"],
  installed_nodes: ["gpu-box", "pi-cpu"],
  potential_nodes: ["stale-box"],
};

describe("capabilityNodeState", () => {
  it("reports active ahead of installed for the same node", () => {
    expect(capabilityNodeState(cap, "gpu-box")).toBe("active");
  });

  it("reports installed when a node has the model but is not serving it", () => {
    expect(capabilityNodeState(cap, "pi-cpu")).toBe("installed");
  });

  it("reports potential when the hardware could run it", () => {
    expect(capabilityNodeState(cap, "stale-box")).toBe("potential");
  });

  it("returns null for an unrelated node", () => {
    expect(capabilityNodeState(cap, "laptop")).toBeNull();
  });

  it("tolerates a response missing a bucket", () => {
    expect(capabilityNodeState({ capability: "chat" } as ClusterMapCapability, "x")).toBeNull();
  });
});

describe("capabilityNodeNames", () => {
  it("dedupes a node that is both active and installed", () => {
    expect(capabilityNodeNames(cap)).toEqual(["gpu-box", "pi-cpu", "stale-box"]);
  });
});

describe("capabilitySummary", () => {
  it("counts each bucket", () => {
    expect(capabilitySummary(cap)).toBe("1 active, 2 installed, 1 capable");
  });

  it("never renders an empty summary", () => {
    expect(capabilitySummary({ capability: "tts" } as ClusterMapCapability)).toBe("no nodes");
  });
});

describe("formatVram", () => {
  it("renders used of total in GB", () => {
    expect(formatVram({ free_mb: 4096, used_mb: 8192, total_mb: 12288 })).toBe("8.0 GB used of 12.0 GB");
  });

  it("stays honest when the node reported no probe", () => {
    expect(formatVram({ free_mb: null, used_mb: null, total_mb: null })).toBe("no VRAM probe");
  });

  it("does not treat a missing vram block as zero usage", () => {
    expect(formatVram(undefined)).toBe("no VRAM probe");
  });
});

describe("placementForCapability", () => {
  const node: ClusterMapNode = {
    name: "gpu-box",
    url: "http://10.0.0.7:9000",
    health: "online",
    heartbeat_age_s: 1,
    vram: { free_mb: 4096, used_mb: 8192, total_mb: 12288 },
    capabilities: ["chat"],
    potential_capabilities: [],
    backends: [],
    leases: [],
    placement: [
      { model_id: "qwen2.5-7b", capability: "chat", backend: "vllm:8000", backend_type: "vllm", backend_status: "ok", state: "loaded", vram_required_gb: 6, health_url: "" },
      { model_id: "llama3-8b", capability: "chat", backend: "vllm:8000", backend_type: "vllm", backend_status: "ok", state: "installed", vram_required_gb: 8, health_url: "" },
      { model_id: "dreamshaper-8-lcm", capability: "image-generation", backend: "sd-cpp", backend_type: "sd-cpp", backend_status: "stopped", state: "installed", vram_required_gb: 0, health_url: "" },
    ],
  };

  it("keeps only the rows serving the requested capability", () => {
    const rows = placementForCapability(node, "chat");
    expect(rows.map((r) => r.model_id)).toEqual(["qwen2.5-7b", "llama3-8b"]);
  });

  it("still finds an installed-only capability on a stopped backend", () => {
    const rows = placementForCapability(node, "image-generation");
    expect(rows).toHaveLength(1);
    expect(rows[0].state).toBe("installed");
    expect(rows[0].backend_status).toBe("stopped");
  });

  it("returns nothing for a capability the node does not have", () => {
    expect(placementForCapability(node, "tts")).toEqual([]);
  });
});
