import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkles, Info, Loader2, Play } from "lucide-react";
import { createFromTemplate, generateGame, type GenerateProgress } from "./generate-game";
import { TEMPLATES } from "./templates";
import type { Template } from "./types";

/* ------------------------------------------------------------------ */
/*  CreateView -- prompt box + real AI generation + template gallery   */
/*                                                                     */
/*  "Generate with AI" streams the taos-agent (same pattern as App      */
/*  Studio's Build view) to customize a matched starter template, then  */
/*  saves the result and opens the Editor. "Use template" saves the     */
/*  seed as-is with no AI step. Real progress states from the actual    */
/*  stream -- no setTimeout theater.                                    */
/* ------------------------------------------------------------------ */

export interface CreateViewProps {
  /** Called with the new game's id once it has been saved. */
  onOpenGame: (gameId: string) => void;
}

export function CreateView({ onOpenGame }: CreateViewProps) {
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<GenerateProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
    fetch("/api/taos-agent/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.model !== undefined) setModel(data.model);
      })
      .catch(() => {});
  }, []);

  const handleGenerate = useCallback(async () => {
    if (busy) return;
    const text = prompt.trim();
    if (!text) {
      setError("Describe your game idea first, or pick a template below.");
      return;
    }
    if (!model) {
      setError("No taOS agent model is configured -- pick one in taOS Assistant settings first.");
      return;
    }

    setError(null);
    setNotice(null);
    setBusy(true);
    setProgress({ stage: "seed", detail: "Starting..." });

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const result = await generateGame(text, null, (p) => {
        if (mountedRef.current) setProgress(p);
      }, { signal: controller.signal });
      if (result.parseNotice && mountedRef.current) setNotice(result.parseNotice);
      onOpenGame(result.game.id);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError") && mountedRef.current) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (mountedRef.current) {
        setBusy(false);
        setProgress(null);
      }
    }
  }, [busy, prompt, model, onOpenGame]);

  const handleUseTemplate = useCallback(
    async (t: Template) => {
      if (busy) return;
      setError(null);
      setBusy(true);
      setProgress({ stage: "saving", detail: `Saving the ${t.title} template...` });
      try {
        const game = await createFromTemplate(t);
        onOpenGame(game.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
        setProgress(null);
      }
    },
    [busy, onOpenGame],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div
        className="flex flex-col gap-[18px] p-[22px]"
        style={{ paddingBottom: "calc(22px + env(safe-area-inset-bottom, 0px))" }}
      >
        <header>
          <h2 className="text-[17px] font-bold tracking-[-0.02em]">
            Describe the game you want to make
          </h2>
          <p className="mt-1 text-[12.5px] text-shell-text-secondary">
            The taOS agent customizes a real starter template to match your idea, or start from a
            template with no changes.
          </p>
        </header>

        {/* prompt card */}
        <section className="rounded-2xl border border-shell-border bg-shell-surface p-4 shadow-card">
          <label htmlFor="gs-prompt" className="sr-only">
            Game idea
          </label>
          <textarea
            id="gs-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            disabled={busy}
            placeholder="A neon endless runner where a fox dodges traffic across rooftops at night, with a double-jump and a coin combo meter."
            className="w-full resize-none rounded-xl border border-shell-border bg-shell-bg-deep px-3.5 py-3 text-[13px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20 disabled:opacity-60"
          />

          <div className="mt-3.5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={busy}
              className="flex h-[44px] items-center gap-2 rounded-full bg-gradient-to-br from-accent to-accent/70 px-5 text-[13px] font-bold text-white shadow-lg shadow-accent/20 transition-all hover:-translate-y-0.5 hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {busy ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
              {busy ? "Building..." : "Generate with AI"}
            </button>
            <span className="text-[11px] text-shell-text-tertiary">
              {model ? `Using ${model}` : "No taOS agent model configured"}
            </span>
          </div>

          {error && (
            <div
              role="alert"
              className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3 text-[12.5px] text-red-200"
            >
              {error}
            </div>
          )}

          {progress && (
            <div
              role="status"
              className="mt-3 flex items-start gap-2.5 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3"
            >
              <Loader2 size={16} className="mt-0.5 flex-none animate-spin text-accent" />
              <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-shell-text-secondary">
                <span className="font-semibold text-shell-text">Building your game.</span>{" "}
                {progress.detail}
              </div>
            </div>
          )}

          {notice && (
            <div
              role="status"
              className="mt-3 flex items-start gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3.5 py-3"
            >
              <Info size={16} className="mt-0.5 flex-none text-amber-400" />
              <p className="text-[12.5px] leading-relaxed text-amber-200">{notice}</p>
            </div>
          )}
        </section>

        {/* template gallery */}
        <section>
          <h2 className="text-[16px] font-bold tracking-[-0.01em]">Start from a template</h2>
          <p className="mb-3.5 mt-1 text-[12.5px] text-shell-text-secondary">
            Each template is a real, playable starter you can open and edit right away.
          </p>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
            {TEMPLATES.map((t) => (
              <TemplateCard key={t.id} template={t} disabled={busy} onUse={() => handleUseTemplate(t)} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function TemplateCard({
  template,
  disabled,
  onUse,
}: {
  template: Template;
  disabled: boolean;
  onUse: () => void;
}) {
  return (
    <div className="flex flex-col overflow-hidden rounded-2xl border border-shell-border bg-shell-surface shadow-card transition-all hover:-translate-y-0.5 hover:border-shell-border-strong hover:shadow-card-hover">
      <div className="relative h-[104px]" style={{ background: template.cover }}>
        <span className="absolute left-2.5 top-2.5 rounded-full bg-black/40 px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-white/85 backdrop-blur-sm">
          {template.genre}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-3.5">
        <div className="text-[13.5px] font-bold">{template.title}</div>
        <p className="flex-1 text-[11.5px] leading-relaxed text-shell-text-secondary">
          {template.desc}
        </p>
        <div className="mt-1 flex items-center justify-between">
          <span className="text-[11px] text-shell-text-tertiary">Starter template</span>
          <button
            type="button"
            onClick={onUse}
            disabled={disabled}
            aria-label={`Use ${template.title} template`}
            className="flex items-center gap-1.5 rounded-full border border-shell-border bg-shell-surface-active px-3 py-1.5 text-[11.5px] font-bold text-shell-text transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Play size={12} />
            Use
          </button>
        </div>
      </div>
    </div>
  );
}
