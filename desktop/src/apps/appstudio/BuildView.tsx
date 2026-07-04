import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Sparkles, XCircle } from "lucide-react";
import { PROMPT_SEEDED_EVENT, takePendingPrompt, setBuildSession } from "./build-state";
import { generateApp } from "./generate-app";
import { analyzeAppSource, type Finding } from "./analyze-source";
import { FindingsPanel } from "./FindingsPanel";
import { installUserspaceApp, packageUserspaceApp, USERSPACE_APPS_CHANGED } from "@/lib/userspace-apps";
import { emitAppEvent } from "@/lib/app-event-bus";
import { SandboxedAppWindow } from "../SandboxedAppWindow";

/* ------------------------------------------------------------------ */
/*  BuildView -- generate -> analyze -> package -> install -> render    */
/*                                                                     */
/*  A single Build click drives the whole pipeline: generateApp()       */
/*  streams the taos-agent and parses its ### FILE: blocks the same way */
/*  Game Studio does, analyzeAppSource() runs the same server-side       */
/*  static security gate Publish/Share use (a critical finding blocks    */
/*  the rest of the pipeline), a clean result is packaged via the new    */
/*  generic /api/userspace-apps/package route, and installed through the */
/*  existing /api/userspace-apps/install endpoint tagged "ai-generated". */
/*  The preview panel then renders the REAL installed app through        */
/*  SandboxedAppWindow -- the same bundle/CSP/broker and server-side      */
/*  gate every other userspace app goes through, install-then-render.    */
/* ------------------------------------------------------------------ */

type Stage = "idle" | "generating" | "analyzing" | "blocked" | "packaging" | "installing" | "installed";

const DEFAULT_PROMPT = "a weekly chore tracker with points and a family leaderboard";

const BUSY_STAGES: readonly Stage[] = ["generating", "analyzing", "packaging", "installing"];

