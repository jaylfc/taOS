import { Sparkles, Pencil, Download, Check } from "lucide-react";
import {
  Segmented,
  Slider,
  Chip,
  ModelPill,
  SeedPill,
  GroupLabel,
} from "./controls";
import {
  SIZE_OPTIONS,
  STYLE_CHIPS,
  type GeneratedImage,
  type GenerateMode,
} from "./types";

/* ------------------------------------------------------------------ */
/*  CreateView — result canvas + filmstrip + controls + prompt bar     */
/* ------------------------------------------------------------------ */

export interface CreateViewProps {
  mode: GenerateMode;
  onModeChange: (m: GenerateMode) => void;

  modelName: string;
  modelMeta: string;
  onPickModel: () => void;

  prompt: string;
  onPromptChange: (v: string) => void;

  size: number;
  onSizeChange: (v: number) => void;
  steps: number;
  onStepsChange: (v: number) => void;
  guidance: number;
  onGuidanceChange: (v: number) => void;
  style: string | null;
  onStyleChange: (v: string | null) => void;
  seed: string;
  onReroll: () => void;

  results: GeneratedImage[];
  activeResultId: string | null;
  onSelectResult: (id: string) => void;

  generating: boolean;
  canGenerate: boolean;
  onGenerate: () => void;
  error: string | null;

  onEditResult: (img: GeneratedImage) => void;
  onDownloadResult: (img: GeneratedImage) => void;
}

