import { useState, useEffect, useRef } from "react";
import { AlertCircle } from "lucide-react";
import { Card, Label, Switch } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

export const PERFORMANCE_CAPTION =
  "Runs the RK3588's NPU, GPU, memory controller and big CPU cores at full clocks so local inference stays at full throughput. Turning it off restores the default power-saving governors.";

/** Settings -> Performance (admin, RK3588 only — SettingsApp hides this
 *  section on other hardware). One switch over the taos-rk3588-perf.service
 *  unit: enabling applies the devfreq governor tuning immediately and at
 *  every boot; disabling restores simple_ondemand. */
export function PerformancePanel() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [installed, setInstalled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    // StrictMode runs setup -> cleanup -> setup in dev; restoring the flag
    // in setup keeps the second mount's load completions from being dropped.
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/settings/rk3588-performance", { credentials: "include" });
        const body = await res.json().catch(() => null);
        if (!mounted.current) return;
        if (!res.ok || typeof body?.enabled !== "boolean") {
          setError("Could not load performance mode.");
          return;
        }
        setEnabled(body.enabled);
        setInstalled(body.installed !== false);
      } catch {
        if (mounted.current) setError("Could not reach taOS.");
      }
    })();
  }, []);

  // Re-read the unit state after a failed save. `enable --now` changes
  // boot enablement before it starts the unit, so a failed save can leave
  // the real state different from what the switch shows; if the re-read
  // also fails, drop to unknown (null) instead of presenting the stale
  // value as current. Never touches the error state — the save's own
  // error message stands.
  const reconcile = async () => {
    try {
      const res = await fetch("/api/settings/rk3588-performance", { credentials: "include" });
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (res.ok && typeof body?.enabled === "boolean") {
        setEnabled(body.enabled);
        setInstalled(body.installed !== false);
      } else {
        setEnabled(null);
      }
    } catch {
      if (mounted.current) setEnabled(null);
    }
  };

  const toggle = async (next: boolean) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/settings/rk3588-performance", withCsrf({
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || typeof body?.enabled !== "boolean") {
        setError(body?.error || body?.detail || `Failed to save (${res.status})`);
        await reconcile();
        return;
      }
      setEnabled(body.enabled);
      setInstalled(body.installed !== false);
    } catch {
      if (mounted.current) {
        setError("Could not reach taOS.");
        // The PUT may still have landed; re-read rather than trust the
        // pre-save value.
        await reconcile();
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <section aria-label="Performance settings">
      <h2 className="text-lg font-semibold mb-2">Performance mode</h2>
      {error && (
        <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {error}
        </p>
      )}
      {enabled !== null && (
        <Card className="p-4 flex items-center justify-between gap-3">
          <div className="flex-1 min-w-0">
            <Label htmlFor="performance-mode" className="text-sm font-medium text-shell-text">
              Performance mode
            </Label>
            <p className="text-xs text-shell-text-tertiary mt-0.5">{PERFORMANCE_CAPTION}</p>
            {!installed && (
              <p className="text-xs text-shell-text-tertiary mt-1">
                The performance service is not installed on this device. It is installed
                automatically with the RK3588 NPU backend.
              </p>
            )}
          </div>
          <Switch
            id="performance-mode"
            checked={enabled}
            onCheckedChange={(v) => toggle(v)}
            disabled={busy || !installed}
            aria-label="Performance mode"
          />
        </Card>
      )}
    </section>
  );
}
