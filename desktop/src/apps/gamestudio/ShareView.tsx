import { useCallback, useEffect, useState } from "react";
import { Share2, Download, Loader2, CheckCircle2, AlertCircle, Gamepad2 } from "lucide-react";
import { analyzeAppSource, type Finding } from "../appstudio/analyze-source";
import { FindingsPanel } from "../appstudio/FindingsPanel";
import { installUserspaceApp, USERSPACE_APPS_CHANGED } from "@/lib/userspace-apps";
import { emitAppEvent } from "@/lib/app-event-bus";
import { fetchGamePackage, getGame } from "./games-api";
import { findTemplate } from "./templates";
import type { GameRecord } from "./types";

/* ------------------------------------------------------------------ */
/*  ShareView -- install locally or export a .taosapp package           */
/*                                                                     */
/*  Both actions build the SAME package: GET /api/games/{id}/package    */
/*  returns a real .taosapp zip (manifest.yaml + the game's files) built */
/*  by the backend's build_package() (the inverse of the existing        */
/*  userspace package extractor). "Install" POSTs that exact file to     */
/*  the existing, unmodified /api/userspace-apps/install endpoint --     */
/*  the same pipeline ImportAppButton uses -- tagged provenance          */
/*  "ai-generated"; the static security analyzer runs on install same    */
/*  as any other userspace app, and its findings are shown honestly      */
/*  here too (previewed via the same analyze endpoint App Studio uses).  */
/*  "Export" downloads the identical package as a file. No public-store  */
/*  submission in v1 -- distribution goes through the maintainer flow.   */
/* ------------------------------------------------------------------ */

export interface ShareViewProps {
  gameId: string | null;
}

export function ShareView({ gameId }: ShareViewProps) {
  const [game, setGame] = useState<GameRecord | null>(null);
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
    if (!gameId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setInstallResult(null);
    setInstallError(null);
    setExportError(null);
    getGame(gameId)
      .then((g) => {
        if (cancelled) return;
        setGame(g);
        setScanning(true);
        setScanError(null);
        return analyzeAppSource(g.files)
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
  }, [gameId]);

  const blocked = !scanning && !scanError && findings.some((f) => f.severity === "critical");
  const actionsDisabled = loading || scanning || blocked || !game;

  const handleInstall = useCallback(async () => {
    if (!game) return;
    setInstalling(true);
    setInstallError(null);
    setInstallResult(null);
    try {
      const file = await fetchGamePackage(game.id);
      const result = await installUserspaceApp(file, "ai-generated");
      emitAppEvent(USERSPACE_APPS_CHANGED);
      setInstallResult(result.needs_consent ? "consent" : "ok");
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : String(e));
    } finally {
      setInstalling(false);
    }
  }, [game]);

  const handleExport = useCallback(async () => {
    if (!game) return;
    setExporting(true);
    setExportError(null);
    try {
      const file = await fetchGamePackage(game.id);
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
  }, [game]);

  if (!gameId) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 text-shell-text-tertiary">
        <Gamepad2 size={28} />
        <p className="text-[13px]">Open or create a game first.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-shell-text-tertiary">
        <Loader2 size={16} className="mr-2 animate-spin" />
        Loading game...
      </div>
    );
  }

  if (loadError || !game) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-red-400">
        <AlertCircle size={16} />
        {loadError ?? "Game not found"}
      </div>
    );
  }

  const template = findTemplate(game.template);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div
        className="flex flex-col gap-3.5 p-[22px]"
        style={{ paddingBottom: "calc(22px + env(safe-area-inset-bottom, 0px))" }}
      >
        <header>
          <h2 className="text-[17px] font-bold tracking-[-0.02em]">Share "{game.name}"</h2>
          <p className="mt-1 text-[12.5px] text-shell-text-secondary">
            Install it on this taOS or export a .taosapp package to share elsewhere.
          </p>
        </header>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="flex flex-col gap-4 rounded-2xl border border-shell-border bg-shell-surface p-4 shadow-card">
            <div
              className="relative h-[150px] overflow-hidden rounded-2xl border border-shell-border-strong"
              style={{ background: template?.cover ?? "linear-gradient(140deg,#2c3142,#171a24)" }}
            >
              <span
                className="absolute bottom-2.5 left-3 text-[17px] font-extrabold text-white"
                style={{ textShadow: "0 2px 10px rgba(0,0,0,0.6)" }}
              >
                {game.name}
              </span>
            </div>

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
              Distribution to the public Store goes through review; that flow isn't part of this
              release.
            </p>
          </div>

          <div className="flex flex-col gap-4">
            <div className="rounded-2xl border border-shell-border bg-shell-surface p-4 shadow-card">
              <dl className="flex flex-col gap-2.5 text-[12.5px]">
                <MetaRow label="Template" value={template?.title ?? (game.template || "Custom")} />
                <MetaRow label="Files" value={String(Object.keys(game.files).length)} />
                <MetaRow label="Package format" value=".taosapp (web app)" />
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
