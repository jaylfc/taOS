import { useState, useEffect, useCallback, useRef } from "react";
import { Settings, RefreshCw, AlertCircle, Check, ChevronDown, ChevronRight } from "lucide-react";
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
  update_ping_enabled?: boolean;
}

interface UpdateStatus {
  current_sha: string;
  pending_restart_sha: string | null;
  auto_check: boolean;
}

interface BranchInfo {
  branches: string[];
  current: string;
}

export function UpdatesPanel() {
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<AutoUpdatePrefs>({ check_enabled: true });
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [showRestartModal, setShowRestartModal] = useState(false);

  // Advanced / branch selector state
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [branchInfo, setBranchInfo] = useState<BranchInfo | null>(null);
  const [selectedBranch, setSelectedBranch] = useState<string>("");
  const [switching, setSwitching] = useState(false);
  const [showSwitchConfirm, setShowSwitchConfirm] = useState(false);
  const branchFetched = useRef(false);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  // Focus management for the confirm dialog: move focus into the dialog when it
  // opens and return it to the previously focused control when it closes, so
  // keyboard/screen-reader users get and keep the confirmation context.
  useEffect(() => {
    if (!showSwitchConfirm) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    confirmBtnRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, [showSwitchConfirm]);

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
              update_ping_enabled: data.update_ping_enabled ?? true,
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
    const prev = prefs;
    setPrefs(next);
    try {
      const res = await fetch("/api/preferences/auto-update", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      if (!res.ok) { setPrefs(prev); }   // revert on server error
    } catch {
      setPrefs(prev);                    // revert on network failure
    }
  }, [prefs]);

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
    try {
      const res = await fetch("/api/system/restart/prepare", { method: "POST" });
      if (res.ok) {
        setShowRestartModal(true);
      } else {
        setStatus("Could not start restart.");
      }
    } catch {
      setStatus("Could not reach the restart endpoint.");
    }
  };

  // Fetch branches once when Advanced is first opened. Only mark as fetched on
  // success so a transient 500/network error can be retried by re-opening
  // Advanced, rather than wedging the panel until a full page refresh.
  useEffect(() => {
    if (!advancedOpen || branchFetched.current) return;
    (async () => {
      try {
        const r = await fetch("/api/settings/branches");
        if (r.ok) {
          const data: BranchInfo = await r.json();
          setBranchInfo(data);
          setSelectedBranch(data.current);
          branchFetched.current = true;
        } else {
          setStatus("Could not load branch list.");
        }
      } catch {
        setStatus("Could not reach branch endpoint.");
      }
    })();
  }, [advancedOpen]);

  const confirmSwitchBranch = async () => {
    setShowSwitchConfirm(false);
    setSwitching(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/update-channel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch: selectedBranch }),
      });
      const data = await res.json().catch(() => ({})) as { status?: string; branch?: string; snapshot?: string; recovery_tag?: string; message?: string; error?: string };
      if (res.ok && !data.error) {
        setStatus(data.message ?? data.snapshot ?? `Switching to ${data.branch ?? selectedBranch}…`);
        setShowRestartModal(true);
      } else {
        setStatus(data.error ?? "Branch switch failed.");
      }
    } catch {
      setStatus("Could not reach the update-channel endpoint.");
    }
    setSwitching(false);
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

          <div className="flex items-center justify-between gap-3">
            <div className="flex-1 min-w-0">
              <Label className="text-sm">Anonymous install ping</Label>
              <p className="text-[11px] text-shell-text-tertiary mt-0.5">
                Reports an anonymous install count with the update check. No personal data, no identifiers beyond a random id. Updates work either way.
              </p>
            </div>
            <Switch
              checked={prefs.update_ping_enabled ?? true}
              onCheckedChange={(v) => savePrefs({ ...prefs, update_ping_enabled: v })}
              aria-label="Anonymous install ping"
            />
          </div>

        </div>

        {/* Advanced disclosure */}
        <div className="border-t border-white/5 pt-4">
          <button
            type="button"
            aria-label="Advanced"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-shell-text-tertiary hover:text-shell-text-secondary transition-colors"
          >
            {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Advanced
          </button>

          {advancedOpen && (
            <div className="mt-3 space-y-3">
              <div className="flex items-end gap-2 flex-wrap">
                <div className="flex flex-col gap-1">
                  <label htmlFor="branch-select" className="text-[11px] text-shell-text-tertiary">
                    Branch
                  </label>
                  <select
                    id="branch-select"
                    aria-label="Branch"
                    value={selectedBranch}
                    onChange={(e) => setSelectedBranch(e.target.value)}
                    className="text-sm bg-white/5 border border-white/10 rounded px-2 py-1 text-shell-text-primary focus:outline-none focus:ring-1 focus:ring-sky-500"
                  >
                    {branchInfo?.branches.map((b) => (
                      <option key={b} value={b}>{b}</option>
                    ))}
                  </select>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={switching || !selectedBranch || selectedBranch === branchInfo?.current}
                  onClick={() => setShowSwitchConfirm(true)}
                  aria-label="Switch branch"
                >
                  {switching ? "Switching…" : "Switch branch"}
                </Button>
              </div>
              {branchInfo && (
                <p className="text-[11px] text-shell-text-tertiary">
                  Current branch: <span className="font-mono">{branchInfo.current}</span>
                </p>
              )}
            </div>
          )}
        </div>
      </Card>

      {/* Branch-switch confirm dialog */}
      {showSwitchConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setShowSwitchConfirm(false)}
          onKeyDown={(e) => { if (e.key === "Escape") setShowSwitchConfirm(false); }}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-labelledby="switch-branch-title"
          aria-describedby="switch-branch-desc"
        >
          <div
            className="bg-shell-surface border border-white/10 rounded-xl shadow-2xl p-6 max-w-md w-full mx-4 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="switch-branch-title" className="text-sm font-semibold">Switch branch?</h3>
            <p id="switch-branch-desc" className="text-xs text-shell-text-secondary leading-relaxed">
              This switches taOS to <span className="font-mono font-semibold">{selectedBranch}</span> and restarts.
              Your data/ is backed up first (data-backups/).
              Switching to an older branch may leave data written by a newer version unreadable.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <Button size="sm" variant="outline" onClick={() => setShowSwitchConfirm(false)}>
                Cancel
              </Button>
              <Button ref={confirmBtnRef} size="sm" onClick={confirmSwitchBranch} aria-label="Confirm">
                Confirm
              </Button>
            </div>
          </div>
        </div>
      )}

      {showRestartModal && (
        <RestartProgressModal onClose={() => setShowRestartModal(false)} />
      )}
    </section>
  );
}

// Alias for backward compatibility with existing imports
export { UpdatesPanel as UpdatesSection };
