import { useEffect, useState } from "react";
import { PersonaPicker } from "../persona-picker/PersonaPicker";
import type { PersonaSelection } from "../persona-picker/types";

interface PersonaTabProps {
  agent: {
    name: string;
    soul_md?: string;
    agent_md?: string;
    source_persona_id?: string | null;
  };
  onUpdated: () => void;
}

export function PersonaTab({ agent, onUpdated }: PersonaTabProps) {
  const [soul, setSoul] = useState(agent.soul_md ?? "");
  const [agentMd, setAgentMd] = useState(agent.agent_md ?? "");
  const [saving, setSaving] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [pendingSelection, setPendingSelection] = useState<PersonaSelection | null>(null);

  useEffect(() => {
    setSoul(agent.soul_md ?? "");
    setAgentMd(agent.agent_md ?? "");
  }, [agent.name]);

  const save = async () => {
    setSaving(true);
    await fetch(`/api/agents/${agent.name}/persona`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ soul_md: soul, agent_md: agentMd }),
    });
    setSaving(false);
    onUpdated();
  };

  const confirmSwap = async () => {
    if (!pendingSelection) return;
    const patch =
      pendingSelection.kind === "blank"
        ? { soul_md: "", source_persona_id: null }
        : {
            soul_md: pendingSelection.soul_md,
            source_persona_id: pendingSelection.source_persona_id ?? null,
          };
    await fetch(`/api/agents/${agent.name}/persona`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    setSoul(patch.soul_md);
    setPendingSelection(null);
    setShowPicker(false);
    onUpdated();
  };

  const pendingName =
    pendingSelection?.kind === "blank"
      ? "Blank"
      : pendingSelection?.kind === "custom"
        ? "Custom"
        : (pendingSelection?.save_to_library?.name ?? pendingSelection?.source_persona_id ?? "this persona");

  const sourceLabel = agent.source_persona_id
    ? `From: ${agent.source_persona_id}`
    : soul || agentMd
      ? "Custom"
      : "Blank";

  return (
    <div className="h-full overflow-auto p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase opacity-60">{sourceLabel}</span>
        <button
          onClick={() => setShowPicker(true)}
          className="text-xs px-2 py-1 rounded border border-white/10 hover:bg-white/5 transition-colors"
        >
          Swap persona…
        </button>
      </div>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase opacity-60">Soul</span>
        <textarea
          value={soul}
          onChange={(e) => setSoul(e.target.value)}
          rows={10}
          className="border border-white/10 rounded bg-shell-bg px-2 py-1 font-mono text-xs text-shell-text-secondary resize-none focus:outline-none focus:ring-1 focus:ring-white/20"
          aria-label="Agent soul (identity)"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase opacity-60">Agent.md — operational rules</span>
        <textarea
          value={agentMd}
          onChange={(e) => setAgentMd(e.target.value)}
          rows={8}
          className="border border-white/10 rounded bg-shell-bg px-2 py-1 font-mono text-xs text-shell-text-secondary resize-none focus:outline-none focus:ring-1 focus:ring-white/20"
          aria-label="Agent operational rules"
        />
      </label>
      <button
        disabled={saving}
        onClick={save}
        className="self-end bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1.5 rounded text-sm text-white transition-colors"
      >
        {saving ? "Saving…" : "Save"}
      </button>

      {/* Swap persona modal */}
      {showPicker && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
          onClick={() => { setPendingSelection(null); setShowPicker(false); }}
        >
          <div
            className="bg-shell-bg border border-white/10 rounded-lg p-4 w-[480px] max-h-[80vh] overflow-auto flex flex-col gap-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Swap persona</span>
              <button
                onClick={() => { setPendingSelection(null); setShowPicker(false); }}
                className="text-xs opacity-60 hover:opacity-100"
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <PersonaPicker onSelect={setPendingSelection} />
          </div>
        </div>
      )}

      {/* Confirmation dialog */}
      {pendingSelection && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-60"
          onClick={() => setPendingSelection(null)}
        >
          <div
            className="bg-shell-bg border border-white/10 rounded-lg p-5 w-[360px] flex flex-col gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm">
              Replace Soul with <strong>{pendingName}</strong>? Agent.md stays as-is.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setPendingSelection(null)}
                className="text-xs px-3 py-1.5 rounded border border-white/10 hover:bg-white/5 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmSwap}
                className="text-xs px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white transition-colors"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
