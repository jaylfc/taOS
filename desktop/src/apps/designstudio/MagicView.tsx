import { Loader2, Sparkles } from "lucide-react";
import { MAGIC_STYLE_CHIPS, type GeneratedImage } from "./types";

export interface MagicViewProps {
  prompt: string;
  onPromptChange: (v: string) => void;
  style: string | null;
  onStyleChange: (v: string | null) => void;
  results: GeneratedImage[];
  generating: boolean;
  canGenerate: boolean;
  error: string | null;
  errorNeedsModel: boolean;
  needsModel: boolean;
  onGenerate: () => void;
  onPickModel: () => void;
  onUseResult: (img: GeneratedImage) => void;
}

export function MagicView({
  prompt,
  onPromptChange,
  style,
  onStyleChange,
  results,
  generating,
  canGenerate,
  error,
  errorNeedsModel,
  needsModel,
  onGenerate,
  onPickModel,
  onUseResult,
}: MagicViewProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Magic design</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          Describe it, generate an image for the canvas
        </span>
      </div>

      <div className="flex flex-1 flex-col items-center overflow-auto px-[26px] pb-[26px] pt-[26px]">
        <div className="mb-6 w-full max-w-[640px] text-center">
          <h3 className="text-[24px] font-extrabold tracking-[-0.02em]">
            Describe the design you need.
          </h3>
          <p className="mt-2 text-[13.5px] leading-[1.5] text-shell-text-secondary">
            Generate an image on your cluster, then place it on the canvas and keep editing.
          </p>
        </div>

        <div className="mb-[26px] flex w-full max-w-[640px] gap-2.5">
          <textarea
            aria-label="Design prompt"
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            placeholder="a launch poster for taOS Studios, bold, dark, confident..."
            rows={2}
            className="flex-1 resize-none rounded-[14px] border border-shell-border-strong bg-shell-surface px-4 py-3.5 text-[13.5px] text-shell-text outline-none placeholder:text-shell-text-tertiary focus-visible:ring-2 focus-visible:ring-accent/40"
          />
          <button
            type="button"
            disabled={!canGenerate}
            onClick={onGenerate}
            className="flex items-center gap-2 rounded-[14px] px-[22px] py-3.5 text-[14px] font-bold text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: "linear-gradient(135deg,#a9b0c2,#8b92a3)",
              border: "none",
            }}
          >
            {generating ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
            {generating ? "Generating..." : "Generate"}
          </button>
        </div>

        {needsModel && (
          <div className="mb-4 w-full max-w-[640px] rounded-[12px] border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[13px] text-amber-100">
            <p>Install an image generation model to use Magic.</p>
            <button
              type="button"
              onClick={onPickModel}
              className="mt-2 rounded-[9px] border border-shell-border bg-shell-surface px-3 py-1.5 text-[12px] font-semibold text-shell-text-secondary hover:text-shell-text"
            >
              Browse models
            </button>
          </div>
        )}

        {error && (
          <div className="mb-4 w-full max-w-[640px] rounded-[12px] border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-red-200">
            {error}
            {errorNeedsModel && (
              <button
                type="button"
                onClick={onPickModel}
                className="mt-2 block rounded-[9px] border border-shell-border bg-shell-surface px-3 py-1.5 text-[12px] font-semibold text-shell-text-secondary hover:text-shell-text"
              >
                Install a model
              </button>
            )}
          </div>
        )}

        <div className="mb-6 flex w-full max-w-[640px] flex-wrap gap-2">
          {MAGIC_STYLE_CHIPS.map((chip) => {
            const on = style === chip;
            return (
              <button
                key={chip}
                type="button"
                onClick={() => onStyleChange(on ? null : chip)}
                className={`rounded-full border px-[14px] py-[7px] text-[12px] font-semibold transition-colors ${
                  on
                    ? "border-accent bg-accent/20 text-shell-text"
                    : "border-shell-border bg-shell-surface text-shell-text-secondary hover:border-shell-border-strong hover:text-shell-text"
                }`}
              >
                {chip}
              </button>
            );
          })}
        </div>

        {generating && results.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-8 text-shell-text-tertiary">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-accent border-t-transparent" />
            <span className="text-[13px]">Generating your design...</span>
          </div>
        )}

        {results.length > 0 && (
          <div className="grid w-full max-w-[760px] grid-cols-3 gap-[14px]">
            {results.map((img) => (
              <button
                key={img.id}
                type="button"
                onClick={() => onUseResult(img)}
                className="relative cursor-pointer overflow-hidden rounded-[12px] border border-shell-border transition-all hover:-translate-y-[3px] hover:border-accent/50"
                style={{ aspectRatio: "0.8" }}
              >
                <img src={img.url} alt={img.prompt} className="h-full w-full object-cover" />
                <div
                  className="absolute bottom-0 left-0 right-0 px-[11px] py-[9px] text-left text-[11px] text-white"
                  style={{ background: "linear-gradient(transparent, rgba(0,0,0,0.6))" }}
                >
                  {img.prompt.slice(0, 48)}
                  {img.prompt.length > 48 ? "..." : ""}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}