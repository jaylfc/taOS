import { useState } from "react";
import type { PersonaSelection } from "./types";

export function PersonaCreate({ onSelect }: { onSelect: (s: PersonaSelection) => void }) {
  const [soul, setSoul] = useState("");
  const [agentMd, setAgentMd] = useState("");
  const [save, setSave] = useState(false);
  const [saveName, setSaveName] = useState("");

  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase opacity-60">Soul (identity)</span>
        <textarea
          aria-label="Soul (identity)"
          value={soul}
          onChange={(e) => setSoul(e.target.value)}
          rows={6}
          className="rounded border border-white/20 bg-white/5 px-2 py-1 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
          placeholder="You are…"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase opacity-60">Agent.md (operational rules)</span>
        <textarea
          aria-label="Agent.md (operational rules)"
          value={agentMd}
          onChange={(e) => setAgentMd(e.target.value)}
          rows={5}
          className="rounded border border-white/20 bg-white/5 px-2 py-1 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
          placeholder="Guardrails, project context, tool guidance…"
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={save}
          onChange={(e) => setSave(e.target.checked)}
        />
        Save to my persona library for reuse
      </label>
      {save && (
        <input
          aria-label="Persona library name"
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
          placeholder="Name for your library entry"
          className="rounded border border-white/20 bg-white/5 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
      )}
      <button
        disabled={!soul.trim() && !agentMd.trim()}
        onClick={() =>
          onSelect({
            kind: "custom",
            soul_md: soul,
            agent_md: agentMd,
            save_to_library: save ? { name: saveName || "Untitled" } : undefined,
          })
        }
        className="self-start rounded bg-blue-600 px-3 py-1.5 text-sm font-medium hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-50"
      >
        Use this persona
      </button>
    </div>
  );
}
