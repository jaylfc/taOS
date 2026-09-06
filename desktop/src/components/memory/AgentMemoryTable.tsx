import { useState, useEffect } from "react";
import { ChevronDown, ChevronRight, Save, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { fetchAgentMemoryConfig, updateAgentMemoryConfig } from "@/lib/memory";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AgentRow {
  name: string;
  color: string;
  strategy?: string;
  layers?: string[];
  backend?: string;
}

interface AgentMemoryTableProps {
  agents: AgentRow[];
}

const STRATEGIES = ['contextual', 'episodic', 'semantic', 'hybrid', 'none'] as const;
const ALL_LAYERS = ['archive', 'catalog', 'vector', 'kg', 'crystal'] as const;

/* ------------------------------------------------------------------ */
/*  ExpandedRow                                                        */
/* ------------------------------------------------------------------ */

interface ExpandedRowProps {
  agent: AgentRow;
  onClose: () => void;
}

function ExpandedRow({ agent, onClose }: ExpandedRowProps) {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [strategy, setStrategy] = useState(agent.strategy ?? 'contextual');
  const [layers, setLayers] = useState<string[]>(agent.layers ?? [...ALL_LAYERS]);

  useEffect(() => {
    (async () => {
      setLoading(true);
      const cfg = await fetchAgentMemoryConfig(agent.name);
      setConfig(cfg);
      if (cfg.strategy) setStrategy(cfg.strategy);
      if (Array.isArray(cfg.layers)) setLayers(cfg.layers);
      setLoading(false);
    })();
  }, [agent.name]);

  const toggleLayer = (layer: string) => {
    setLayers((prev) =>
      prev.includes(layer) ? prev.filter((l) => l !== layer) : [...prev, layer],
    );
  };

  const handleSave = async () => {
    setSaving(true);
    await updateAgentMemoryConfig(agent.name, { ...config, strategy, layers });
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-shell-text-tertiary text-xs">
        Loading config…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4 border-t border-white/8" role="region" aria-label={`${agent.name} memory configuration`}>
      {/* Strategy */}
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
          Strategy
        </Label>
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Memory strategy">
          {STRATEGIES.map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={strategy === s}
              onClick={() => setStrategy(s)}
              className={`
                px-2.5 py-1 rounded-md border text-xs font-medium transition-colors
                focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent
                ${strategy === s
                  ? 'border-accent bg-accent/15 text-shell-text'
                  : 'border-white/10 text-shell-text-secondary hover:bg-white/[0.04]'}
              `}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Layers */}
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
          Active Layers
        </Label>
        <div className="flex flex-wrap gap-3" role="group" aria-label="Memory layers">
          {ALL_LAYERS.map((layer) => {
            const checked = layers.includes(layer);
            const switchId = `layer-${agent.name}-${layer}`;
            return (
              <div key={layer} className="flex items-center gap-1.5">
                <Switch
                  id={switchId}
                  checked={checked}
                  onCheckedChange={() => toggleLayer(layer)}
                  aria-label={`Enable ${layer} layer`}
                />
                <Label htmlFor={switchId} className="text-xs text-shell-text cursor-pointer capitalize">
                  {layer}
                </Label>
              </div>
            );
          })}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving}
          aria-label={`Save memory config for ${agent.name}`}
          className="h-7 px-3 gap-1.5 text-xs"
        >
          {saving ? (
            <RefreshCw size={12} className="animate-spin" aria-hidden="true" />
          ) : (
            <Save size={12} aria-hidden="true" />
          )}
          {saved ? 'Saved!' : saving ? 'Saving…' : 'Save'}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClose}
          className="h-7 px-2.5 text-xs"
          aria-label="Close configuration"
        >
          Close
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  AgentMemoryTable                                                   */
/* ------------------------------------------------------------------ */

export function AgentMemoryTable({ agents }: AgentMemoryTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (agents.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-shell-text-tertiary text-sm">
        No agents found
      </div>
    );
  }

  return (
    <div className="overflow-auto h-full p-4" aria-label="Agent memory configuration table">
      <Card className="overflow-hidden border-white/8">
        {/* Table header */}
        <div
          className="grid grid-cols-[auto_1fr_140px_160px_120px] gap-0 border-b border-white/8 bg-white/[0.02]"
          role="row"
          aria-label="Column headers"
        >
          <div className="w-9" aria-hidden="true" />
          <div className="px-4 py-2.5 text-[10px] font-medium text-shell-text-tertiary uppercase tracking-wider" role="columnheader">
            Agent
          </div>
          <div className="px-4 py-2.5 text-[10px] font-medium text-shell-text-tertiary uppercase tracking-wider" role="columnheader">
            Strategy
          </div>
          <div className="px-4 py-2.5 text-[10px] font-medium text-shell-text-tertiary uppercase tracking-wider" role="columnheader">
            Layers
          </div>
          <div className="px-4 py-2.5 text-[10px] font-medium text-shell-text-tertiary uppercase tracking-wider" role="columnheader">
            Backend
          </div>
        </div>

        {/* Rows */}
        <div role="rowgroup" aria-label="Agents">
          {agents.map((agent) => {
            const isExpanded = expanded === agent.name;
            return (
              <div key={agent.name} className="border-b border-white/5 last:border-0" role="row">
                {/* Main row */}
                <button
                  type="button"
                  onClick={() => setExpanded(isExpanded ? null : agent.name)}
                  aria-expanded={isExpanded}
                  aria-controls={`agent-detail-${agent.name}`}
                  className={`
                    w-full grid grid-cols-[auto_1fr_140px_160px_120px] gap-0 items-center
                    hover:bg-white/[0.03] transition-colors text-left
                    focus-visible:outline-none focus-visible:ring-inset focus-visible:ring-1 focus-visible:ring-accent
                    ${isExpanded ? 'bg-white/[0.03]' : ''}
                  `}
                >
                  <div className="flex items-center justify-center w-9 py-3 text-shell-text-tertiary" aria-hidden="true">
                    {isExpanded
                      ? <ChevronDown size={13} />
                      : <ChevronRight size={13} />}
                  </div>
                  <div className="px-4 py-3 flex items-center gap-2.5">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: agent.color }}
                      aria-hidden="true"
                    />
                    <span className="text-sm text-shell-text font-medium truncate">{agent.name}</span>
                  </div>
                  <div className="px-4 py-3">
                    <span className="text-xs text-shell-text-secondary capitalize">
                      {agent.strategy ?? '—'}
                    </span>
                  </div>
                  <div className="px-4 py-3">
                    {Array.isArray(agent.layers) && agent.layers.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {agent.layers.slice(0, 3).map((l) => (
                          <span key={l} className="px-1.5 py-0.5 rounded bg-white/[0.06] text-[10px] text-shell-text-tertiary capitalize">
                            {l}
                          </span>
                        ))}
                        {agent.layers.length > 3 && (
                          <span className="text-[10px] text-shell-text-tertiary">+{agent.layers.length - 3}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-shell-text-tertiary">—</span>
                    )}
                  </div>
                  <div className="px-4 py-3">
                    <span className="text-xs text-shell-text-secondary truncate">
                      {agent.backend ?? '—'}
                    </span>
                  </div>
                </button>

                {/* Expanded detail */}
                {isExpanded && (
                  <div id={`agent-detail-${agent.name}`}>
                    <ExpandedRow
                      agent={agent}
                      onClose={() => setExpanded(null)}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
