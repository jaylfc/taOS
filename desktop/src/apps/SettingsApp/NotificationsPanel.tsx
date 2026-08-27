import { useState, useEffect, useCallback } from "react";
import { Button, Card, Label, Switch } from "@/components/ui";

interface NotifPref {
  event_type: string;
  muted: boolean;
}

const NOTIF_TYPES = [
  { type: "worker.join", label: "Worker joined", desc: "A new worker connects to the cluster" },
  { type: "worker.online", label: "Worker online", desc: "A worker comes online" },
  { type: "worker.leave", label: "Worker left", desc: "A worker disconnects from the cluster" },
  { type: "backend.up", label: "Backend up", desc: "A model backend comes online" },
  { type: "backend.down", label: "Backend down", desc: "A model backend goes offline" },
  { type: "training.complete", label: "Training complete", desc: "A training run finishes successfully" },
  { type: "training.failed", label: "Training failed", desc: "A training run fails" },
  { type: "app.installed", label: "App installed", desc: "A new app is installed" },
  { type: "app.failed", label: "App install failed", desc: "An app installation fails" },
  { type: "disk_quota", label: "Disk quota", desc: "Disk usage approaches the limit" },
  { type: "task.claimed", label: "Task claimed", desc: "A task is claimed by a worker" },
  { type: "task.closed", label: "Task closed", desc: "A task is closed" },
];

export function NotificationsPanel() {
  const [prefs, setPrefs] = useState<NotifPref[] | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadPrefs = useCallback(() => {
    setError(null);
    setLoaded(false);
    fetch("/api/notifications/prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && Array.isArray(data)) setPrefs(data);
        else setError("Could not load notification preferences.");
      })
      .catch(() => setError("Could not reach backend."))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    loadPrefs();
  }, [loadPrefs]);

  const toggle = async (eventType: string, muted: boolean) => {
    setSaving(eventType);
    setError(null);
    try {
      const res = await fetch(`/api/notifications/prefs/${encodeURIComponent(eventType)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ muted }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error || `Failed to save (${res.status})`);
        return;
      }
      setPrefs((prev) =>
        prev
          ? prev.map((p) => (p.event_type === eventType ? { ...p, muted } : p))
          : prev
      );
    } catch {
      setError("Could not reach backend.");
    } finally {
      setSaving(null);
    }
  };

  if (!loaded) {
    return (
      <section aria-label="Notification settings">
        <h2 className="text-lg font-semibold mb-5">Notifications</h2>
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      </section>
    );
  }

  if (!prefs) {
    return (
      <section aria-label="Notification settings">
        <h2 className="text-lg font-semibold mb-2">Notifications</h2>
        <p className="text-sm text-shell-text-tertiary mb-5">
          Choose which notification types to receive. Disabled types are suppressed
          everywhere -- no in-app notification, no web push.
        </p>

        {error && (
          <>
            <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
              {error}
            </p>
            <Button variant="outline" size="sm" onClick={loadPrefs}>
              Retry
            </Button>
          </>
        )}
      </section>
    );
  }

  return (
    <section aria-label="Notification settings">
      <h2 className="text-lg font-semibold mb-2">Notifications</h2>
      <p className="text-sm text-shell-text-tertiary mb-5">
        Choose which notification types to receive. Disabled types are suppressed
        everywhere -- no in-app notification, no web push.
      </p>

      {error && (
        <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
          {error}
        </p>
      )}

      <div className="space-y-2">
        {NOTIF_TYPES.map((item) => {
          const pref = prefs.find((p) => p.event_type === item.type);
          const checked = pref ? !pref.muted : true;
          const id = `notif-${item.type}`;
          return (
            <Card key={item.type} className="p-4 flex items-center justify-between gap-3">
              <div className="flex-1 min-w-0">
                <Label htmlFor={id} className="text-sm font-medium text-shell-text">
                  {item.label}
                </Label>
                <p className="text-xs text-shell-text-tertiary mt-0.5">{item.desc}</p>
              </div>
              <Switch
                id={id}
                checked={checked}
                onCheckedChange={(v) => toggle(item.type, !v)}
                disabled={saving === item.type}
                aria-label={`${item.label} notifications`}
              />
            </Card>
          );
        })}
      </div>
    </section>
  );
}
