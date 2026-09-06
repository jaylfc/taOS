import { Loader2, Play, Sparkles } from "lucide-react";

const STYLE_CHIPS = ["Lo-fi", "Cinematic", "House", "Ambient", "Drum and bass", "Hip-hop"];

export interface ComposedTrack {
  id: string;
  url: string;
  prompt: string;
  duration: number;
}

export interface ComposeViewProps {
  prompt: string;
  onPromptChange: (value: string) => void;
  style: string | null;
  onStyleChange: (value: string | null) => void;
  results: ComposedTrack[];
  generating: boolean;
  canGenerate: boolean;
  error: string | null;
  needsBackend: boolean;
  onGenerate: () => void;
  onOpenStore: () => void;
}

export function ComposeView({
  prompt,
  onPromptChange,
  style,
  onStyleChange,
  results,
  generating,
  canGenerate,
  error,
  needsBackend,
  onGenerate,
  onOpenStore,
}: ComposeViewProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Compose</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          Describe a track, get a full arrangement
        </span>
      </div>

      <div className="flex flex-1 flex-col items-center overflow-auto p-[26px]">
        <div className="mb-[22px] max-w-[620px] text-center">
          <h3 className="text-[23px] font-extrabold tracking-[-0.02em]">
            Hum it, or just describe it.
          </h3>
          <p className="mt-2 text-[13.5px] leading-[1.5] text-shell-text-secondary">
            An agent on your cluster writes a multi-track arrangement you can open in the studio
            and edit note by note. Apache-licensed models, nothing leaves your network.
          </p>
        </div>

        <div className="mb-3.5 flex w-full max-w-[620px] gap-2.5">
          <textarea
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            placeholder="a warm lo-fi beat, 90 bpm, dusty drums, rhodes chords, vinyl crackle..."
            rows={2}
            className="flex-1 resize-none rounded-[14px] border border-shell-border bg-shell-surface px-4 py-3.5 text-[13.5px] text-shell-text outline-none placeholder:text-shell-text-tertiary focus-visible:ring-2 focus-visible:ring-accent/40"
          />
          <button
            type="button"
            disabled={!canGenerate}
            onClick={onGenerate}
            className="flex cursor-pointer items-center gap-2 rounded-[14px] border-0 px-[22px] text-[14px] font-bold text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: "linear-gradient(135deg, var(--color-accent-strong, #a9b0c2), var(--color-accent, #8b92a3))",
            }}
          >
            {generating ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
            {generating ? "Generating..." : "Generate"}
          </button>
        </div>

        {needsBackend && (
          <div className="mb-4 w-full max-w-[620px] rounded-[12px] border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[13px] text-amber-100">
            <p>Install a music generation backend (musicgpt, musicgen, or stable-audio-open) to compose tracks.</p>
            <button
              type="button"
              onClick={onOpenStore}
              className="mt-2 text-[12px] font-semibold text-accent underline-offset-2 hover:underline"
            >
              Open Store
            </button>
          </div>
        )}

        {error && (
          <div className="mb-4 w-full max-w-[620px] rounded-[12px] border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-red-100">
            {error}
          </div>
        )}

        <div className="mb-[26px] flex flex-wrap justify-center gap-2">
          {STYLE_CHIPS.map((chip) => {
            const active = style === chip;
            return (
              <button
                key={chip}
                type="button"
                onClick={() => onStyleChange(active ? null : chip)}
                className={`rounded-full border px-3.5 py-[7px] text-[11.5px] font-semibold ${
                  active
                    ? "border-accent bg-accent/20 text-accent"
                    : "border-shell-border bg-shell-surface text-shell-text-secondary"
                }`}
              >
                {chip}
              </button>
            );
          })}
        </div>

        <div className="flex w-full max-w-[660px] flex-col gap-[11px]">
          {results.length === 0 && !generating && (
            <p className="text-center text-[12.5px] text-shell-text-tertiary">
              Generated tracks will appear here.
            </p>
          )}
          {results.map((result) => (
            <div
              key={result.id}
              className="flex cursor-pointer items-center gap-3.5 rounded-[14px] border border-shell-border bg-shell-surface px-[15px] py-[13px] hover:bg-shell-surface-active"
            >
              <div
                className="flex h-[38px] w-[38px] flex-none items-center justify-center rounded-[11px] text-white"
                style={{ background: "linear-gradient(135deg, var(--color-accent-strong, #a9b0c2), var(--color-accent, #8b92a3))" }}
              >
                <Play size={16} fill="currentColor" />
              </div>

              <div className="min-w-0 flex-1">
                <p className="truncate text-[12.5px] font-semibold text-shell-text">{result.prompt}</p>
                <audio controls preload="none" src={result.url} className="mt-2 h-8 w-full" />
              </div>

              <span className="whitespace-nowrap text-[11px] text-shell-text-tertiary">
                {result.duration}s
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}