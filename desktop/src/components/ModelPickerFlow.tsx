import { useState, useEffect, useMemo } from "react";
import { ChevronLeft, Monitor, Server, Cloud, Search, X, RefreshCw } from "lucide-react";
import { HOST_BADGE_CLASS } from "@/lib/models";

export interface AgentModel {
  id: string;
  name: string;
  host?: string;
  hostKind?: "controller" | "worker" | "cloud";
}

interface Props {
  models: AgentModel[];
  modelsLoaded: boolean;
  onSelect: (modelId: string, model: AgentModel) => void;
  onBack?: () => void;    // inline mode: shown on source screen as "Back"
  onCancel?: () => void;  // modal mode: shown on source screen as "Cancel"
  onRefresh?: () => void; // optional: called when the user clicks the refresh button
  refreshing?: boolean;   // true while a refresh is in-flight
  cachedAt?: number;      // wall-clock seconds of last successful catalog fetch
}

type Screen = "source" | "provider" | "list";
type Source = "local" | "worker" | "cloud";

const SOURCE_META: Record<Source, { label: string; icon: React.ReactNode; desc: string }> = {
  local:  { label: "Local",  icon: <Monitor size={18} />, desc: "Downloaded on this device" },
  worker: { label: "Worker", icon: <Server  size={18} />, desc: "Hosted on cluster workers"  },
  cloud:  { label: "Cloud",  icon: <Cloud   size={18} />, desc: "Cloud provider API"          },
};

