import { useCallback, useEffect, useState } from "react";
import { Sparkles, Upload, Share2, Download, Shield, CheckCircle2, AlertCircle, Loader2, LayoutGrid } from "lucide-react";
import { analyzeAppSource, type Finding } from "./analyze-source";
import { FindingsPanel } from "./FindingsPanel";
import { BUILD_SESSION_CHANGED_EVENT, SHOW_BUILD_VIEW_EVENT, getBuildSession, type BuildSession } from "./build-state";
import { installUserspaceApp, packageUserspaceApp, USERSPACE_APPS_CHANGED } from "@/lib/userspace-apps";
import { emitAppEvent } from "@/lib/app-event-bus";

/* ------------------------------------------------------------------ */
/*  PublishView -- review, security scan, and install/export the app    */
/*  most recently built in the Build view.                             */
/*                                                                     */
/*  "Publish to my Store" and "Share with family" both install the      */
/*  same real package through the existing, unmodified                */
/*  /api/userspace-apps/install endpoint (there is no separate public   */
/*  Store submission or family-sharing backend yet -- both actions are  */
/*  "install this app here", same as Game/Web Studio's Share flow).     */
/*  "Export package" downloads the identical .taosapp. All three stay   */
/*  behind the same critical-findings security gate.                   */
/* ------------------------------------------------------------------ */

