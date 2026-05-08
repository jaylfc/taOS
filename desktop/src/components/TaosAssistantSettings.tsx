import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { ModelPickerFlow, type AgentModel } from "./ModelPickerFlow";
import {
  fetchClusterWorkers,
  fetchCloudProviders,
  workersToAggregated,
  cloudProvidersToAggregated,
  localProvidersToAggregated,
} from "@/lib/models";
import { useTaosAgentStore } from "@/stores/taos-agent-store";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function TaosAssistantSettings({ open, onClose }: Props) {
  const { model, setModel } = useTaosAgentStore();
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    async function load() {
      try {
        const [localRes, workers, providers] = await Promise.all([
          fetch("/api/models").then((r) => r.ok ? r.json() : { models: [] }),
          fetchClusterWorkers(),
          fetchCloudProviders(),
        ]);
        if (cancelled) return;

        // Use models[].id (manifest id, e.g. "gemma-4-e2b-gguf") rather
        // than downloaded_files[].filename ("gemma-4-e2b-gguf.gguf").
        // The chat path passes whatever id we set here straight to the
        // LiteLLM proxy as the model_name; #433 registered aliases by
        // manifest id, so sending the filename 400s. AgentsApp uses
        // the same models[] source and works correctly.
        type ApiModel = { id: string; name?: string; has_downloaded_variant?: boolean };
        const apiModels: ApiModel[] = Array.isArray(localRes?.models) ? localRes.models : [];
        const local = apiModels
          .filter((m) => m.has_downloaded_variant === true)
          .map((m) => ({
            id: m.id,
            name: m.name ?? m.id,
            host: "controller" as const,
            hostKind: "controller" as const,
          }));
        const worker = workersToAggregated(workers);
        const cloud = cloudProvidersToAggregated(providers);
        // Providers configured directly on the controller (a local ollama,
        // a manually-added llama-cpp, etc.) are neither cloud nor a remote
        // worker — they were silently dropped from the picker before #356
        // surfaced the gap.
        const localProviders = localProvidersToAggregated(providers);

        const all: AgentModel[] = [
          ...local,
          ...localProviders.map((m: { id: string; name: string; host: string; hostKind: "controller" | "worker" | "cloud" }) => ({ id: m.id, name: m.name, host: m.host, hostKind: m.hostKind })),
          ...worker.map((m: { id: string; name: string; host: string; hostKind: "controller" | "worker" | "cloud" }) => ({ id: m.id, name: m.name, host: m.host, hostKind: m.hostKind })),
          ...cloud.map((m: { id: string; name: string; host: string; hostKind: "controller" | "worker" | "cloud" }) => ({ id: m.id, name: m.name, host: m.host, hostKind: m.hostKind })),
        ];

        setModels(all as AgentModel[]);
      } catch {
        // non-fatal — models just won't show
      } finally {
        if (!cancelled) setModelsLoaded(true);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [open]);

  const handleSelect = async (modelId: string) => {
    try {
      await fetch("/api/taos-agent/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelId }),
      });
      setModel(modelId);
      onClose();
    } catch {
      // ignore
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="taos-assistant-settings-title"
    >
      <div
        className="w-full max-w-md bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col"
        style={{ maxHeight: "80vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <h2 id="taos-assistant-settings-title" className="text-sm font-semibold">
            taOS Assistant — Settings
          </h2>
          {model && (
            <span className="text-xs text-shell-text-tertiary mr-auto ml-3 truncate max-w-[120px]">
              {model}
            </span>
          )}
          <button
            className="p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
            onClick={onClose}
            aria-label="Close settings"
          >
            <X size={16} />
          </button>
        </div>
        <div className="px-5 py-5 flex-1 min-h-0 overflow-y-auto">
          <p className="text-xs text-shell-text-secondary mb-4">
            Pick the model the taOS Assistant will use. You can change this at any time.
          </p>
          <ModelPickerFlow
            models={models}
            modelsLoaded={modelsLoaded}
            onSelect={(id) => handleSelect(id)}
            onCancel={onClose}
          />
        </div>
      </div>
    </div>
  );
}
