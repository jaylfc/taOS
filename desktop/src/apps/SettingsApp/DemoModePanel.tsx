import { useState, useEffect, useRef } from "react";
import { AlertCircle } from "lucide-react";
import { Card, Label, Switch } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

export const DEMO_CAPTION =
  "Shows scripted agents, notifications and calls on the lock screen for demonstrations.";

/** Settings -> Demo mode (admin). One switch over every TAOS_LOCK_DEMO_* flag:
 *  the flags define WHAT demo content exists, this decides whether it shows. */
export function DemoModePanel() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [available, setAvailable] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/settings/demo-mode", { credentials: "include" });
        const body = await res.json().catch(() => null);
        if (!mounted.current) return;
        if (!res.ok || typeof body?.enabled !== "boolean") {
          setError("Could not load demo mode.");
          return;
        }
        setEnabled(body.enabled);
        setAvailable(body.available !== false);
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
      const res = await fetch("/api/settings/demo-mode", withCsrf({
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      }));
      const body = await res.json().catch(() => null);
      if (!mounted.current) return;
      if (!res.ok || typeof body?.enabled !== "boolean") {
        setError(body?.error || body?.detail || `Failed to save (${res.status})`);
        return;
      }
      setEnabled(body.enabled);
    } catch {
      if (mounted.current) setError("Could not reach taOS.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <section aria-label="Demo mode settings">
      <h2 className="text-lg font-semibold mb-2">Demo mode</h2>
      {error && (
        <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {error}
        </p>
      )}
      {enabled !== null && (
        <Card className="p-4 flex items-center justify-between gap-3">
          <div className="flex-1 min-w-0">
            <Label htmlFor="demo-mode" className="text-sm font-medium text-shell-text">
              Demo mode
            </Label>
            <p className="text-xs text-shell-text-tertiary mt-0.5">{DEMO_CAPTION}</p>
            {!available && (
              <p className="text-xs text-shell-text-tertiary mt-1">
                No demo content is configured on this device.
              </p>
            )}
          </div>
          <Switch
            id="demo-mode"
            checked={enabled}
            onCheckedChange={(v) => toggle(v)}
            disabled={busy}
            aria-label="Demo mode"
          />
        </Card>
      )}
    </section>
  );
}
