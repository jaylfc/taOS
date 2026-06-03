import { useState, useEffect, useCallback } from "react";
import { Settings, RefreshCw, AlertCircle, Check } from "lucide-react";
import { Button, Card, Label, Switch } from "@/components/ui";
import { RestartProgressModal } from "@/apps/SettingsApp/_shared";

interface UpdateInfo {
  has_updates: boolean;
  current_version: string;
  current_commit: string;
  new_commit?: string | null;
}

interface AutoUpdatePrefs {
  check_enabled?: boolean;
}

interface UpdateStatus {
  current_sha: string;
  pending_restart_sha: string | null;
  auto_check: boolean;
}

export function UpdatesSection() {
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<AutoUpdatePrefs>({ check_enabled: true });
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [showRestartModal, setShowRestartModal] = useState(false);

  // Load current prefs + info on mount
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/preferences/auto-update");
        if (r.ok) {
          const data = await r.json();
          if (data && typeof data === "object") {
            setPrefs({
              check_enabled: data.check_enabled ?? true,
            });
          }
        }
      } catch { /* ignore */ }
      try {
        const r2 = await fetch("/api/settings/update-check");
        if (r2.ok) setInfo(await r2.json());
      } catch { /* ignore */ }
      try {
        const r3 = await fetch("/api/settings/update-status");
        if (r3.ok) setUpdateStatus(await r3.json());
      } catch { /* ignore */ }
    })();
  }, []);

  const savePrefs = useCallback(async (next: AutoUpdatePrefs) => {
    setPrefs(next);
    try {
      await fetch("/api/preferences/auto-update", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
    } catch { /* ignore network */ }
  }, []);

  const checkUpdates = async () => {
    setChecking(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/update-check");
      if (res.ok) {
        const data = (await res.json()) as UpdateInfo;
        setInfo(data);
        setStatus(data.has_updates ? "A new version is available." : "You are up to date.");
      } else {
        setStatus("Update check not available.");
      }
    } catch {
      setStatus("Could not reach update server.");
    }
    setChecking(false);
  };

  const applyUpdate = async () => {
    setApplying(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/update", { method: "POST" });
      if (res.ok) {
        // Server always restarts after a successful install — show the modal.
        setShowRestartModal(true);
      } else {
        const err = await res.json().catch(() => ({}));
        setStatus((err as { error?: string }).error ?? "Update failed.");
      }
    } catch {
      setStatus("Could not apply update.");
    }
    setApplying(false);
  };

  const rebuildFrontend = async () => {
    setRebuilding(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/rebuild-frontend", { method: "POST" });
      const data = (await res.json().catch(() => ({}))) as { message?: string; error?: string };
      if (res.ok) {
        setStatus(data.message ?? "Frontend rebuilt — hard-refresh the browser to see changes.");
      } else {
        setStatus(data.error ?? "Frontend rebuild failed.");
      }
    } catch {
      setStatus("Could not reach rebuild endpoint.");
    }
    setRebuilding(false);
  };

  const triggerRestart = async () => {
    setShowRestartModal(true);
    try {
      await fetch("/api/system/restart/prepare", { method: "POST" });
    } catch { /* ignore — modal polls status */ }
  };

  const hasPendingRestart = !!updateStatus?.pending_restart_sha;

  return (
    <section aria-label="System updates">
      <h2 className="text-lg font-semibold mb-5">Updates</h2>

      {hasPendingRestart && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <div className="flex items-center gap-2 text-sm text-amber-200">
            <AlertCircle size={16} className="shrink-0" />
            <span>Update pulled — restart to finish applying ({updateStatus!.pending_restart_sha!.slice(0, 7)})</span>
          </div>
          <Button size="sm" onClick={triggerRestart} aria-label="Restart server to apply update">
            Restart now
          </Button>
        </div>
      )}

      <Card className="p-4 space-y-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-white/5 text-sky-400">
            <Settings size={20} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">taOS</p>
            {info?.has_updates && info.new_commit ? (
              <div className="flex flex-col gap-0.5">
                <p className="text-xs text-shell-text-tertiary tabular-nums">
                  <span className="text-white/40">installed </span>{info.current_commit}
                </p>
                <p className="text-xs text-amber-300/90 tabular-nums">
                  <span className="text-amber-300/50">available </span>{info.new_commit}
                </p>
              </div>
            ) : (
              <p className="text-xs text-shell-text-tertiary tabular-nums">
                {info?.current_commit ?? "v0.1.0-dev"}
              </p>
            )}
          </div>
          {info?.has_updates && (
            <span className="text-[10px] px-2 py-1 rounded-full font-semibold bg-amber-500/20 text-amber-300">
              Update available
            </span>
          )}
        </div>

        <div className="flex gap-2 flex-wrap">
          <Button variant="outline" size="sm" onClick={checkUpdates} disabled={checking}>
            <RefreshCw size={14} className={checking ? "animate-spin" : ""} />
            {checking ? "Checking..." : "Check Now"}
          </Button>
          {info?.has_updates ? (
            <Button size="sm" onClick={applyUpdate} disabled={applying}>
              <RefreshCw size={14} className={applying ? "animate-spin" : ""} />
              {applying ? "Installing..." : "Install Update"}
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={rebuildFrontend}
            disabled={rebuilding}
            aria-label="Force rebuild of the desktop frontend bundle"
            title="Rebuild the desktop bundle from source. Use after a source-only update or when the UI looks stale."
          >
            <RefreshCw size={14} className={rebuilding ? "animate-spin" : ""} />
            {rebuilding ? "Rebuilding..." : "Rebuild Frontend"}
          </Button>
        </div>

        {status && (
          <div className="flex items-start gap-2 text-xs">
            {status.includes("up to date") || status.includes("applied") ? (
              <Check size={14} className="text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <AlertCircle size={14} className="text-amber-400 shrink-0 mt-0.5" />
            )}
            <span className="text-shell-text-secondary">{status}</span>
          </div>
        )}

        <div className="border-t border-white/5 pt-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex-1 min-w-0">
              <Label className="text-sm">Check for updates automatically</Label>
              <p className="text-[11px] text-shell-text-tertiary mt-0.5">
                Polls GitHub hourly and notifies when a new version is available.
              </p>
            </div>
            <Switch
              checked={prefs.check_enabled ?? true}
              onCheckedChange={(v) => savePrefs({ ...prefs, check_enabled: v })}
            />
          </div>

        </div>
      </Card>

      {showRestartModal && (
        <RestartProgressModal onClose={() => setShowRestartModal(false)} />
      )}
    </section>
  );
}
