import { useState, useEffect, useRef } from "react";
import { AlertCircle } from "lucide-react";
import { Card, Label, Switch } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

interface OfflineModelData {
  available: boolean;
  enabled: boolean;
  running: boolean;
  healthy: boolean;
  model: string;
  licence: string;
  licence_note: string;
  performance_note: string;
}

/** Settings -> Offline model (LFM3 1.7B on-device).
 *
 *  GET /api/system/offline-model reports availability, the saved preference
 *  and the runtime status; PUT flips the preference (signed-in admin session
 *  only -- member, host local token and agent token callers are refused 403).
 */
export function OfflineModelPanel() {
  const [data, setData] = useState<OfflineModelData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/system/offline-model", {
          credentials: "include",
        });
        const body = await res.json().catch(() => null);
        if (!mounted.current) return;
        if (
          !res.ok ||
          !body ||
          typeof body.available !== "boolean" ||
          typeof body.enabled !== "boolean"
        ) {
          setError("Could not load offline model status.");
          return;
        }
        setData(body as OfflineModelData);
      } catch {
        if (mounted.current) setError("Could not reach taOS.");
      }
    })();
  }, []);

  const toggle = async (next: boolean) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/system/offline-model", withCsrf({
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || typeof body?.status !== "string") {
        setError(body?.error || body?.detail || `Failed to save (${res.status})`);
        return;
      }
      if (data) {
        setData({ ...data, enabled: body.enabled ?? next });
      }
    } catch {
      if (mounted.current) setError("Could not reach taOS.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  if (!data) {
    return (
      <section aria-label="Offline model settings">
        <h2 className="text-lg font-semibold mb-2">Offline model</h2>
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      </section>
    );
  }

  if (!data.available) {
    return (
      <section aria-label="Offline model settings">
        <h2 className="text-lg font-semibold mb-2">Offline model</h2>
        <p role="status" className="text-sm text-shell-text-tertiary">
          The offline model is not available on this device.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Offline model settings">
      <h2 className="text-lg font-semibold mb-2">Offline model</h2>
      {error && (
        <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {error}
        </p>
      )}
      <Card className="p-4 flex items-center justify-between gap-3">
        <div className="flex-1 min-w-0">
          <Label htmlFor="offline-model" className="text-sm font-medium text-shell-text">
            Offline model
          </Label>
          <p className="text-xs text-shell-text-tertiary mt-0.5">
            {data.model} -- runs on this device, no network.
          </p>
          {data.licence_note && (
            <p className="text-xs text-shell-text-tertiary mt-1">
              {data.licence_note}
            </p>
          )}
          {data.performance_note && (
            <p className="text-xs text-shell-text-tertiary mt-1">
              {data.performance_note}
            </p>
          )}
        </div>
        <Switch
          id="offline-model"
          checked={data.enabled}
          onCheckedChange={(v) => toggle(v)}
          disabled={busy}
          aria-label={`Offline model ${data.running ? "running" : "stopped"}`}
        />
      </Card>
      <p className="text-xs text-shell-text-tertiary mt-3">
        {data.running ? "Running and healthy." : "Stopped."}
      </p>
    </section>
  );
}
