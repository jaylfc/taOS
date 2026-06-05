import { useState, useEffect } from "react";
import { Bot, Settings, X } from "lucide-react";
import { ModelPickerModal } from "@/components/ModelPickerModal";
import type { AgentModel } from "@/components/ModelPickerFlow";
import {
  fetchTaosAgentConfig,
  setTaosAgentModel,
  setTaosAgentPermitted,
  setTaosAgentPersona,
  type TaosAgentConfig,
} from "@/lib/taos-agent-api";
import {
  Button,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";

/* ------------------------------------------------------------------ */
/*  TaosAgentDetailPanel                                               */
/*  Same chrome as AgentDetailPanel but wired to /api/taos-agent/*    */
/* ------------------------------------------------------------------ */

type TaosDetailTab = "settings" | "persona";

export function TaosAgentDetailPanel({
  onClose,
  fullHeight = false,
}: {
  onClose: () => void;
  fullHeight?: boolean;
}) {
  const [tab, setTab] = useState<TaosDetailTab>("settings");
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

  const currentModel = config?.model ?? null;

  return (
    <Tabs
      value={tab}
      onValueChange={(v) => setTab(v as TaosDetailTab)}
      className={
        fullHeight
          ? "border-t border-white/5 bg-shell-bg-deep flex flex-1 min-h-0 flex-col"
          : "border-t border-white/5 bg-shell-bg-deep flex flex-col"
      }
      style={fullHeight ? undefined : { height: "22rem" }}
    >
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-sm">
            <span className="text-base leading-none" aria-hidden="true">🤖</span>
            <span className="font-medium">taOS agent</span>
            <span
              className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium lowercase shrink-0 bg-white/5 text-shell-text-secondary border border-white/10"
              aria-label="System agent"
            >
              system
            </span>
          </div>
          <TabsList aria-label="taOS agent detail tabs">
            <TabsTrigger value="settings">
              <Settings size={13} className="mr-1.5" />
              Settings
            </TabsTrigger>
            <TabsTrigger value="persona">
              <Bot size={13} className="mr-1.5" />
              Persona
            </TabsTrigger>
          </TabsList>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onClose}
          aria-label="Close detail panel"
        >
          <X size={14} />
        </Button>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {/* Settings tab: model + permitted models */}
        <TabsContent value="settings" className="h-full mt-0">
          <div className="h-full overflow-auto p-4 flex flex-col gap-4">
            {loadErr ? (
              <div className="text-xs text-red-400" role="alert">
                Failed to load taOS agent config: {loadErr}
              </div>
            ) : !config ? (
              <div className="text-sm opacity-60">Loading…</div>
            ) : (
              <>
                <div className="text-sm">
                  This is the taOS system agent. It runs <b>opencode</b> and is always present.
                </div>

                {/* Model */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="opacity-60 text-sm">Model</span>
                  <code className="text-sm">{currentModel || "(not set)"}</code>
                  <button
                    onClick={async () => { setPickerOpen(true); await ensureModelsLoaded(); }}
                    aria-label="Change taOS agent model"
                    className="bg-white/10 hover:bg-white/15 px-2.5 py-1 rounded text-xs"
                  >
                    Change model
                  </button>
                </div>
                {modelErr && <div className="text-xs text-red-400">{modelErr}</div>}

                {/* Permitted models */}
                <section aria-label="Permitted models">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="opacity-60 text-sm">Permitted models</span>
                    <button
                      onClick={async () => { setAddPickerOpen(true); await ensureModelsLoaded(); }}
                      aria-label="Add permitted model for taOS agent"
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

                {config.key_masked && (
                  <div className="flex items-center gap-2">
                    <span className="opacity-60 text-xs">API key</span>
                    <code className="text-xs bg-white/5 px-2 py-0.5 rounded" aria-label="Masked API key">
                      {config.key_masked}
                    </code>
                  </div>
                )}
              </>
            )}
          </div>
        </TabsContent>

        {/* Persona tab */}
        <TabsContent value="persona" className="h-full mt-0">
          <div className="h-full overflow-auto p-4 flex flex-col gap-3">
            {loadErr ? (
              <div className="text-xs text-red-400" role="alert">
                Failed to load: {loadErr}
              </div>
            ) : !config ? (
              <div className="text-sm opacity-60">Loading…</div>
            ) : (
              <>
                <label className="flex flex-col gap-1">
                  <span className="text-xs uppercase opacity-60">Persona (system-prompt override)</span>
                  <textarea
                    value={draftPersona}
                    onChange={(e) => {
                      setDraftPersona(e.target.value);
                      setPersonaDirty(e.target.value !== (config.persona ?? ""));
                    }}
                    rows={10}
                    aria-label="taOS agent persona — system-prompt override"
                    placeholder="Leave empty to use the built-in manual as the system prompt."
                    className="border border-white/10 rounded bg-shell-bg px-2 py-1 font-mono text-xs text-shell-text-secondary resize-none focus:outline-none focus:ring-1 focus:ring-white/20"
                  />
                </label>
                {personaErr && <div className="text-xs text-red-400">{personaErr}</div>}
                {personaDirty && (
                  <button
                    onClick={savePersona}
                    disabled={personaSaving}
                    aria-label="Save taOS agent persona"
                    aria-busy={personaSaving}
                    className="self-end bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-1.5 rounded text-sm text-white transition-colors"
                  >
                    {personaSaving ? "Saving…" : "Save"}
                  </button>
                )}
              </>
            )}
          </div>
        </TabsContent>
      </div>

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
    </Tabs>
  );
}
