import { useEffect, useState } from "react";
import { fetchFrameworkState, FrameworkState, startFrameworkUpdate } from "@/lib/framework-api";
import { ModelPickerModal } from "@/components/ModelPickerModal";
import type { AgentModel } from "@/components/ModelPickerFlow";

export function FrameworkTab(
  { agent, onUpdated }: { agent: { name: string; model?: string }; onUpdated: () => void },
) {
  const [state, setState] = useState<FrameworkState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  // Model change — swap the agent's primary model without a redeploy
  // (POST /api/agents/{name}/model updates the LiteLLM route + resumes).
  const [currentModel, setCurrentModel] = useState<string | undefined>(agent.model);
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modelErr, setModelErr] = useState<string | null>(null);

  // Routable models = LiteLLM /v1/models passthrough (single source of truth
  // for what an agent can use). Loaded when the picker is first opened.
  async function openPicker() {
    setPickerOpen(true);
    if (modelsLoaded) return;
    try {
      const res = await fetch("/api/providers/models?refresh=true", {
        headers: { Accept: "application/json" },
      });
      const data = res.ok ? await res.json() : { data: [] };
      setModels((data.data ?? []).map((m: { id: string }) => ({
        id: m.id, name: m.id, hostKind: "cloud" as const,
      })));
    } catch { /* leave empty — picker shows no models */ }
    finally { setModelsLoaded(true); }
  }

  async function changeModel(modelId: string) {
    setModelErr(null);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agent.name)}/model`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelId }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        setModelErr(String(e.error ?? e.detail ?? `Failed (${res.status})`));
        return;
      }
      setCurrentModel(modelId);
      setPickerOpen(false);
      onUpdated();
    } catch {
      setModelErr("Couldn't reach the server.");
    }
  }

  async function load() {
    try { setState(await fetchFrameworkState(agent.name)); setErr(null); }
    catch (e: any) { setErr(String(e)); }
  }

  useEffect(() => { load(); }, [agent.name]);

  useEffect(() => {
    if (state?.update_status !== "updating") return;
    const id = setInterval(() => { load(); }, 2000);
    return () => clearInterval(id);
  }, [state?.update_status]);

  useEffect(() => {
    if (state?.update_status !== "updating" || !state.update_started_at) { setElapsed(0); return; }
    const tick = () => setElapsed(Math.floor(Date.now() / 1000) - (state.update_started_at ?? 0));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [state?.update_status, state?.update_started_at]);

  async function doUpdate() {
    setSubmitting(true);
    try {
      // Pin the request to the exact tag the user just confirmed so the
      // backend can't drift to a newer release if its cache advances mid-click.
      await startFrameworkUpdate(agent.name, state?.latest?.tag);
      // Optimistically flip to "updating" so the polling effect arms even
      // if a racing load() reads an idle status before the backend writes.
      setState((prev) => prev ? { ...prev, update_status: "updating", update_started_at: Math.floor(Date.now() / 1000) } : prev);
      await load();
      onUpdated();
    } catch (e: any) { setErr(String(e)); }
    finally { setSubmitting(false); setConfirming(false); }
  }

  if (err) return <div className="p-4 text-sm text-red-400">Error: {err}</div>;
  if (!state) return <div className="p-4 text-sm opacity-60">Loading…</div>;

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="text-sm">This agent runs <b>{state.framework}</b></div>

      {/* Model — change the agent's primary model without a redeploy. */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="opacity-60 text-sm">Model</span>
        <code className="text-sm">{currentModel || "(not set)"}</code>
        <button
          onClick={openPicker}
          className="bg-white/10 hover:bg-white/15 px-2.5 py-1 rounded text-xs"
        >
          Change model
        </button>
      </div>
      {modelErr && <div className="text-xs text-red-400">{modelErr}</div>}
      <dl className="grid grid-cols-[120px_1fr] gap-y-1 text-sm">
        <dt className="opacity-60">Installed</dt>
        <dd><code>{state.installed.tag ?? "(unknown)"}</code> · <code>{state.installed.sha ?? "—"}</code></dd>
        <dt className="opacity-60">Latest</dt>
        <dd>
          {state.latest
            ? <><code>{state.latest.tag}</code> · <code>{state.latest.sha}</code>
                {state.latest.published_at && <span className="opacity-60 ml-2">published {state.latest.published_at}</span>}</>
            : <span className="opacity-60">(not available)</span>}
        </dd>
      </dl>

      {state.update_available && state.update_status === "idle" && (
        <div className="flex items-center gap-2">
          <span className="bg-yellow-700/30 text-yellow-200 px-2 py-0.5 rounded text-xs">Update available</span>
          <button onClick={() => setConfirming(true)} disabled={submitting}
                  className="bg-blue-600 px-3 py-1.5 rounded text-sm">
            Update Framework
          </button>
        </div>
      )}

      {!state.update_available && state.update_status === "idle" && state.latest && (
        <div className="text-sm text-green-400">✓ You're on the latest version</div>
      )}

      {state.update_status === "updating" && (
        <div className="bg-white/5 border border-white/10 rounded px-3 py-2 text-sm">
          Updating {state.framework}… started {elapsed}s ago.
        </div>
      )}

      {state.update_status === "failed" && (
        <div className="bg-red-950/40 border border-red-800 rounded px-3 py-2 text-sm">
          <div>Update failed: {state.last_error}</div>
          {state.last_snapshot && (
            <div className="opacity-70 mt-1">Snapshot retained: <code>{state.last_snapshot}</code></div>
          )}
        </div>
      )}

      {confirming && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-shell-bg border border-white/10 rounded p-4 max-w-sm">
            <p className="text-sm mb-3">
              Update {agent.name}'s {state.framework} to <code>{state.latest?.tag ?? "latest"}</code>?
              The agent will go offline for up to 2 minutes. Messages will queue.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirming(false)} className="opacity-60 text-sm">Cancel</button>
              <button onClick={doUpdate} disabled={submitting} className="bg-blue-600 px-3 py-1.5 rounded text-sm">
                {submitting ? "Starting…" : "Update"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="mt-auto pt-4 text-xs opacity-50">Switch framework — coming soon</div>

      <ModelPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        models={models}
        modelsLoaded={modelsLoaded}
        onSelect={(modelId) => changeModel(modelId)}
        title="Change model"
      />
    </div>
  );
}
