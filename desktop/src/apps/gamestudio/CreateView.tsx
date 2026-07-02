import { useCallback, useState } from "react";
import { Play, Sparkles, Info, Loader2 } from "lucide-react";
import { runCreateGame } from "./create-game";
import { seedCreatedGame } from "./game-state";
import { TEMPLATES } from "./templates";
import {
  GENRES,
  OFFLINE_MODELS,
  ART_STYLES,
  DIFFICULTIES,
  type BuildStep,
  type Template,
} from "./types";

/* ------------------------------------------------------------------ */
/*  CreateView -- prompt box + genre/template chips + template gallery */
/*                                                                     */
/*  "Generate with AI" matches the prompt to a real starter template,  */
/*  runs a short local build trace, and hands off to Play. Full offline */
/*  model generation is still a later phase; templates always load a   */
/*  live three.js scene. Chess prompts optionally consult games.py.    */
/* ------------------------------------------------------------------ */

export interface CreateViewProps {
  windowId: string;
  prompt: string;
  onPromptChange: (v: string) => void;
  genres: Set<string>;
  onToggleGenre: (g: string) => void;
  model: string;
  onModelChange: (m: string) => void;
  /** Loads the template's real demo scene into the Play view. */
  onUseTemplate: (t: Template) => void;
}

export function CreateView(props: CreateViewProps) {
  const {
    windowId,
    prompt,
    onPromptChange,
    genres,
    onToggleGenre,
    model,
    onModelChange,
    onUseTemplate,
  } = props;
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [liveSteps, setLiveSteps] = useState<BuildStep[] | null>(null);

  const handleGenerate = useCallback(async () => {
    if (creating) return;
    const text = prompt.trim();
    if (!text) {
      setCreateError("Describe your game idea first, or pick a template below.");
      return;
    }

    setCreateError(null);
    setCreating(true);
    setLiveSteps(null);

    try {
      const { template, steps } = await runCreateGame(text, genres, setLiveSteps);
      seedCreatedGame(windowId, template, text, steps);
      onUseTemplate(template);
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }, [creating, prompt, genres, onUseTemplate, windowId]);

  const handleUseTemplate = useCallback(
    (t: Template) => {
      seedCreatedGame(windowId, t, prompt, [
        {
          who: "Director",
          what: `Loaded the ${t.title} starter template.`,
          tag: "routing",
          state: "done",
          director: true,
        },
        {
          who: "Graphics",
          what: `Mounted the ${t.scene} three.js scene in Play & Test.`,
          tag: "graphics",
          state: "done",
        },
      ]);
      onUseTemplate(t);
    },
    [windowId, prompt, onUseTemplate],
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
            Type an idea or start from a template. A template loads a live 3D preview you can play
            right away.
          </p>
        </header>

        {/* prompt + controls card */}
        <section className="rounded-2xl border border-shell-border bg-shell-surface p-4 shadow-card">
          <label htmlFor="gs-prompt" className="sr-only">
            Game idea
          </label>
          <textarea
            id="gs-prompt"
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            rows={3}
            placeholder="A neon endless runner where a fox dodges traffic across rooftops at night, with a double-jump and a coin combo meter."
            className="w-full resize-none rounded-xl border border-shell-border bg-shell-bg-deep px-3.5 py-3 text-[13px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20"
          />

          {/* genre chips (multi-select, illustrative) */}
          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Genre">
            {GENRES.map((g) => {
              const on = genres.has(g);
              return (
                <button
                  key={g}
                  type="button"
                  aria-pressed={on}
                  onClick={() => onToggleGenre(g)}
                  className={`rounded-full border px-3 py-1.5 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    on
                      ? "border-accent/40 bg-accent-soft text-shell-text"
                      : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-white/10 hover:text-shell-text"
                  }`}
                >
                  {g}
                </button>
              );
            })}
          </div>

          {/* model / style / difficulty — labelled, mostly inert in phase 1 */}
          <div className="mt-3.5 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
            <Field label="Model (offline)">
              <select
                value={model}
                onChange={(e) => onModelChange(e.target.value)}
                className="gs-select w-full rounded-xl border border-shell-border bg-shell-bg-deep px-3 py-2.5 text-[13px] text-shell-text focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20"
              >
                {OFFLINE_MODELS.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Art style">
              <select
                disabled
                className="gs-select w-full cursor-not-allowed rounded-xl border border-shell-border bg-shell-bg-deep px-3 py-2.5 text-[13px] text-shell-text-secondary opacity-70"
              >
                {ART_STYLES.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </Field>
            <Field label="Difficulty">
              <select
                disabled
                defaultValue="Normal"
                className="gs-select w-full cursor-not-allowed rounded-xl border border-shell-border bg-shell-bg-deep px-3 py-2.5 text-[13px] text-shell-text-secondary opacity-70"
              >
                {DIFFICULTIES.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </Field>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={creating}
              className="flex h-[44px] items-center gap-2 rounded-full bg-gradient-to-br from-accent to-accent/70 px-5 text-[13px] font-bold text-white shadow-lg shadow-accent/20 transition-all hover:-translate-y-0.5 hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {creating ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
              {creating ? "Building..." : "Generate with AI"}
            </button>
            <span className="text-[11px] text-shell-text-tertiary">
              Matches your prompt to a starter template and opens Play. Full offline model generation
              arrives in a later phase.
            </span>
          </div>

          {createError && (
            <div
              role="alert"
              className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3 text-[12.5px] text-red-200"
            >
              {createError}
            </div>
          )}

          {liveSteps && liveSteps.length > 0 && (
            <div
              role="status"
              className="mt-3 flex items-start gap-2.5 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3"
            >
              <Info size={16} className="mt-0.5 flex-none text-accent" />
              <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-shell-text-secondary">
                <span className="font-semibold text-shell-text">Building your preview.</span>{" "}
                {liveSteps.find((s) => s.state === "run")?.what ??
                  liveSteps[liveSteps.length - 1]?.what}
              </div>
            </div>
          )}
        </section>

        {/* template gallery */}
        <section>
          <h2 className="text-[16px] font-bold tracking-[-0.01em]">Start from a template</h2>
          <p className="mb-3.5 mt-1 text-[12.5px] text-shell-text-secondary">
            Each template loads a live three.js scene you can play, test and share.
          </p>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
            {TEMPLATES.map((t) => (
              <TemplateCard key={t.id} template={t} onUse={() => handleUseTemplate(t)} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1.5 block text-[12px] font-semibold text-shell-text-secondary">
        {label}
      </span>
      {children}
    </div>
  );
}

function TemplateCard({ template, onUse }: { template: Template; onUse: () => void }) {
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
          <span className="text-[11px] text-shell-text-tertiary">Building block</span>
          <button
            type="button"
            onClick={onUse}
            aria-label={`Use ${template.title} template`}
            className="flex items-center gap-1.5 rounded-full border border-shell-border bg-shell-surface-active px-3 py-1.5 text-[11.5px] font-bold text-shell-text transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Play size={12} />
            Use
          </button>
        </div>
      </div>
    </div>
  );
}
