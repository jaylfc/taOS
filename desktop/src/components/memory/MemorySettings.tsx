import { useState, useEffect, useCallback } from "react";
import { Save, RefreshCw, CheckCircle, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchSettingsSchema, fetchMemorySettings, updateMemorySettings } from "@/lib/memory";
import { fetchMemoryModel, setMemoryModel } from "@/lib/memory-api";
import { ModelPickerModal } from "@/components/ModelPickerModal";
import type { AgentModel } from "@/components/ModelPickerFlow";
import { SchemaFormRenderer } from "./SchemaFormRenderer";

/* ------------------------------------------------------------------ */
/*  MemoryModelSection                                                 */
/* ------------------------------------------------------------------ */

function MemoryModelSection() {
  const [model, setModel] = useState<string | null>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchMemoryModel()
      .then((d) => { setModel(d.model); setSupported(d.supported); })
      .catch(() => setSupported(false));
  }, []);

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
    } catch { /* leave empty */ }
    finally { setModelsLoaded(true); }
  }

  async function handleSelect(modelId: string) {
    setSaving(true);
    setErr(null);
    try {
      const result = await setMemoryModel({ model: modelId });
      setModel(result.model);
      setPickerOpen(false);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    setSaving(true);
    setErr(null);
    try {
      const result = await setMemoryModel({ clear: true });
      setModel(result.model);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  if (supported === null) return null;

  if (!supported) {
    return (
      <Card className="bg-white/[0.02] border-white/8">
        <CardContent className="p-4">
          <p className="text-xs text-shell-text-tertiary" aria-label="Memory model not supported">
            Memory model selection needs a newer taOSmd.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="bg-white/[0.02] border-white/8">
      <CardContent className="p-4 flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <p className="text-xs uppercase opacity-60 mb-0.5">Memory model</p>
            <p className="text-sm font-medium" aria-label="Current memory model">
              {model ?? "Built-in default"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={openPicker}
              disabled={saving}
              aria-label="Change memory model"
              className="h-7 px-2.5 text-xs"
            >
              Change model
            </Button>
            {model !== null && (
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClear}
                disabled={saving}
                aria-label="Use built-in default memory model"
                className="h-7 px-2.5 text-xs opacity-70 hover:opacity-100"
              >
                Use built-in default
              </Button>
            )}
          </div>
        </div>
        {err && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/20" role="alert" aria-live="assertive">
            <AlertTriangle size={13} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-300">{err}</p>
          </div>
        )}
        <ModelPickerModal
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          models={models}
          modelsLoaded={modelsLoaded}
          onSelect={(modelId) => handleSelect(modelId)}
          title="Change memory model"
        />
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  MemorySettings                                                     */
/* ------------------------------------------------------------------ */

export function MemorySettings() {
  const [schema, setSchema] = useState<Record<string, any>>({});
  const [values, setValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<'idle' | 'saved' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    const [s, v] = await Promise.all([fetchSettingsSchema(), fetchMemorySettings()]);
    setSchema(s?.properties ?? s ?? {});
    setValues(v ?? {});
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleChange = (key: string, value: any) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setStatus('idle');
  };

  const handleSave = async () => {
    setSaving(true);
    setStatus('idle');
    try {
      const result = await updateMemorySettings(values);
      if (result?.error) {
        setStatus('error');
        setErrorMsg(result.error);
      } else {
        setStatus('saved');
        setTimeout(() => setStatus('idle'), 2500);
      }
    } catch (e: any) {
      setStatus('error');
      setErrorMsg(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="flex flex-col gap-5 p-4 overflow-auto h-full" aria-label="Memory settings">
      {/* System-wide memory model */}
      <MemoryModelSection />

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-shell-text">Backend Settings</h2>
        <Button
          variant="ghost"
          size="sm"
          onClick={load}
          disabled={loading}
          aria-label="Reload settings"
          className="h-7 px-2 gap-1.5 text-xs"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          Reload
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12 text-shell-text-tertiary text-sm">
          Loading settings…
        </div>
      ) : (
        <Card className="bg-white/[0.02] border-white/8">
          <CardContent className="p-5">
            <SchemaFormRenderer
              schema={schema}
              values={values}
              onChange={handleChange}
            />
          </CardContent>
        </Card>
      )}

      {/* Status messages */}
      {status === 'saved' && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-green-500/10 border border-green-500/20" role="status" aria-live="polite">
          <CheckCircle size={13} className="text-green-400 shrink-0" aria-hidden="true" />
          <p className="text-xs text-green-300">Settings saved.</p>
        </div>
      )}
      {status === 'error' && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/20" role="alert" aria-live="assertive">
          <AlertTriangle size={13} className="text-red-400 shrink-0" aria-hidden="true" />
          <p className="text-xs text-red-300">{errorMsg || 'Failed to save settings.'}</p>
        </div>
      )}

      {/* Save button */}
      {!loading && (
        <div className="flex">
          <Button
            onClick={handleSave}
            disabled={saving}
            aria-label="Save memory settings"
            className="gap-1.5"
          >
            {saving ? (
              <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            ) : (
              <Save size={14} aria-hidden="true" />
            )}
            {saving ? 'Saving…' : 'Save Settings'}
          </Button>
        </div>
      )}
    </section>
  );
}
