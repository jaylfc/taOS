import { useState, useEffect, useCallback } from "react";
import { Button, Card, Label, Switch } from "@/components/ui";

type DeliveryStyle = "banner" | "alert" | "none";

interface NotifPref {
  event_type: string;
  muted: boolean;
  style: DeliveryStyle;
  show_centre: boolean;
}

interface DndSettings {
  enabled: boolean;
  start: string;
  end: string;
  timezone?: string;
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

const STYLE_OPTIONS: { value: DeliveryStyle; label: string; hint: string }[] = [
  { value: "banner", label: "Banner", hint: "Pops up, then fades" },
  { value: "alert", label: "Alert", hint: "Pops up and stays until dismissed" },
  { value: "none", label: "None", hint: "No pop-up; Notification Centre only" },
];

function isDndSettings(data: unknown): data is DndSettings {
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  const d = data as Record<string, unknown>;
  return (
    typeof d.enabled === "boolean" &&
    typeof d.start === "string" &&
    typeof d.end === "string"
  );
}

export function NotificationsPanel() {
  const [prefs, setPrefs] = useState<NotifPref[] | null>(null);
  const [dnd, setDnd] = useState<DndSettings | null>(null);
  const [dndSaving, setDndSaving] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadPrefs = useCallback(() => {
    setError(null);
    setLoaded(false);
    fetch("/api/notifications/prefs")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && Array.isArray(data)) {
          // Older backends omit style/show_centre; normalise to the defaults
          // the backend itself applies.
          setPrefs(
            data.map((p) => ({
              event_type: p.event_type,
              muted: Boolean(p.muted),
              style: (p.style ?? "banner") as DeliveryStyle,
              show_centre: p.show_centre ?? true,
            })),
          );
          // DND rides along after prefs succeed; any failure just hides the
          // section silently -- it must never mask the prefs list or error.
          fetch("/api/notifications/dnd")
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
              if (isDndSettings(d)) setDnd(d);
            })
            .catch(() => {});
        } else setError("Could not load notification preferences.");
      })
      .catch(() => setError("Could not reach backend."))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    loadPrefs();
  }, [loadPrefs]);

  const savePref = async (
    eventType: string,
    updates: Partial<Pick<NotifPref, "muted" | "style" | "show_centre">>,
  ) => {
    setSaving(eventType);
    setError(null);
    // Capture previous state for potential revert
    let previousPrefs: NotifPref[] | null = null;
    setPrefs((prev) => {
      previousPrefs = prev ? [...prev] : null;
      return prev
        ? prev.map((p) => (p.event_type === eventType ? { ...p, ...updates } : p))
        : prev;
    });
    try {
      const res = await fetch(`/api/notifications/prefs/${encodeURIComponent(eventType)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        // Revert optimistic update on failure
        if (previousPrefs) setPrefs(previousPrefs);
        if (res.status === 403) {
          setError("Only an admin can change this");
        } else {
          setError(data.error || `Failed to save (${res.status})`);
        }
        return;
      }
    } catch {
      if (previousPrefs) setPrefs(previousPrefs);
      setError("Could not reach backend.");
    } finally {
      setSaving(null);
    }
  };

  const toggle = (eventType: string, muted: boolean) => savePref(eventType, { muted });

  const saveDnd = async (next: DndSettings) => {
    const previous = dnd;
    // DND is evaluated in the person's own timezone, so every save carries
    // the browser's current IANA zone.
    const withTz: DndSettings = {
      ...next,
      timezone:
        Intl.DateTimeFormat().resolvedOptions().timeZone ||
        next.timezone ||
        "UTC",
    };
    setDnd(withTz);
    setError(null);
    setDndSaving(true);
    try {
      const res = await fetch("/api/notifications/dnd", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(withTz),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setDnd(previous);
        if (res.status === 403) {
          setError("Only an admin can change this");
        } else {
          setError(data.error || `Failed to save (${res.status})`);
        }
      }
    } catch {
      setDnd(previous);
      setError("Could not reach backend.");
    } finally {
      setDndSaving(false);
    }
  };

  return (
    <section aria-label="Notification settings">
      <h2 className="text-lg font-semibold mb-2">Notifications</h2>

      {!loaded ? (
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      ) : (
        <>
          <p className="text-sm text-shell-text-tertiary mb-5">
            Choose which notification types to receive. Disabled types are suppressed
            everywhere -- no in-app notification, no web push.
          </p>

          {error && (
            <>
              <p role="alert" className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
                {error}
              </p>
              {!prefs && (
                <Button variant="outline" size="sm" onClick={loadPrefs}>
                  Retry
                </Button>
              )}
            </>
          )}

          {prefs && (
            <div className="space-y-2">
              {NOTIF_TYPES.map((item) => {
                const pref = prefs.find((p) => p.event_type === item.type);
                const checked = pref ? !pref.muted : true;
                const style = pref?.style ?? "banner";
                const showCentre = pref?.show_centre ?? true;
                const id = `notif-${item.type}`;
                return (
                  <Card key={item.type} className="p-4">
                    <div className="flex items-center justify-between gap-3">
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
                    </div>
                    <div
                      className={`mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 ${
                        checked ? "" : "opacity-40 pointer-events-none"
                      }`}
                    >
                      <div
                        className="flex rounded-lg border border-white/10 overflow-hidden"
                        role="group"
                        aria-label={`${item.label} delivery style`}
                      >
                        {STYLE_OPTIONS.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            aria-pressed={style === opt.value}
                            title={opt.hint}
                            disabled={saving === item.type}
                            onClick={() => savePref(item.type, { style: opt.value })}
                            className={`px-2.5 py-1 text-xs transition-colors ${
                              style === opt.value
                                ? "bg-accent text-white font-medium"
                                : "text-shell-text-secondary hover:bg-white/5"
                            }`}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <Switch
                          id={`${id}-centre`}
                          checked={showCentre}
                          onCheckedChange={(v) => savePref(item.type, { show_centre: v })}
                          disabled={saving === item.type}
                          aria-label={`${item.label} in Notification Centre`}
                        />
                        <Label
                          htmlFor={`${id}-centre`}
                          className="text-xs text-shell-text-secondary"
                        >
                          Show in Notification Centre
                        </Label>
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}

          {dnd && (
            <Card className="p-4 mt-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <Label htmlFor="notif-dnd" className="text-sm font-medium text-shell-text">
                    Do Not Disturb
                  </Label>
                  <p className="text-xs text-shell-text-tertiary mt-0.5">
                    While on, notifications are still saved to the Notification Centre,
                    but nothing pops up and no web push is sent.
                  </p>
                </div>
                <Switch
                  id="notif-dnd"
                  checked={dnd.enabled}
                  onCheckedChange={(v) => saveDnd({ ...dnd, enabled: v })}
                  disabled={dndSaving}
                  aria-label="Do Not Disturb"
                />
              </div>
              {dnd.enabled && (
                <div className="mt-3 flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <Label htmlFor="notif-dnd-start" className="text-xs text-shell-text-secondary">
                      From
                    </Label>
                    <input
                      id="notif-dnd-start"
                      type="time"
                      disabled={dndSaving}
                      value={dnd.start}
                      onChange={(e) => saveDnd({ ...dnd, start: e.target.value })}
                      className="rounded-md border border-white/10 bg-shell-bg-deep px-2 py-1 text-xs text-shell-text"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <Label htmlFor="notif-dnd-end" className="text-xs text-shell-text-secondary">
                      To
                    </Label>
                    <input
                      id="notif-dnd-end"
                      type="time"
                      disabled={dndSaving}
                      value={dnd.end}
                      onChange={(e) => saveDnd({ ...dnd, end: e.target.value })}
                      className="rounded-md border border-white/10 bg-shell-bg-deep px-2 py-1 text-xs text-shell-text"
                    />
                  </div>
                </div>
              )}
            </Card>
          )}
        </>
      )}
    </section>
  );
}