export function CreateView(props: CreateViewProps) {
  const {
    mode,
    onModeChange,
    modelName,
    modelMeta,
    onPickModel,
    prompt,
    onPromptChange,
    size,
    onSizeChange,
    steps,
    onStepsChange,
    guidance,
    onGuidanceChange,
    style,
    onStyleChange,
    seed,
    onReroll,
    results,
    activeResultId,
    onSelectResult,
    generating,
    canGenerate,
    onGenerate,
    error,
    onEditResult,
    onDownloadResult,
  } = props;

  const active =
    results.find((r) => r.id === activeResultId) ?? results[0] ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Create</h2>
        <span className="text-[12px] text-shell-text-tertiary truncate">
          {modelMeta}
        </span>
        <div className="ml-auto">
          <Segmented<GenerateMode>
            ariaLabel="Generation mode"
            value={mode}
            onChange={onModeChange}
            options={[
              { value: "single", label: "Single" },
              { value: "batch", label: "Batch" },
            ]}
          />
        </div>
      </div>

      {/* Phone-width viewports stack the stage over the controls rail (and
          scroll) so neither is squeezed off-screen; md+ keeps the desktop
          side-by-side row. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
        {/* stage: canvas + filmstrip */}
        <div className="flex min-w-0 flex-1 flex-col p-[22px]">
          <div className="relative flex min-h-[260px] flex-1 items-center justify-center overflow-hidden rounded-2xl border border-shell-border bg-shell-bg-deep">
            {generating ? (
              <div className="flex flex-col items-center gap-3 text-shell-text-tertiary">
                <div className="h-7 w-7 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                <span className="text-xs">Generating…</span>
              </div>
            ) : active && active.url ? (
              <>
                <img
                  src={active.url}
                  alt={active.prompt}
                  className="h-full w-full object-cover"
                />
                <div className="absolute left-3.5 top-3.5 flex items-center gap-1.5 rounded-full border border-shell-border bg-shell-bg-glass px-3 py-1.5 text-[11px] font-semibold text-shell-text-secondary backdrop-blur-md">
                  <Check size={12} className="text-emerald-400" />
                  Generated
                  {active.backend ? ` · ${active.backend}` : ""}
                </div>
                <div className="absolute bottom-3.5 right-3.5 flex gap-2">
                  <button
                    type="button"
                    aria-label="Edit this image"
                    onClick={() => onEditResult(active)}
                    className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border-strong bg-shell-bg-glass text-shell-text backdrop-blur-md transition-transform hover:-translate-y-0.5 hover:text-accent"
                  >
                    <Pencil size={16} />
                  </button>
                  <button
                    type="button"
                    aria-label="Download this image"
                    onClick={() => onDownloadResult(active)}
                    className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border-strong bg-shell-bg-glass text-shell-text backdrop-blur-md transition-transform hover:-translate-y-0.5 hover:text-accent"
                  >
                    <Download size={16} />
                  </button>
                </div>
              </>
            ) : (
              <div className="flex flex-col items-center gap-2 px-6 text-center text-shell-text-tertiary">
                <Sparkles size={36} className="opacity-30" />
                <p className="text-sm">Your generated image appears here</p>
                <p className="text-xs">
                  Describe something below, then hit Generate.
                </p>
              </div>
            )}
          </div>

          {/* filmstrip */}
          {results.length > 0 && (
            <div
              className="mt-3.5 flex flex-none gap-2.5 overflow-x-auto"
              role="listbox"
              aria-label="Recent results"
            >
              {results.slice(0, 12).map((r) => {
                const on = r.id === (active?.id ?? null);
                return (
                  <button
                    key={r.id}
                    type="button"
                    role="option"
                    aria-selected={on}
                    aria-label={`Select result: ${r.prompt.slice(0, 40)}`}
                    onClick={() => onSelectResult(r.id)}
                    className={`h-16 w-16 flex-none overflow-hidden rounded-xl border transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                      on
                        ? "border-accent ring-2 ring-accent/30"
                        : "border-shell-border"
                    }`}
                  >
                    {r.url ? (
                      <img
                        src={r.url}
                        alt={r.prompt}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <span className="flex h-full w-full items-center justify-center bg-shell-bg-deep text-shell-text-tertiary">
                        <Sparkles size={16} />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* controls rail */}
        <div className="flex w-full flex-none flex-col gap-[18px] overflow-auto border-t border-shell-border p-[18px] md:w-[286px] md:border-l md:border-t-0">
          <div>
            <GroupLabel>Model</GroupLabel>
            <ModelPill name={modelName} meta={modelMeta} onClick={onPickModel} />
          </div>

          <div>
            <GroupLabel>Size</GroupLabel>
            <div className="grid grid-cols-4 gap-[7px]">
              {SIZE_OPTIONS.map((s) => {
                const on = s === size;
                return (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onSizeChange(s)}
                    className={`rounded-xl border py-2.5 text-center text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                      on
                        ? "border-transparent bg-accent text-white"
                        : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-white/10"
                    }`}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          </div>

          <Slider
            id="create-steps"
            label="Steps"
            value={steps}
            min={1}
            max={50}
            display={String(steps)}
            onChange={(v) => onStepsChange(Math.round(v))}
          />

          <Slider
            id="create-guidance"
            label="Guidance"
            value={guidance}
            min={1}
            max={20}
            step={0.5}
            display={guidance.toFixed(1)}
            onChange={onGuidanceChange}
          />

          <div>
            <GroupLabel>Style</GroupLabel>
            <div className="flex flex-wrap gap-[7px]">
              {STYLE_CHIPS.map((s) => (
                <Chip
                  key={s}
                  label={s}
                  on={style === s}
                  onClick={() => onStyleChange(style === s ? null : s)}
                />
              ))}
            </div>
          </div>

          <div>
            <GroupLabel>Seed</GroupLabel>
            <SeedPill seed={seed} onReroll={onReroll} />
          </div>
        </div>
      </div>

      {/* prompt bar. Extra bottom inset so the textarea + Generate button
          clear the phone home indicator / dock edge instead of sitting flush
          under it. On desktop env(safe-area-inset-bottom) is 0, so the layout
          is unchanged there. */}
      <div
        className="flex flex-none items-end gap-3 border-t border-shell-border bg-shell-bg-deep px-[22px] py-4"
        style={{ paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))" }}
      >
        <label htmlFor="create-prompt" className="sr-only">
          Image prompt
        </label>
        <textarea
          id="create-prompt"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canGenerate) {
              e.preventDefault();
              onGenerate();
            }
          }}
          placeholder="Describe the image you want to create…"
          rows={1}
          className="min-h-[50px] flex-1 resize-none rounded-2xl border border-shell-border bg-shell-surface px-4 py-3.5 text-[13.5px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
        />
        <button
          type="button"
          onClick={onGenerate}
          disabled={!canGenerate}
          className="flex h-[50px] flex-none items-center gap-2 rounded-2xl bg-gradient-to-br from-accent to-accent/70 px-6 text-sm font-bold text-white shadow-lg shadow-accent/20 transition-all hover:-translate-y-0.5 hover:brightness-105 disabled:pointer-events-none disabled:opacity-50"
        >
          <Sparkles size={18} />
          {generating ? "Generating…" : "Generate"}
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="border-t border-red-500/30 bg-red-500/10 px-[22px] py-2 text-xs text-red-400"
        >
          {error}
        </div>
      )}
    </div>
  );
}
