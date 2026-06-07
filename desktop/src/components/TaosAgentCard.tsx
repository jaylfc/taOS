import { useEffect, useState } from "react";
import { ModelPickerModal } from "@/components/ModelPickerModal";
import type { AgentModel } from "@/components/ModelPickerFlow";
import {
  fetchTaosAgentConfig,
  setTaosAgentModel,
  setTaosAgentPermitted,
  setTaosAgentPersona,
  type TaosAgentConfig,
} from "@/lib/taos-agent-api";

export function TaosAgentCard() {
  const [config, setConfig] = useState<TaosAgentConfig | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  // Model picker
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modelErr, setModelErr] = useState<string | null>(null);

  // Permitted models
  const [draftPermitted, setDraftPermitted] = useState<string[]>([]);
  const [draftDirty, setDraftDirty] = useState(false);
  const [addPickerOpen, setAddPickerOpen] = useState(false);
  const [permittedSaving, setPermittedSaving] = useState(false);
  const [permittedErr, setPermittedErr] = useState<string | null>(null);

  // Persona
  const [draftPersona, setDraftPersona] = useState("");
  const [personaDirty, setPersonaDirty] = useState(false);
  const [personaSaving, setPersonaSaving] = useState(false);
  const [personaErr, setPersonaErr] = useState<string | null>(null);

  async function load() {
    try {
      const cfg = await fetchTaosAgentConfig();
      setConfig(cfg);
      setDraftPermitted(cfg.permitted_models);
      setDraftPersona(cfg.persona);
      setDraftDirty(false);
      setPersonaDirty(false);
      setLoadErr(null);
    } catch (e: any) {
      setLoadErr(String(e?.message ?? e));
    }
  }

  useEffect(() => { load(); }, []);

  async function ensureModelsLoaded() {
    if (modelsLoaded) return;
    try {
      const res = await fetch("/api/providers/models?refresh=true", {
        headers: { Accept: "application/json" },
      });
      const data = res.ok ? await res.json() : { data: [] };
      setModels((data.data ?? []).map((m: { id: string }) => ({
        id: m.id, name: m.id, hostKind: "cloud" as const,
      })));
    } catch { /* leave empty */ }
    finally { setModelsLoaded(true); }
  }

  async function openPicker() {
    setPickerOpen(true);
    await ensureModelsLoaded();
  }

  async function openAddPicker() {
    setAddPickerOpen(true);
    await ensureModelsLoaded();
  }

  async function changeModel(modelId: string) {
    setModelErr(null);
    try {
      await setTaosAgentModel(modelId);
      setConfig((prev) => prev ? { ...prev, model: modelId } : prev);
      setPickerOpen(false);
    } catch (e: any) {
      setModelErr(String(e?.message ?? e));
    }
  }

  function addToPermitted(modelId: string) {
    if (draftPermitted.includes(modelId)) return;
    setDraftPermitted([...draftPermitted, modelId]);
    setDraftDirty(true);
    setAddPickerOpen(false);
  }

  function removeFromPermitted(modelId: string) {
    // Cannot remove the current primary model.
    if (modelId === config?.model) return;
    setDraftPermitted(draftPermitted.filter((m) => m !== modelId));
    setDraftDirty(true);
  }

  async function savePermitted() {
    setPermittedSaving(true);
    setPermittedErr(null);
    try {
      const data = await setTaosAgentPermitted(draftPermitted);
      setDraftPermitted(data.permitted_models);
      setDraftDirty(false);
    } catch (e: any) {
      setPermittedErr(String(e?.message ?? e));
    } finally {
      setPermittedSaving(false);
    }
  }

  async function savePersona() {
    setPersonaSaving(true);
    setPersonaErr(null);
    try {
      const data = await setTaosAgentPersona(draftPersona);
      setConfig((prev) => prev ? { ...prev, persona: data.persona } : prev);
      setPersonaDirty(false);
    } catch (e: any) {
      setPersonaErr(String(e?.message ?? e));
    } finally {
      setPersonaSaving(false);
    }
  }

  if (loadErr) {
    return (
      <div
        className="rounded-lg border border-white/10 bg-white/5 p-4 mb-3"
        role="alert"
        aria-label="taOS agent config error"
      >
        <p className="text-xs text-red-400">Failed to load taOS agent config: {loadErr}</p>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="rounded-lg border border-white/10 bg-white/5 p-4 mb-3 animate-pulse">
        <div className="h-3 w-40 bg-white/10 rounded" aria-label="Loading taOS agent" />
      </div>
    );
  }

  const currentModel = config.model;

  return (
    <section
      className="rounded-lg border border-white/10 bg-white/5 p-4 mb-3"
      aria-label="taOS agent system card"
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-sm font-semibold">taOS agent</span>
        <span
          className="bg-indigo-600/40 text-indigo-200 px-2 py-0.5 rounded text-[10px] leading-none"
          aria-label="Framework: opencode"
        >
          opencode
        </span>
        <span
          className="bg-amber-600/40 text-amber-200 px-2 py-0.5 rounded text-[10px] leading-none"
          aria-label="System agent"
        >
          System
        </span>
        <span className="text-xs text-shell-text-tertiary ml-auto">Host-resident</span>
      </div>

      {/* API key (read-only) */}
      {config.key_masked && (
        <div className="flex items-center gap-2 mb-3">
          <span className="opacity-60 text-xs">API key</span>
          <code
            className="text-xs bg-white/5 px-2 py-0.5 rounded"
            aria-label="Masked API key"
          >
            {config.key_masked}
          </code>
        </div>
      )}

      {/* Model */}
      <div className="flex items-center gap-2 flex-wrap mb-1">
        <span className="opacity-60 text-sm">Model</span>
        <code className="text-sm">{currentModel || "(not set)"}</code>
        <button
          onClick={openPicker}
          aria-label="Change taOS agent model"
          className="bg-white/10 hover:bg-white/15 px-2.5 py-1 rounded text-xs"
        >
          Change model
        </button>
      </div>
      {modelErr && <div className="text-xs text-red-400 mb-2">{modelErr}</div>}

      {/* Permitted models */}
      <section className="mt-3" aria-label="Permitted models">
        <div className="flex items-center gap-2 mb-2">
          <span className="opacity-60 text-sm">Permitted models</span>
          <button
            onClick={openAddPicker}
            aria-label="Add a permitted model for taOS agent"
            className="bg-white/10 hover:bg-white/15 px-2.5 py-1 rounded text-xs"
          >
            + Add
          </button>
        </div>

        {draftPermitted.length === 0 ? (
          <p className="text-xs opacity-50">No models in the permitted set yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-2" aria-label="Permitted model chips">
            {draftPermitted.map((m) => {
              const isCurrent = m === currentModel;
              return (
                <li
                  key={m}
                  className="flex items-center gap-1.5 bg-white/10 rounded px-2 py-0.5 text-xs"
                >
                  <code>{m}</code>
                  {isCurrent && (
                    <span
                      className="bg-blue-600/50 text-blue-200 px-1.5 py-0.5 rounded text-[10px] leading-none"
                      aria-label="current primary model"
                    >
                      current
                    </span>
                  )}
                  <button
                    onClick={() => removeFromPermitted(m)}
                    disabled={isCurrent}
                    aria-label={
                      isCurrent
                        ? `Cannot remove current model ${m}`
                        : `Remove ${m} from taOS agent permitted models`
                    }
                    className="opacity-60 hover:opacity-100 disabled:opacity-20 disabled:cursor-not-allowed leading-none"
                  >
                    ×
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {permittedErr && <div className="text-xs text-red-400 mt-2">{permittedErr}</div>}

        {draftDirty && (
          <button
            onClick={savePermitted}
            disabled={permittedSaving}
            aria-label="Save taOS agent permitted models"
            aria-busy={permittedSaving}
            className="mt-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1.5 rounded text-xs"
          >
            {permittedSaving ? "Saving…" : "Save"}
          </button>
        )}
      </section>

      {/* Persona (system-prompt override) */}
      <section className="mt-3" aria-label="Persona override">
        <div className="flex items-center gap-2 mb-1">
          <span className="opacity-60 text-sm">Persona</span>
        </div>
        <textarea
          value={draftPersona}
          onChange={(e) => {
            setDraftPersona(e.target.value);
            setPersonaDirty(e.target.value !== (config.persona ?? ""));
          }}
          rows={4}
          aria-label="taOS agent persona — system-prompt override"
          placeholder="Leave empty to use the built-in manual as the system prompt."
          className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-xs resize-y focus:outline-none focus:border-white/25"
        />
        {personaErr && <div className="text-xs text-red-400 mt-1">{personaErr}</div>}
        {personaDirty && (
          <button
            onClick={savePersona}
            disabled={personaSaving}
            aria-label="Save taOS agent persona"
            aria-busy={personaSaving}
            className="mt-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1.5 rounded text-xs"
          >
            {personaSaving ? "Saving…" : "Save"}
          </button>
        )}
      </section>

      {/* Model picker modals */}
      <ModelPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        models={models}
        modelsLoaded={modelsLoaded}
        onSelect={(modelId) => changeModel(modelId)}
        title="Change model"
      />
      <ModelPickerModal
        open={addPickerOpen}
        onClose={() => setAddPickerOpen(false)}
        models={models}
        modelsLoaded={modelsLoaded}
        onSelect={(modelId) => addToPermitted(modelId)}
        title="Add permitted model"
      />
    </section>
  );
}
