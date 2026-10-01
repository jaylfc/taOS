import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CapabilityMapDetail, PlacementOverview } from "./ClusterApp.capabilityMap";
import type { ClusterMap, ClusterMapNode } from "@/lib/cluster";

// Lead-review nit on #3223: no em dashes in user-facing copy (the tooltip and
// the read-only caption). House style (docs/agent-coordination.md, "Identity
// rules") is a comma, colon or full stop instead.

const EM_DASH = "\u2014";

function makeNode(overrides: Partial<ClusterMapNode> = {}): ClusterMapNode {
  return {
    name: "gpu-box",
    url: "http://gpu-box:8080",
    health: "online",
    last_heartbeat: 1_700_000_000,
    heartbeat_age_s: 4,
    tier_id: "gpu-cuda",
    hardware: { ram_mb: 32768 },
    vram: { free_mb: 8192, used_mb: 4096, total_mb: 12288 },
    capabilities: ["chat"],
    potential_capabilities: ["image-gen"],
    backends: [],
    placement: [],
    leases: [],
    ...overrides,
  };
}

function makeMap(...nodes: ClusterMapNode[]): ClusterMap {
  return {
    generated_at: 1_700_000_000,
    nodes,
    capabilities: [
      {
        capability: "chat",
        active_nodes: ["gpu-box"],
        installed_nodes: [],
        potential_nodes: [],
      },
    ],
  };
}

/** Every user-facing string the fragment renders: text plus title tooltips. */
function userFacingCopy(container: HTMLElement): string {
  const titles = Array.from(container.querySelectorAll("[title]")).map(
    (el) => el.getAttribute("title") ?? "",
  );
  return [container.textContent ?? "", ...titles].join("\n");
}

describe("capability map copy", () => {
  it("renders the read-only caption without an em dash", () => {
    const { container } = render(<PlacementOverview map={makeMap(makeNode())} />);

    // The caption is present, so the em dash check below is not vacuous.
    expect(container.textContent).toContain("Read-only view.");
    expect(userFacingCopy(container)).not.toContain(EM_DASH);
  });

  it("renders the potential-capability tooltip without an em dash", () => {
    const { container } = render(<PlacementOverview map={makeMap(makeNode())} />);

    const tooltips = Array.from(container.querySelectorAll("[title]")).map(
      (el) => el.getAttribute("title") ?? "",
    );
    const tooltip = tooltips.find((t) => t.startsWith("Hardware can support this"));
    // The potential pill carrying the tooltip is genuinely on screen.
    expect(tooltip).toBeDefined();
    expect(container.textContent).toContain("image-gen");
    expect(userFacingCopy(container)).not.toContain(EM_DASH);
  });

  it("renders the capability detail pane without an em dash", () => {
    const { container } = render(
      <CapabilityMapDetail map={makeMap(makeNode())} capability="chat" />,
    );

    expect(container.textContent).toContain("chat");
    expect(userFacingCopy(container)).not.toContain(EM_DASH);
  });
});
