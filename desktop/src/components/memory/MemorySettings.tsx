import { useState, useEffect, useCallback } from "react";
import { Save, RefreshCw, CheckCircle, AlertTriangle, Wifi, WifiOff, Monitor, Globe } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetchSettingsSchema, fetchMemorySettings, updateMemorySettings, fetchMemoryEndpoint, updateMemoryEndpoint, fetchBackendCapabilities } from "@/lib/memory";
import type { TaOSmdEndpoint } from "@/lib/memory";
import { fetchMemoryModel, setMemoryModel } from "@/lib/memory-api";
import { ModelPickerModal } from "@/components/ModelPickerModal";
import type { AgentModel } from "@/components/ModelPickerFlow";
import { SchemaFormRenderer } from "./SchemaFormRenderer";

/* ------------------------------------------------------------------ */
/*  TaOSmdEndpointCard — running mode + switch-to-remote                */
/* ------------------------------------------------------------------ */

const LOCAL_URL = "http://localhost:7900";

function TaOSmdEndpointCard() {
  const [endpoint, setEndpoint] = useState<TaOSmdEndpoint | null>(null);
  const [capabilities, setCapabilities] = useState<{ name: string; version: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [remoteUrl, setRemoteUrl] = useState("");
  const [switching, setSwitching] = useState(false);
  const [switchErr, setSwitchErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setSwitchErr(null);
    try {
      const [ep, caps] = await Promise.all([
        fetchMemoryEndpoint(),
        fetchBackendCapabilities(),
      ]);
      setEndpoint(ep);
      setCapabilities({ name: caps.name, version: caps.version });
      setRemoteUrl((ep?.url && ep.url !== LOCAL_URL) ? ep.url : "");
    } catch {
      /* graceful — will show fallback */
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSwitch = async () => {
    const url = remoteUrl.trim();
    if (!url) return;
    setSwitching(true);
    setSwitchErr(null);
    try {
      const result = await updateMemoryEndpoint(url);
      setEndpoint(result);
      if (!result.reachable && !result.is_local) {
        setSwitchErr("Server is not reachable. Check the URL and try again.");
      }
    } catch (e: any) {
      setSwitchErr(String(e?.message ?? e));
    }
    setSwitching(false);
  };

  const handleRevert = async () => {
    setSwitching(true);
    setSwitchErr(null);
    try {
      const result = await updateMemoryEndpoint(LOCAL_URL);
      setEndpoint(result);
      setRemoteUrl("");
    } catch (e: any) {
      setSwitchErr(String(e?.message ?? e));
    }
    setSwitching(false);
  };

  if (loading) {
    return (
      <Card className="bg-white/[0.02] border-white/8">
        <CardContent className="p-4">
          <p className="text-xs text-shell-text-tertiary" aria-label="Loading taOSmd status">
            Loading taOSmd status…
          </p>
        </CardContent>
      </Card>
    );
  }

  if (!endpoint) return null;

  const isLocal = endpoint.is_local;
  const modeLabel = isLocal ? "LOCAL" : "REMOTE";
  const ModeIcon = isLocal ? Monitor : Globe;
  const modeClasses = isLocal
    ? "bg-green-500/15 text-green-400 border-green-500/30"
    : "bg-blue-500/15 text-blue-400 border-blue-500/30";

  return (
    <Card className="bg-white/[0.02] border-white/8">
      <CardContent className="p-4 flex flex-col gap-3">
        {/* Header */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <p className="text-xs uppercase opacity-60">taOSmd Status</p>
            <span
              className={`text-[10px] font-semibold px-1.5 py-px rounded-full border ${modeClasses}`}
              aria-label={`Mode: ${modeLabel}`}
            >
              <ModeIcon size={10} className="inline mr-0.5" aria-hidden="true" />
              {modeLabel}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={load}
            disabled={loading}
            aria-label="Refresh taOSmd status"
            className="h-7 px-2.5 text-xs"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} aria-hidden="true" />
            Refresh
          </Button>
        </div>

        {/* Reachability */}
        <div className="flex items-center gap-2 text-xs">
          {endpoint.reachable ? (
            <Wifi size={13} className="text-green-400 shrink-0" aria-hidden="true" />
          ) : (
            <WifiOff size={13} className="text-red-400 shrink-0" aria-hidden="true" />
          )}
          <span
            className={endpoint.reachable ? "text-green-400" : "text-red-400"}
            aria-label={endpoint.reachable ? "Reachable" : "Not reachable"}
          >
            {endpoint.reachable ? "Reachable" : "Not reachable"}
          </span>
          <span className="opacity-40" aria-label="Server URL">{endpoint.url || LOCAL_URL}</span>
        </div>

        {/* Capabilities / Tier */}
        <div className="flex items-center gap-3 text-xs flex-wrap">
          {capabilities && (
            <span className="opacity-50" aria-label={`Backend ${capabilities.name} v${capabilities.version}`}>
              {capabilities.name} v{capabilities.version}
            </span>
          )}
          {endpoint.tier && (
            <span
              className="px-1.5 py-px rounded-full border border-white/10 bg-white/[0.03] text-[10px] font-medium opacity-60"
              aria-label={`Memory tier: ${endpoint.tier}`}
            >
              Tier: {endpoint.tier}
            </span>
          )}
        </div>

        {/* Switch to remote */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="taosmd-remote-url" className="text-xs font-normal opacity-60">
            Switch to remote instance
          </Label>
          <div className="flex items-center gap-2">
            <Input
              id="taosmd-remote-url"
              type="url"
              placeholder="http://192.168.1.x:7900"
              value={remoteUrl}
              onChange={(e) => { setRemoteUrl(e.target.value); setSwitchErr(null); }}
              disabled={switching}
              className="h-8 text-xs bg-white/[0.04] border-white/10"
              aria-label="Remote taOSmd URL"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={handleSwitch}
              disabled={switching || !remoteUrl.trim() || remoteUrl.trim() === (endpoint.url || LOCAL_URL)}
              aria-label="Connect to remote taOSmd"
              className="h-8 px-3 text-xs shrink-0"
            >
              {switching ? <RefreshCw size={12} className="animate-spin" aria-hidden="true" /> : "Connect"}
            </Button>
          </div>
        </div>

        {/* Revert to local */}
        {!isLocal && (
          <div className="flex">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRevert}
              disabled={switching}
              aria-label="Revert to local taOSmd"
              className="h-7 px-2.5 text-xs opacity-70 hover:opacity-100"
            >
              <Monitor size={12} className="mr-1" aria-hidden="true" />
              Revert to local
            </Button>
          </div>
        )}

        {/* Errors */}
        {switchErr && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/20" role="alert" aria-live="assertive">
            <AlertTriangle size={13} className="text-red-400 shrink-0" aria-hidden="true" />
            <p className="text-xs text-red-300">{switchErr}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

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

      {/* taOSmd endpoint status */}
      <TaOSmdEndpointCard />

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
