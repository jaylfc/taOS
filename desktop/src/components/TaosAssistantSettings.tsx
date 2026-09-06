import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { ModelPickerFlow, type AgentModel } from "./ModelPickerFlow";
import { loadAgentModels } from "@/lib/models";
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
        // Same unified loader the agent deploy picker uses, so the taOS
        // agent chooser lists exactly the same models. hostKind is
        // preserved per model, so ModelPickerFlow's source-select screen
        // still separates local / worker / cloud clearly.
        const aggregated = await loadAgentModels();
        if (cancelled) return;
        setModels(aggregated.map((m) => ({
          id: m.id, name: m.name, host: m.host, hostKind: m.hostKind,
        })));
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
            taOS agent — Settings
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
            Pick the model the taOS agent will use. You can change this at any time.
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
