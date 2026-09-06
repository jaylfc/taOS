import { X } from "lucide-react";
import { Button } from "@/components/ui";
import { ModelPickerFlow, type AgentModel } from "./ModelPickerFlow";

interface Props {
  open: boolean;
  onClose: () => void;
  models: AgentModel[];
  modelsLoaded: boolean;
  onSelect: (modelId: string, model: AgentModel) => void;
  title?: string;
}

export function ModelPickerModal({
  open,
  onClose,
  models,
  modelsLoaded,
  onSelect,
  title = "Select Model",
}: Props) {
  if (!open) return null;

  return (
    <div
      className="absolute inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      style={{
        paddingTop: "calc(1rem + env(safe-area-inset-top, 0px))",
        paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))",
      }}
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-picker-modal-title"
    >
      <div
        className="w-full max-w-md max-h-full min-h-0 bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <h2 id="model-picker-modal-title" className="text-sm font-semibold">{title}</h2>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </Button>
        </div>
        <div className="px-5 py-5 flex-1 min-h-0 overflow-y-auto">
          <ModelPickerFlow
            models={models}
            modelsLoaded={modelsLoaded}
            onSelect={(id, m) => {
              onSelect(id, m);
              onClose();
            }}
            onCancel={onClose}
          />
        </div>
      </div>
    </div>
  );
}