function _formatCachedAt(ts: number | undefined): string | null {
  if (!ts || ts <= 0) return null;
  const diffSec = Math.floor(Date.now() / 1000 - ts);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

export function ModelPickerFlow({ models, modelsLoaded, onSelect, onBack, onCancel, onRefresh, refreshing = false, cachedAt }: Props) {
  const [screen, setScreen]                   = useState<Screen>("source");
  const [selectedSource, setSelectedSource]   = useState<Source | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [search, setSearch]                   = useState("");

  // Partition by source
  const localModels  = useMemo(() => models.filter(m => m.hostKind === "controller" || !m.hostKind), [models]);
  const workerModels = useMemo(() => models.filter(m => m.hostKind === "worker"), [models]);
  const cloudModels  = useMemo(() => models.filter(m => m.hostKind === "cloud"), [models]);

  const availableSources: Source[] = [
    ...(localModels.length  > 0 ? ["local"  as Source] : []),
    ...(workerModels.length > 0 ? ["worker" as Source] : []),
    ...(cloudModels.length  > 0 ? ["cloud"  as Source] : []),
  ];

  const workerProviders = useMemo(
    () => [...new Set(workerModels.map(m => m.host ?? "unknown"))],
    [workerModels]
  );
  const cloudProviders = useMemo(
    () => [...new Set(cloudModels.map(m => m.host ?? "unknown"))],
    [cloudModels]
  );

  const goToProvider = (source: Source) => {
    const providers = source === "worker" ? workerProviders : cloudProviders;
    if (providers.length <= 1) {
      setSelectedProvider(providers[0] ?? null);
      setScreen("list");
    } else {
      setScreen("provider");
    }
  };

  const handleSourceSelect = (source: Source) => {
    setSelectedSource(source);
    setSearch("");
    if (source === "local") {
      setSelectedProvider(null);
      setScreen("list");
    } else {
      goToProvider(source);
    }
  };

  // Auto-select if only one source has models. handleSourceSelect is intentionally
  // excluded — this should only auto-advance when models first become available.
  useEffect(() => {
    if (modelsLoaded && availableSources.length === 1 && availableSources[0]) {
      handleSourceSelect(availableSources[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelsLoaded]);

  const handleProviderSelect = (provider: string) => {
    setSelectedProvider(provider);
    setSearch("");
    setScreen("list");
  };

  const handleBack = () => {
    if (screen === "source") {
      onCancel ? onCancel() : onBack?.();
    } else if (screen === "provider") {
      setSelectedSource(null);
      setScreen("source");
    } else {
      // list → provider (if there were multiple providers) or source
      const providers = selectedSource === "worker" ? workerProviders : cloudProviders;
      if (selectedSource !== "local" && providers.length > 1) {
        setSelectedProvider(null);
        setScreen("provider");
      } else {
        setSelectedSource(null);
        setScreen("source");
      }
    }
  };

  // Models visible in the list screen, filtered by source/provider then search
  const listModels = models
    .filter(m => {
      if (selectedSource === "local")  return m.hostKind === "controller" || !m.hostKind;
      if (selectedSource === "worker") return m.hostKind === "worker" && m.host === selectedProvider;
      if (selectedSource === "cloud")  return m.hostKind === "cloud"  && m.host === selectedProvider;
      return false;
    })
    .filter(m => {
      if (!search) return true;
      const q = search.toLowerCase();
      return m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q);
    });

  /* ── Source screen ─────────────────────────────── */
  if (screen === "source") {
    const exitLabel = onCancel ? "Cancel" : "Back";
    const cachedLabel = _formatCachedAt(cachedAt);
    return (
      <div className="space-y-2">
        {(onBack || onCancel) && (
          <button
            onClick={handleBack}
            className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text mb-3 transition-colors"
          >
            <ChevronLeft size={14} />
            {exitLabel}
          </button>
        )}
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-shell-text-secondary">Where is the model?</span>
          {onRefresh && (
            <div className="flex items-center gap-1.5">
              {cachedLabel && !refreshing && (
                <span className="text-[10px] text-shell-text-tertiary" title="Cloud catalog last updated">
                  {cachedLabel}
                </span>
              )}
              <button
                onClick={onRefresh}
                disabled={refreshing || !modelsLoaded}
                aria-label="Refresh model catalog"
                className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text disabled:opacity-40 transition-colors"
              >
                <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
                {refreshing ? "Refreshing…" : "Refresh"}
              </button>
            </div>
          )}
        </div>
        {!modelsLoaded && (
          <p className="text-xs text-shell-text-tertiary py-2">Loading models…</p>
        )}
        {modelsLoaded && availableSources.length === 0 && (
          <p className="text-xs text-shell-text-tertiary py-2">No models available.</p>
        )}
        <div className="grid grid-cols-1 gap-2">
          {availableSources.map(source => {
            const { label, icon, desc } = SOURCE_META[source];
            return (
              <button
                key={source}
                onClick={() => handleSourceSelect(source)}
                aria-label={`Select ${label} models`}
                className="w-full text-left px-4 py-3 rounded-lg border border-white/5 bg-shell-bg-deep hover:bg-white/5 transition-colors flex items-center gap-3"
              >
                <span className="text-accent shrink-0">{icon}</span>
                <div>
                  <div className="text-sm font-medium">{label}</div>
                  <div className="text-xs text-shell-text-tertiary">{desc}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  /* ── Provider screen ───────────────────────────── */
  if (screen === "provider") {
    const providers = selectedSource === "worker" ? workerProviders : cloudProviders;
    const heading   = selectedSource === "worker" ? "Select worker" : "Select provider";
    return (
      <div className="space-y-2">
        <button
          onClick={handleBack}
          className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text mb-3 transition-colors"
        >
          <ChevronLeft size={14} />
          Back
        </button>
        <span className="block text-xs text-shell-text-secondary mb-2">{heading}</span>
        <div className="grid grid-cols-1 gap-2">
          {providers.map(provider => (
            <button
              key={provider}
              onClick={() => handleProviderSelect(provider)}
              aria-label={`Select ${provider}`}
              className="w-full text-left px-4 py-3 rounded-lg border border-white/5 bg-shell-bg-deep hover:bg-white/5 transition-colors"
            >
              <div className="text-sm font-medium">{provider}</div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  /* ── Model list screen ─────────────────────────── */
  return (
    <div className="space-y-2">
      <button
        onClick={handleBack}
        className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text mb-1 transition-colors"
      >
        <ChevronLeft size={14} />
        Back
      </button>
      <div className="relative mb-2">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none" />
        <input
          type="text"
          placeholder="Search models…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full pl-8 pr-8 h-8 rounded-lg border border-white/10 bg-shell-bg-deep text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/40"
          autoFocus
          aria-label="Search models"
        />
        {search && (
          <button
            onClick={() => setSearch("")}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary hover:text-shell-text"
            aria-label="Clear search"
          >
            <X size={12} />
          </button>
        )}
      </div>
      {listModels.length === 0 && (
        <p className="text-xs text-shell-text-tertiary py-4 text-center">No models match your search.</p>
      )}
      {listModels.map(m => (
        <button
          key={`${m.hostKind ?? "?"}:${m.host ?? "?"}:${m.id}`}
          onClick={() => onSelect(m.id, m)}
          className="w-full text-left px-4 py-3 rounded-lg border border-white/5 bg-shell-bg-deep hover:bg-white/5 transition-colors"
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <div className="text-sm font-medium truncate">{m.name}</div>
            {m.host && m.hostKind !== "controller" && (
              <span className={HOST_BADGE_CLASS} title={`Hosted on ${m.host}`}>{m.host}</span>
            )}
          </div>
          <div className="text-xs text-shell-text-tertiary">{m.id}</div>
        </button>
      ))}
    </div>
  );
}
