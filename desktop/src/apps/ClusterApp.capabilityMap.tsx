import { AlertTriangle, Boxes, Circle, Layers, Loader2, Zap } from "lucide-react";
import { Card, CardContent } from "@/components/ui";
import {
  capabilityNodeNames,
  capabilityNodeState,
  capabilitySummary,
  formatVram,
  placementForCapability,
  STATUS_LABEL,
  STATUS_PILL_CLASS,
  type CapabilityNodeState,
  type ClusterMap,
  type ClusterMapCapability,
  type ClusterMapNode,
} from "@/lib/cluster";

// Read-only half of taOS #897: render the aggregated capability map and the
// live placement view (what runs where, loaded vs installed, VRAM, health).
// Deliberately no move/drag affordances here — relocation is a later slice.

const STATE_LABEL: Record<CapabilityNodeState, string> = {
  active: "serving",
  installed: "installed",
  potential: "capable",
};

const STATE_BADGE_CLASS: Record<CapabilityNodeState, string> = {
  active: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  installed: "bg-sky-500/15 text-sky-200 border-sky-500/25",
  potential: "bg-white/[0.03] border-white/10 text-shell-text-tertiary",
};

function HealthPill({ node }: { node: ClusterMapNode }) {
  const health = node.health ?? "unknown";
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold border ${STATUS_PILL_CLASS[health]}`}
      aria-label={`Node health: ${STATUS_LABEL[health]}`}
    >
      {STATUS_LABEL[health]}
    </span>
  );
}

function CapabilityRowCard({
  cap,
  selected,
  onSelect,
}: {
  cap: ClusterMapCapability;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Show where ${cap.capability} runs`}
      className={`w-full text-left p-2.5 rounded-lg border transition-colors ${
        selected
          ? "border-accent/50 bg-accent/10"
          : "border-white/5 bg-white/[0.02] hover:bg-white/[0.04]"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-shell-text truncate">{cap.capability}</span>
        {(cap.active_nodes ?? []).length > 0 ? (
          <Zap size={12} className="text-emerald-300 shrink-0" aria-hidden="true" />
        ) : (
          <Circle size={10} className="text-shell-text-tertiary shrink-0" aria-hidden="true" />
        )}
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.04] border border-white/10 text-shell-text-tertiary">
          {capabilitySummary(cap)}
        </span>
      </div>
    </button>
  );
}

/** List pane for the Map tab: every capability known anywhere in the mesh. */
export function CapabilityMapList({
  map,
  selected,
  onSelect,
  loading,
  error,
}: {
  map: ClusterMap | null;
  selected: string | null;
  onSelect: (capability: string) => void;
  loading: boolean;
  error?: string | null;
}) {
  if (error) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-200">
        <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
        <span>{error}</span>
      </div>
    );
  }
  if (loading && !map) {
    return (
      <div className="flex items-center justify-center gap-2 py-6 text-[11px] text-shell-text-tertiary">
        <Loader2 size={12} className="animate-spin" aria-hidden="true" />
        Loading capability map...
      </div>
    );
  }
  const capabilities = map?.capabilities ?? [];
  if (capabilities.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-6 text-center">
        <p className="text-[11px] text-shell-text-tertiary">
          No capabilities reported yet.
        </p>
        <p className="text-[10px] text-shell-text-tertiary leading-relaxed px-2">
          Once a node heartbeats with its backends and models, what it can serve shows up here.
        </p>
      </div>
    );
  }
  return (
    <>
      {capabilities.map((cap) => (
        <CapabilityRowCard
          key={cap.capability}
          cap={cap}
          selected={selected === cap.capability}
          onSelect={() => onSelect(cap.capability)}
        />
      ))}
    </>
  );
}

function PlacementTable({ rows, onEmpty }: { rows: ReturnType<typeof placementForCapability>; onEmpty: string }) {
  if (rows.length === 0) {
    return <p className="text-[10px] text-shell-text-tertiary italic">{onEmpty}</p>;
  }
  return (
    <div className="space-y-1">
      {rows.map((row) => (
        <div
          key={`${row.backend}-${row.model_id}`}
          className="flex items-center justify-between gap-2 rounded border border-white/5 bg-white/[0.02] px-2 py-1"
        >
          <div className="min-w-0">
            <div className="text-[11px] text-shell-text truncate">{row.model_id}</div>
            <div className="text-[9px] text-shell-text-tertiary truncate">
              {row.backend || "no backend"}
              {row.backend_status ? ` \u00b7 ${row.backend_status}` : ""}
              {row.vram_required_gb ? ` \u00b7 ${row.vram_required_gb} GB VRAM` : ""}
            </div>
          </div>
          <span
            className={`text-[9px] px-1.5 py-0.5 rounded-full border shrink-0 ${
              row.state === "loaded"
                ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/25"
                : "bg-sky-500/15 text-sky-200 border-sky-500/25"
            }`}
          >
            {row.state === "loaded" ? "loaded" : "installed"}
          </span>
        </div>
      ))}
    </div>
  );
}

function NodeCard({ node, cap }: { node: ClusterMapNode; cap?: ClusterMapCapability }) {
  const state = cap ? capabilityNodeState(cap, node.name) : null;
  const rows = cap ? placementForCapability(node, cap.capability) : node.placement ?? [];
  const ramGb = node.hardware?.ram_mb ? `${Math.round(node.hardware.ram_mb / 1024)} GB RAM` : "";

  return (
    <Card className="p-3">
      <CardContent className="p-0 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-[12px] font-semibold text-shell-text truncate">{node.name}</span>
            {cap && state && (
              <span
                className={`text-[9px] px-1.5 py-0.5 rounded-full border font-medium ${STATE_BADGE_CLASS[state]}`}
                aria-label={`${cap.capability} is ${STATE_LABEL[state]} on this node`}
              >
                {STATE_LABEL[state]}
              </span>
            )}
          </div>
          <HealthPill node={node} />
        </div>
        <div className="text-[10px] text-shell-text-tertiary">
          {[node.tier_id, ramGb, formatVram(node.vram)].filter(Boolean).join("  \u00b7  ")}
        </div>
        {rows.length > 0 && (
          <PlacementTable
            rows={rows}
            onEmpty="Nothing for this capability on this node."
          />
        )}
        {!cap && (node.potential_capabilities ?? []).length > 0 && (
          <div className="flex flex-wrap gap-1">
            {node.potential_capabilities.slice(0, 6).map((c) => (
              <span
                key={`${node.name}-pot-${c}`}
                className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.03] border border-white/10 text-shell-text-tertiary"
                title="Hardware can support this. Install a model with this capability to enable it."
              >
                {c}
              </span>
            ))}
          </div>
        )}
        {(node.leases ?? []).length > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-amber-300">
            <AlertTriangle size={11} aria-hidden="true" />
            {node.leases.map((l) => l.caller || l.lease_id).join(", ")} holding a GPU lease
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Detail pane when a capability is selected: who has it and what serves it. */
export function CapabilityMapDetail({
  map,
  capability,
}: {
  map: ClusterMap | null;
  capability: string;
}) {
  const cap = (map?.capabilities ?? []).find((c) => c.capability === capability);
  const nodeNames = cap ? capabilityNodeNames(cap) : [];
  const byName = new Map((map?.nodes ?? []).map((n) => [n.name, n]));
  const nodes = nodeNames
    .map((name) => byName.get(name))
    .filter((n): n is ClusterMapNode => Boolean(n));

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Layers size={16} className="text-accent" aria-hidden="true" />
        <h2 className="text-sm font-semibold text-shell-text">{capability}</h2>
      </div>
      <p className="text-[11px] text-shell-text-tertiary">
        {cap ? capabilitySummary(cap) : "not reported by any node"}
      </p>
      {nodes.length === 0 ? (
        <p className="text-[11px] text-shell-text-tertiary italic">
          No node is related to this capability right now.
        </p>
      ) : (
        <div className="space-y-2">
          {nodes.map((node) => (
            <NodeCard key={node.name} node={node} cap={cap} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Detail pane with nothing selected: placement across every node. */
export function PlacementOverview({ map }: { map: ClusterMap | null }) {
  const nodes = map?.nodes ?? [];
  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
        No nodes in the cluster map yet.
      </div>
    );
  }
  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Boxes size={16} className="text-accent" aria-hidden="true" />
        <h2 className="text-sm font-semibold text-shell-text">Placement</h2>
      </div>
      <p className="text-[11px] text-shell-text-tertiary">
        Everything installed or loaded across {nodes.length} node{nodes.length === 1 ? "" : "s"}.
        Read-only view. Nothing here moves anything.
      </p>
      <div className="space-y-2">
        {nodes.map((node) => (
          <NodeCard key={node.name} node={node} />
        ))}
      </div>
    </div>
  );
}
