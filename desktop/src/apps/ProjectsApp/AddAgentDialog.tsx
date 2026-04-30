import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { projectsApi } from "@/lib/projects";

type Mode = "new" | "existing";
type AgentMode = "native" | "clone";

export function AddAgentDialog({
  projectId,
  onClose,
  onAdded,
}: {
  projectId: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [mode, setMode] = useState<Mode>("new");
  const [agentMode, setAgentMode] = useState<AgentMode>("native");
  const [agentId, setAgentId] = useState("");
  const [cloneMemory, setCloneMemory] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When user picks "new agent", agentMode is locked to "native".
  useEffect(() => {
    if (mode === "new") setAgentMode("native");
  }, [mode]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    const normalizedAgentId = agentId.trim();
    if (!normalizedAgentId) {
      setError("Agent ID is required.");
      return;
    }
    // mode === "new" forces native; otherwise the user's agentMode choice wins.
    const useNative = mode === "new" || agentMode === "native";
    setSubmitting(true);
    setError(null);
    try {
      if (useNative) {
        await projectsApi.members.addNative(projectId, normalizedAgentId);
      } else {
        await projectsApi.members.addClone(projectId, normalizedAgentId, cloneMemory);
      }
      onAdded();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = () => {
    if (submitting) return;
    onClose();
  };

  return createPortal(
    <div role="dialog" aria-modal="true" aria-label="Add agent" className="fixed inset-0 bg-black/50 flex items-center justify-center">
      <form onSubmit={onSubmit} className="bg-zinc-900 p-4 rounded shadow w-[26rem] space-y-3">
        <h3 className="text-lg font-semibold">Add agent</h3>

        <fieldset className="border border-zinc-800 p-2 rounded">
          <legend className="text-xs px-1">Source</legend>
          <label className="mr-3 text-sm">
            <input type="radio" checked={mode === "new"} onChange={() => setMode("new")} /> New agent
          </label>
          <label className="text-sm">
            <input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} /> Existing agent
          </label>
        </fieldset>

        <fieldset className="border border-zinc-800 p-2 rounded">
          <legend className="text-xs px-1">Mode</legend>
          <label className="mr-3 text-sm">
            <input
              type="radio"
              checked={agentMode === "native"}
              onChange={() => setAgentMode("native")}
              disabled={mode === "new"}
            />{" "}
            Native
          </label>
          <label
            className="text-sm"
            title={
              mode === "new"
                ? "New agents are always native to this project. Clone exists for adding an existing agent without contaminating its memory."
                : "Cloning gives this project its own copy of the agent. Project memory stays here; the original agent isn't affected."
            }
          >
            <input
              type="radio"
              checked={agentMode === "clone"}
              onChange={() => setAgentMode("clone")}
              disabled={mode === "new"}
            />{" "}
            Clone
            <span aria-hidden className="ml-1 text-zinc-500">ⓘ</span>
          </label>
        </fieldset>

        <label className="block text-sm">
          {mode === "new" ? "New agent name" : "Existing agent ID"}
          <input
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            required
            className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
            placeholder={mode === "new" ? "e.g. researcher" : "agent-id"}
          />
        </label>

        {agentMode === "clone" && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cloneMemory}
              onChange={(e) => setCloneMemory(e.target.checked)}
            />
            Clone memory (uncheck for empty memory)
          </label>
        )}

        {error && <div role="alert" className="text-red-400 text-xs">{error}</div>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            className="px-3 py-1 text-sm disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-3 py-1 bg-blue-600 rounded text-sm disabled:opacity-50"
          >
            {submitting ? "Adding…" : "Add"}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
