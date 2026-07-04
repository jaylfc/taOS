import { useCallback, useEffect, useState } from "react";
import { Share2, Download, Loader2, CheckCircle2, AlertCircle, Globe2 } from "lucide-react";
import { analyzeAppSource, type Finding } from "../appstudio/analyze-source";
import { FindingsPanel } from "../appstudio/FindingsPanel";
import { installUserspaceApp, USERSPACE_APPS_CHANGED } from "@/lib/userspace-apps";
import { emitAppEvent } from "@/lib/app-event-bus";
import { fetchSitePackage, getSiteRow } from "./web-sites-api";
import type { SiteRow } from "./web-sites-api";

/* ------------------------------------------------------------------ */
/*  ShareView -- install locally or export a .taosapp package           */
/*                                                                     */
/*  Both actions build the SAME package: GET /api/web/sites/{id}/package */
/*  returns a real .taosapp zip (manifest.yaml + the site's rendered      */
/*  index.html) built by the backend's build_package() -- the exact same  */
/*  pipeline Game Studio's ShareView uses. "Install" POSTs that file to    */
/*  the existing, unmodified /api/userspace-apps/install endpoint tagged   */
/*  provenance "ai-generated"; the static security analyzer runs on         */
/*  install same as any other userspace app, and its findings are shown     */
/*  honestly here too (previewed via the same analyze endpoint App Studio   */
/*  and Game Studio use). "Export" downloads the identical package.         */
/*                                                                          */
/*  A site must be saved at least once (it needs a rendered index_html)     */
/*  before it can be shared -- there is no unsaved-site install path.       */
/* ------------------------------------------------------------------ */

export interface ShareViewProps {
  siteId: string | null;
  /** Honest origin of the current site: "ai-generated" only when the agent
   *  actually authored it, else "user-uploaded" (templates, manual edits,
   *  matched fallback, reopened site). Both tiers share a capability ceiling;
   *  this keeps the install tag truthful rather than always "ai-generated". */
  provenance: "ai-generated" | "user-uploaded";
}

export function ShareView({ siteId, provenance }: ShareViewProps) {
  const [site, setSite] = useState<SiteRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [findings, setFindings] = useState<Finding[]>([]);
  const [scanning, setScanning] = useState(true);
  const [scanError, setScanError] = useState<string | null>(null);

  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<"ok" | "consent" | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    if (!siteId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setInstallResult(null);
    setInstallError(null);
    setExportError(null);
    getSiteRow(siteId)
      .then((s) => {
        if (cancelled) return;
        setSite(s);
        if (!s.index_html) {
          setScanning(false);
          return;
        }
        setScanning(true);
        setScanError(null);
        return analyzeAppSource({ "index.html": s.index_html })
          .then((result) => {
            if (!cancelled) setFindings(result.findings);
          })
          .catch((e) => {
            if (!cancelled) setScanError(e instanceof Error ? e.message : String(e));
          })
          .finally(() => {
            if (!cancelled) setScanning(false);
          });
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [siteId]);

  const needsSave = !loading && !loadError && site !== null && !site.index_html;
  const blocked = !scanning && !scanError && findings.some((f) => f.severity === "critical");
  const actionsDisabled = loading || scanning || blocked || !site || needsSave;

  const handleInstall = useCallback(async () => {
    if (!site) return;
    setInstalling(true);
    setInstallError(null);
    setInstallResult(null);
    try {
      const file = await fetchSitePackage(site.id);
      const result = await installUserspaceApp(file, provenance);
      emitAppEvent(USERSPACE_APPS_CHANGED);
      setInstallResult(result.needs_consent ? "consent" : "ok");
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : String(e));
    } finally {
      setInstalling(false);
    }
  }, [site, provenance]);

  const handleExport = useCallback(async () => {
    if (!site) return;
    setExporting(true);
    setExportError(null);
    try {
      const file = await fetchSitePackage(site.id);
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
  }, [site]);

  if (!siteId) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 text-shell-text-tertiary">
        <Globe2 size={28} />
        <p className="text-[13px]">Save a site in the Edit view first.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-shell-text-tertiary">
        <Loader2 size={16} className="mr-2 animate-spin" />
        Loading site...
      </div>
    );
  }

  if (loadError || !site) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-red-400">
        <AlertCircle size={16} />
        {loadError ?? "Site not found"}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div
        className="flex flex-col gap-3.5 p-[22px]"
        style={{ paddingBottom: "calc(22px + env(safe-area-inset-bottom, 0px))" }}
      >
        <header>
          <h2 className="text-[17px] font-bold tracking-[-0.02em]">Share "{site.title}"</h2>
          <p className="mt-1 text-[12.5px] text-shell-text-secondary">
            Install it on this taOS or export a .taosapp package to share elsewhere.
          </p>
        </header>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="flex flex-col gap-4 rounded-2xl border border-shell-border bg-shell-surface p-4 shadow-card">
            {needsSave ? (
              <div role="status" className="flex items-start gap-2.5 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3">
                <AlertCircle size={16} className="mt-0.5 flex-none text-accent" />
                <p className="text-[12.5px] leading-relaxed text-shell-text-secondary">
                  This site hasn't been saved with rendered content yet. Save it from the Edit view, then come back
                  here to share it.
                </p>
              </div>
            ) : (
              <>
                <FindingsPanel loading={scanning} error={scanError} findings={findings} />

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={() => void handleInstall()}
                    disabled={actionsDisabled || installing}
                    title={blocked ? "Fix the critical security findings before installing" : undefined}
                    className="flex h-[44px] items-center gap-2 rounded-full bg-gradient-to-br from-accent to-accent/70 px-5 text-[13px] font-bold text-white shadow-lg shadow-accent/20 transition-all hover:-translate-y-0.5 hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {installing ? <Loader2 size={17} className="animate-spin" /> : <Share2 size={17} />}
                    {blocked ? "Blocked by security scan" : installing ? "Installing..." : "Install on this taOS"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleExport()}
                    disabled={actionsDisabled || exporting}
                    className="flex h-[44px] items-center gap-2 rounded-full border border-shell-border bg-shell-surface px-5 text-[13px] font-bold text-shell-text disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {exporting ? <Loader2 size={17} className="animate-spin" /> : <Download size={17} />}
                    Export .taosapp
                  </button>
                </div>
              </>
            )}

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
                  Installed. It requests extra permissions -- review them from Settings &rarr; Apps.
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

            <p className="text-[11px] text-shell-text-tertiary">
              Distribution to the public Store goes through review; that flow isn't part of this release. A site
              that needs a real backend (not just static HTML) is a later phase -- v1 only ships static-HTML sites.
            </p>
          </div>

          <div className="flex flex-col gap-4">
            <div className="rounded-2xl border border-shell-border bg-shell-surface p-4 shadow-card">
              <dl className="flex flex-col gap-2.5 text-[12.5px]">
                <MetaRow label="Package format" value=".taosapp (web app)" />
                <MetaRow label="Rendered size" value={`${(new Blob([site.index_html]).size / 1024).toFixed(1)} KB`} />
              </dl>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-shell-text-secondary">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
