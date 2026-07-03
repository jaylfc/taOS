import { useEffect, useRef } from "react";
import { Clapperboard, Download, ExternalLink } from "lucide-react";
import {
  Slider,
  Chip,
  ModelPill,
  SeedPill,
  GroupLabel,
} from "../images/controls";
import {
  MODEL_OPTIONS,
  RESOLUTION_OPTIONS,
  MIN_DURATION,
  MAX_DURATION,
  type GeneratedVideo,
} from "./types";

/* ------------------------------------------------------------------ */
/*  CreateView — prompt bar + controls + latest result                 */
/*                                                                     */
/*  The backend (routes/video.py) generates synchronously: the POST    */
/*  resolves only once the clip is done (or errors), with no job id or  */
/*  status endpoint to poll. So instead of faking a progress bar, the   */
/*  stage shows a spinner with a real elapsed-time counter.             */
/* ------------------------------------------------------------------ */

export interface CreateViewProps {
  modelId: string;
  onModelChange: (id: string) => void;
  modelMeta: string;

  prompt: string;
  onPromptChange: (v: string) => void;

  duration: number;
  onDurationChange: (v: number) => void;
  resolution: string;
  onResolutionChange: (v: string) => void;
  seed: string;
  onReroll: () => void;

  latest: GeneratedVideo | null;

  generating: boolean;
  elapsedSeconds: number;
  canGenerate: boolean;
  onGenerate: () => void;

  error: string | null;
  needsBackend: boolean;
  onOpenStore: () => void;

  onOpenLibrary: () => void;
}

export function CreateView(props: CreateViewProps) {
  const {
    modelId,
    onModelChange,
    modelMeta,
    prompt,
    onPromptChange,
    duration,
    onDurationChange,
    resolution,
    onResolutionChange,
    seed,
    onReroll,
    latest,
    generating,
    elapsedSeconds,
    canGenerate,
    onGenerate,
    error,
    needsBackend,
    onOpenStore,
    onOpenLibrary,
  } = props;

  const selectedModel = MODEL_OPTIONS.find((m) => m.id === modelId);

  // Auto-grow the prompt textarea with its content, up to the max-h set
  // below (CSS then clamps and scrolls beyond that).
  const promptRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = promptRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [prompt]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Create</h2>
        <span className="text-[12px] text-shell-text-tertiary truncate">
          {modelMeta}
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
        {/* stage */}
        <div className="flex min-w-0 flex-1 flex-col p-[22px]">
          <div className="relative flex min-h-[260px] flex-1 items-center justify-center overflow-hidden rounded-2xl border border-shell-border bg-shell-bg-deep">
            {generating ? (
              <div className="flex flex-col items-center gap-3 text-shell-text-tertiary">
                <div className="h-7 w-7 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                <span className="text-xs">
                  Generating… {elapsedSeconds}s
                </span>
                <span className="text-[11px] text-shell-text-tertiary">
                  This can take several minutes.
                </span>
              </div>
            ) : latest && latest.url ? (
              <>
                <video
                  src={latest.url}
                  controls
                  className="h-full w-full object-contain"
                />
                <div className="absolute bottom-3.5 right-3.5 flex gap-2">
                  <a
                    href={latest.url}
                    download={`${latest.filename}`}
                    aria-label="Download this video"
                    className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border-strong bg-shell-bg-glass text-shell-text backdrop-blur-md transition-transform hover:-translate-y-0.5 hover:text-accent"
                  >
                    <Download size={16} />
                  </a>
                  <button
                    type="button"
                    aria-label="Open in Library"
                    onClick={onOpenLibrary}
                    className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border-strong bg-shell-bg-glass text-shell-text backdrop-blur-md transition-transform hover:-translate-y-0.5 hover:text-accent"
                  >
                    <ExternalLink size={16} />
                  </button>
                </div>
              </>
            ) : (
              <div className="flex flex-col items-center gap-2 px-6 text-center text-shell-text-tertiary">
                <Clapperboard size={36} className="opacity-30" />
                <p className="text-sm">Your generated video appears here</p>
                <p className="text-xs">
                  Describe a scene below, then hit Generate.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* controls rail */}
        <div className="flex w-full flex-none flex-col gap-[18px] overflow-auto border-t border-shell-border p-[18px] md:w-[286px] md:border-t-0 md:border-l">
          <div>
            <GroupLabel>Model</GroupLabel>
            <ModelPill
              name={selectedModel?.label ?? modelId}
              meta={selectedModel?.meta ?? ""}
              onClick={() => {
                const idx = MODEL_OPTIONS.findIndex((m) => m.id === modelId);
                const next =
                  MODEL_OPTIONS[(idx + 1) % MODEL_OPTIONS.length] ??
                  MODEL_OPTIONS[0];
                onModelChange(next.id);
              }}
            />
          </div>

          <div>
            <GroupLabel>Resolution</GroupLabel>
            <div className="grid grid-cols-2 gap-[7px]">
              {RESOLUTION_OPTIONS.map((r) => (
                <Chip
                  key={r.id}
                  label={r.label}
                  on={r.id === resolution}
                  onClick={() => onResolutionChange(r.id)}
                />
              ))}
            </div>
          </div>

          <Slider
            id="video-duration"
            label="Duration"
            value={duration}
            min={MIN_DURATION}
            max={MAX_DURATION}
            display={`${duration}s`}
            onChange={(v) => onDurationChange(Math.round(v))}
          />

          <div>
            <GroupLabel>Seed</GroupLabel>
            <SeedPill seed={seed} onReroll={onReroll} />
          </div>
        </div>
      </div>

      {/* prompt bar */}
      <div
        className="flex flex-none items-end gap-3 border-t border-shell-border bg-shell-bg-deep px-[22px] py-4"
        style={{ paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))" }}
      >
        <label htmlFor="video-prompt" className="sr-only">
          Video prompt
        </label>
        <textarea
          ref={promptRef}
          id="video-prompt"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canGenerate) {
              e.preventDefault();
              onGenerate();
            }
          }}
          placeholder="Describe the video you want to create…"
          rows={1}
          className="min-h-[50px] max-h-40 flex-1 resize-none overflow-y-auto rounded-2xl border border-shell-border bg-shell-surface px-4 py-3.5 text-[13.5px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
        />
        <button
          type="button"
          onClick={onGenerate}
          disabled={!canGenerate}
          className="flex h-[50px] flex-none items-center gap-2 rounded-2xl bg-gradient-to-br from-accent to-accent/70 px-6 text-sm font-bold text-white shadow-lg shadow-accent/20 transition-all hover:-translate-y-0.5 hover:brightness-105 disabled:pointer-events-none disabled:opacity-50"
        >
          <Clapperboard size={18} />
          {generating ? `Generating… ${elapsedSeconds}s` : "Generate"}
        </button>
      </div>

      {needsBackend && (
        <div className="border-t border-amber-500/30 bg-amber-500/10 px-[22px] py-3 text-[13px] text-amber-100">
          <p>{error ?? "Connect a video generation backend (WanGP / Wan 2.1) to create videos."}</p>
          <button
            type="button"
            onClick={onOpenStore}
            className="mt-1.5 text-[12px] font-semibold text-accent underline-offset-2 hover:underline"
          >
            Open Store
          </button>
        </div>
      )}

      {!needsBackend && error && (
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