export function PublishView() {
  const [session, setSession] = useState<BuildSession | null>(() => getBuildSession());
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scanning, setScanning] = useState(true);
  const [scanError, setScanError] = useState<string | null>(null);

  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<"ok" | "consent" | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    const onSessionChanged = () => setSession(getBuildSession());
    window.addEventListener(BUILD_SESSION_CHANGED_EVENT, onSessionChanged);
    return () => window.removeEventListener(BUILD_SESSION_CHANGED_EVENT, onSessionChanged);
  }, []);

  useEffect(() => {
    if (!session) {
      setScanning(false);
      return;
    }
    let cancelled = false;
    setScanning(true);
    setScanError(null);
    setInstallResult(null);
    setInstallError(null);
    setExportError(null);
    analyzeAppSource(session.files)
      .then((result) => {
        if (cancelled) return;
        setFindings(result.findings);
      })
      .catch((e) => {
        if (cancelled) return;
        setScanError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => {
        if (!cancelled) setScanning(false);
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  const blocked = !scanning && !scanError && findings.some((f) => f.severity === "critical");
  const actionsDisabled = !session || scanning || blocked;

  const handleInstall = useCallback(async () => {
    if (!session) return;
    setInstalling(true);
    setInstallError(null);
    setInstallResult(null);
    try {
      const file = await packageUserspaceApp(session.name, session.files);
      const result = await installUserspaceApp(file, "ai-generated");
      emitAppEvent(USERSPACE_APPS_CHANGED);
      setInstallResult(result.needs_consent ? "consent" : "ok");
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : String(e));
    } finally {
      setInstalling(false);
    }
  }, [session]);

  const handleExport = useCallback(async () => {
    if (!session) return;
    setExporting(true);
    setExportError(null);
    try {
      const file = await packageUserspaceApp(session.name, session.files);
      const url = URL.createObjectURL(file);
      const a = document.createElement("a");
      a.href = url;
      a.download = file.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }, [session]);

  if (!session) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-shell-text-tertiary">
        <LayoutGrid size={28} />
        <p className="text-[13px]">Generate an app in the Build view first.</p>
        <button
          type="button"
          onClick={() => window.dispatchEvent(new CustomEvent(SHOW_BUILD_VIEW_EVENT))}
          className="rounded-[11px] border border-shell-border bg-shell-surface px-4 py-2 text-[12.5px] font-semibold text-shell-text"
        >
          Go to Build
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Publish</h2>
        <span className="text-[12px] text-shell-text-tertiary">Review, scan, share</span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* main */}
        <div className="min-w-0 flex-1 overflow-auto p-6">
          <div className="mx-auto max-w-[560px]">
            {/* app identity */}
            <div className="mb-[22px] flex items-center gap-[15px]">
              <div
                className="flex h-[62px] w-[62px] flex-none items-center justify-center rounded-[16px] text-white shadow-[0_8px_22px_rgba(0,0,0,0.35)]"
                style={{ background: "linear-gradient(135deg,#6f7687,#474d5e)" }}
              >
                <LayoutGrid size={30} />
              </div>
              <div>
                <div className="flex items-center gap-[8px]">
                  <span className="text-[19px] font-extrabold tracking-[-0.02em]">{session.name}</span>
                  <span
                    data-testid="provenance-badge"
                    data-provenance="ai-generated"
                    className="flex items-center gap-1 rounded-full border border-shell-border bg-shell-surface px-[8px] py-[2px] text-[10px] font-semibold text-shell-text-secondary"
                  >
                    <Sparkles size={11} />
                    AI-generated
                  </span>
                </div>
                <div className="mt-[3px] text-[12.5px] text-shell-text-secondary">
                  Built in App Studio &middot; {Object.keys(session.files).length} file
                  {Object.keys(session.files).length === 1 ? "" : "s"}
                </div>
              </div>
            </div>

            {/* security scan panel */}
            <FindingsPanel loading={scanning} error={scanError} findings={findings} />

            {/* safety note */}
            <div
              className="mt-2 flex gap-[10px] rounded-[13px] border p-[13px_15px]"
              style={{
                background: "rgba(95,191,120,0.08)",
                borderColor: "rgba(95,191,120,0.25)",
              }}
            >
              <Shield size={17} className="mt-[1px] flex-none" style={{ color: "#5fbf78" }} />
              <p className="text-[12px] leading-relaxed text-shell-text-secondary">
                Runs sandboxed with no network access. It can only touch what you grant, and you can
                change that any time.
              </p>
            </div>
          </div>
        </div>

        {/* side panel */}
        <div className="flex w-[280px] flex-none flex-col gap-[13px] border-l border-shell-border bg-shell-bg-deep p-[22px_20px]">
          {/* preview tile */}
          <div
            className="flex aspect-[16/10] items-center justify-center overflow-hidden rounded-[13px] border border-shell-border p-3 text-center font-bold text-white"
            style={{ background: "linear-gradient(140deg,#2c3142,#171a24)" }}
          >
            {session.name}
          </div>

          <button
            type="button"
            onClick={() => void handleInstall()}
            disabled={actionsDisabled || installing}
            aria-disabled={actionsDisabled || installing}
            title={blocked ? "Fix the critical security findings before publishing" : undefined}
            className="flex h-[46px] items-center justify-center gap-[9px] rounded-[13px] text-[13.5px] font-bold text-white shadow-[0_8px_22px_-8px_rgba(139,146,163,0.35)] disabled:cursor-not-allowed disabled:opacity-50"
            style={{ background: "linear-gradient(135deg,var(--color-accent),var(--color-accent))" }}
          >
            {installing ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
            {blocked ? "Blocked by security scan" : installing ? "Publishing..." : "Publish to my Store"}
          </button>

          <button
            type="button"
            onClick={() => void handleInstall()}
            disabled={actionsDisabled || installing}
            className="flex h-[46px] items-center justify-center gap-[9px] rounded-[13px] border border-shell-border bg-shell-surface text-[13.5px] font-bold text-shell-text disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Share2 size={16} />
            Share with family
          </button>

          <button
            type="button"
            onClick={() => void handleExport()}
            disabled={actionsDisabled || exporting}
            className="flex h-[46px] items-center justify-center gap-[9px] rounded-[13px] border border-shell-border bg-shell-surface text-[13.5px] font-bold text-shell-text disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exporting ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            Export package
          </button>

          {installResult === "ok" && (
            <div role="status" className="flex items-start gap-2.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-3">
              <CheckCircle2 size={16} className="mt-0.5 flex-none text-emerald-400" />
              <p className="text-[12.5px] leading-relaxed text-emerald-200">
                Installed. Find it in Launchpad, sandboxed like any other app.
              </p>
            </div>
          )}
          {installResult === "consent" && (
            <div role="status" className="flex items-start gap-2.5 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3">
              <CheckCircle2 size={16} className="mt-0.5 flex-none text-accent" />
              <p className="text-[12.5px] leading-relaxed text-shell-text-secondary">
                Installed. It requests extra permissions &rarr; review them from Settings &rarr; Apps.
              </p>
            </div>
          )}
          {installError && (
            <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3">
              <AlertCircle size={16} className="mt-0.5 flex-none text-red-400" />
              <p className="text-[12.5px] leading-relaxed text-red-200">{installError}</p>
            </div>
          )}
          {exportError && (
            <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3">
              <AlertCircle size={16} className="mt-0.5 flex-none text-red-400" />
              <p className="text-[12.5px] leading-relaxed text-red-200">{exportError}</p>
            </div>
          )}

          <p className="text-center text-[11px] leading-relaxed text-shell-text-tertiary">
            Community submissions are reviewed before they appear in the public Store.
          </p>
        </div>
      </div>
    </div>
  );
}