export function BuildView() {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [stage, setStage] = useState<Stage>("idle");
  const [logLines, setLogLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [installedAppId, setInstalledAppId] = useState<string | null>(null);
  const [appName, setAppName] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const seeded = takePendingPrompt();
    if (seeded) setPrompt(seeded);

    const onSeeded = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      if (detail) setPrompt(detail);
    };
    window.addEventListener(PROMPT_SEEDED_EVENT, onSeeded);
    return () => window.removeEventListener(PROMPT_SEEDED_EVENT, onSeeded);
  }, []);

  useEffect(() => {
    fetch("/api/taos-agent/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.model !== undefined) setModel(data.model);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logLines, error]);

  const busy = BUSY_STAGES.includes(stage);

  const handleBuild = useCallback(async () => {
    if (busy || !model) return;
    const text = prompt.trim();
    if (!text) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setError(null);
    setFindings([]);
    setInstalledAppId(null);
    setAppName(null);
    setLogLines([]);
    setStage("generating");

    const appendLog = (line: string) => {
      if (mountedRef.current) setLogLines((prev) => [...prev, line]);
    };

    try {
      const generated = await generateApp(text, (p) => appendLog(p.detail), {
        signal: controller.signal,
      });
      if (!mountedRef.current) return;
      if (generated.parseNotice) appendLog(generated.parseNotice);

      setStage("analyzing");
      appendLog("Scanning for security issues...");
      const analysis = await analyzeAppSource(generated.files);
      if (!mountedRef.current) return;
      setFindings(analysis.findings);
      if (analysis.blocked) {
        appendLog("Blocked: fix the critical security findings before this app can be installed.");
        setStage("blocked");
        return;
      }
      appendLog("No security issues found.");

      setStage("packaging");
      appendLog("Packaging your app...");
      const name = text.slice(0, 60);
      const file = await packageUserspaceApp(name, generated.files);
      if (!mountedRef.current) return;

      setStage("installing");
      appendLog("Installing...");
      const install = await installUserspaceApp(file, "ai-generated");
      if (!mountedRef.current) return;

      emitAppEvent(USERSPACE_APPS_CHANGED);
      setBuildSession({ name, files: generated.files, appId: install.app_id });
      setAppName(name);
      setInstalledAppId(install.app_id);
      appendLog("Installed. Your app is running below.");
      setStage("installed");
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        if (mountedRef.current) setStage("idle");
        return;
      }
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : String(e));
        setStage("idle");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [prompt, busy, model]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    setStage("idle");
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        void handleBuild();
      }
    },
    [handleBuild],
  );

  const noModel = !model;
  const showFindings = stage !== "idle" && stage !== "generating";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Build</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          {appName ? `${appName} · taOS app · sandboxed` : "Describe your app in plain words"}
        </span>
      </div>

      {/* build area */}
      <div className="flex min-h-0 flex-1">
        {/* sandbox panel */}
        <div
          className="flex min-w-0 flex-1 flex-col p-[22px]"
          style={{
            background:
              "repeating-conic-gradient(rgba(255,255,255,.016) 0% 25%, transparent 0% 50%) 0 0 / 22px 22px, var(--tw-color-shell-bg, transparent)",
          }}
        >
          {/* chip row */}
          <div className="mb-[14px] flex items-center gap-[9px]">
            <Chip
              icon={
                stage === "installed" ? (
                  <CheckCircle2 size={13} className="text-[#5fbf78]" />
                ) : undefined
              }
              label="Live preview"
            />
            <Chip label="Sandboxed" />
            <span className="ml-auto text-[11px] text-shell-text-tertiary">
              taOS SDK 1.0 &middot; no network
            </span>
          </div>

          {/* app window in window */}
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-shell-border-strong shadow-[0_18px_40px_-16px_rgba(0,0,0,0.6)]">
            {/* mini titlebar */}
            <div
              className="flex h-[38px] flex-none items-center gap-[9px] px-[13px]"
              style={{ background: "linear-gradient(135deg,#6f7687,#525868)", color: "#fff" }}
            >
              <div className="flex gap-1.5">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="inline-block h-[9px] w-[9px] rounded-full"
                    style={{ background: "rgba(255,255,255,0.5)" }}
                  />
                ))}
              </div>
              <span className="text-[12px] font-bold">{appName ?? "Preview"}</span>
            </div>

            {/* app body */}
            {installedAppId ? (
              <div className="min-h-0 flex-1">
                <SandboxedAppWindow
                  windowId="app-studio-build-preview"
                  appId={installedAppId}
                  appType="web"
                  provenance="ai-generated"
                  grantedCapabilities={[]}
                />
              </div>
            ) : (
              <div
                className="flex flex-1 flex-col items-center justify-center gap-2 p-[18px_20px] text-center"
                style={{ background: "#202024", color: "#aab" }}
              >
                {busy ? (
                  <>
                    <Loader2 size={22} className="animate-spin text-white/70" />
                    <p className="text-[12.5px]">{logLines[logLines.length - 1] ?? "Working..."}</p>
                  </>
                ) : stage === "blocked" ? (
                  <>
                    <XCircle size={22} className="text-red-400" />
                    <p className="text-[12.5px]">Blocked by the security scan. See findings on the right.</p>
                  </>
                ) : (
                  <p className="text-[12.5px]">
                    Describe your app below and press Build to generate, scan, install, and run it here.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        {/* build log panel */}
        <div className="flex w-[300px] flex-none flex-col border-l border-shell-border">
          <div className="flex items-center gap-2 px-[18px] pb-2 pt-4 text-[13px] font-bold">
            {busy ? (
              <Loader2 size={16} className="animate-spin text-accent" />
            ) : (
              <CheckCircle2 size={16} className="text-accent" />
            )}
            Build log
          </div>

          <div className="min-h-0 flex-1 overflow-auto px-[14px] pb-2">
            {stage === "idle" && logLines.length === 0 && !error && (
              <p className="px-1 py-2 text-[12px] text-shell-text-tertiary">
                Describe your app below and press Build to stream the pipeline's progress here.
              </p>
            )}
            {error && (
              <div
                className="mb-2 rounded-[11px] border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300"
                role="alert"
              >
                {error}
              </div>
            )}
            {logLines.length > 0 && (
              <ul className="mb-2 flex flex-col gap-1.5 rounded-[11px] bg-shell-surface p-[10px] text-[12px] leading-relaxed text-shell-text-secondary">
                {logLines.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            )}
            {showFindings && (
              <FindingsPanel loading={stage === "analyzing"} error={null} findings={findings} />
            )}
            <div ref={logEndRef} />
          </div>

          {/* model pill */}
          <div className="mt-auto border-t border-shell-border p-[13px_14px]">
            <div className="flex items-center gap-[10px] rounded-[12px] border border-shell-border bg-shell-surface p-[9px_12px]">
              <div
                className="h-6 w-6 rounded-[7px]"
                style={{ background: "linear-gradient(135deg,#7c8ba1,#aab4c9)" }}
              />
              <div>
                <div className="text-[12px] font-semibold">
                  {model ?? "No model selected"}
                </div>
                <div className="text-[10px] text-shell-text-tertiary">
                  {noModel ? "choose a model in taOS Assistant settings" : "taOS agent"}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* prompt bar */}
      <div className="flex flex-none items-center gap-3 border-t border-shell-border bg-shell-bg-deep px-[22px] py-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          disabled={busy}
          placeholder="Describe what your app should do..."
          className="flex min-h-[50px] flex-1 resize-none items-center rounded-[15px] border border-shell-border bg-shell-surface px-4 py-3 text-[13.5px] text-shell-text-secondary placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20 disabled:opacity-60"
        />
        {busy && (
          <button
            type="button"
            onClick={handleCancel}
            className="flex h-[50px] flex-none items-center gap-[9px] rounded-[15px] border border-shell-border bg-shell-surface px-6 text-[14px] font-bold text-shell-text"
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          onClick={() => void handleBuild()}
          disabled={busy || !prompt.trim() || noModel}
          className="flex h-[50px] flex-none items-center gap-[9px] rounded-[15px] border-none px-6 text-[14px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: "linear-gradient(135deg,var(--color-accent),var(--color-accent))" }}
        >
          {busy ? <Loader2 size={18} className="animate-spin" /> : <Sparkles size={18} />}
          {busy ? "Building..." : "Build"}
        </button>
      </div>
    </div>
  );
}

function Chip({ icon, label }: { icon?: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-[7px] rounded-[8px] border border-shell-border bg-shell-surface px-[11px] py-[5px] text-[11px] font-semibold text-shell-text-secondary">
      {icon}
      {label}
    </div>
  );
}
