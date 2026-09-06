import { useEffect, useRef, useState } from "react";
import { Sparkles, Wand2, Info, Loader2 } from "lucide-react";
import { generateSite, type GenerateProgress } from "./generate-site";
import { PALETTES, FONTS, type Site, type PaletteId, type FontId } from "./types";

const PROMPT_IDEAS = [
  "A landing page for a to-do app",
  "A portfolio for a photographer",
  "A cafe with a menu and bookings",
  "An event page for a tech meetup",
];

export function GenerateView({
  onGenerate,
}: {
  /** wasAiGenerated is false when we fell back to a matched template, so the
   *  caller can tag provenance honestly (ai-generated vs user-authored). */
  onGenerate: (site: Site, wasAiGenerated: boolean) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [palette, setPalette] = useState<PaletteId | null>(null);
  const [font, setFont] = useState<FontId | null>(null);
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

  const applyOverrides = (site: Site): Site => {
    const theme = { ...site.theme };
    if (palette) theme.palette = palette;
    if (font) theme.font = font;
    return { ...site, theme };
  };

  const generate = async () => {
    if (busy) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    setProgress({ stage: "streaming", detail: "Starting..." });

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const result = await generateSite(
        prompt,
        (p) => {
          if (mountedRef.current) setProgress(p);
        },
        { signal: controller.signal },
      );
      if (result.usedFallback && mountedRef.current) setNotice(result.parseNotice);
      onGenerate(applyOverrides(result.site), !result.usedFallback);
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
  };

  const cancel = () => abortRef.current?.abort();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Generate</h2>
        <span className="text-[12px] text-shell-text-tertiary">Describe your site, the taOS agent designs it</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[640px] px-6 py-8">
          <label htmlFor="ws-prompt" className="mb-2 block text-[13px] font-semibold text-shell-text-secondary">
            What are you building?
          </label>
          <textarea
            id="ws-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={busy}
            placeholder="e.g. A landing page for a meal-planning app for busy families"
            className="h-28 w-full resize-none rounded-xl border border-shell-border bg-shell-surface px-4 py-3 text-[13px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-60"
          />

          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Prompt ideas">
            {PROMPT_IDEAS.map((idea) => (
              <button
                key={idea}
                type="button"
                disabled={busy}
                onClick={() => setPrompt(idea)}
                className="rounded-full border border-shell-border bg-shell-surface px-3 py-1.5 text-[11.5px] font-medium text-shell-text-secondary transition-colors hover:border-shell-border-strong hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {idea}
              </button>
            ))}
          </div>

          <p className="mb-2 mt-6 text-[13px] font-semibold text-shell-text-secondary">Palette</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Palette">
            {(Object.keys(PALETTES) as PaletteId[]).map((id) => {
              const on = palette === id;
              return (
                <button
                  key={id}
                  type="button"
                  aria-pressed={on}
                  disabled={busy}
                  onClick={() => setPalette(on ? null : id)}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-60 ${
                    on ? "border-accent bg-accent-soft text-accent" : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-shell-surface-active"
                  }`}
                >
                  <span className="h-3 w-3 rounded-full" style={{ background: PALETTES[id].colors.accent }} />
                  {PALETTES[id].label}
                </button>
              );
            })}
          </div>

          <p className="mb-2 mt-5 text-[13px] font-semibold text-shell-text-secondary">Type style</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Type style">
            {(Object.keys(FONTS) as FontId[]).map((id) => {
              const on = font === id;
              return (
                <button
                  key={id}
                  type="button"
                  aria-pressed={on}
                  disabled={busy}
                  onClick={() => setFont(on ? null : id)}
                  className={`rounded-full border px-3 py-1.5 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-60 ${
                    on ? "border-accent bg-accent-soft text-accent" : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-shell-surface-active"
                  }`}
                  style={{ fontFamily: FONTS[id].stack }}
                >
                  {FONTS[id].label}
                </button>
              );
            })}
          </div>

          <div className="mt-7 flex items-center gap-2">
            <button
              type="button"
              onClick={() => void generate()}
              disabled={busy}
              className="flex h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-accent to-accent/70 text-[13px] font-bold text-white transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
              {busy ? "Generating..." : "Generate site"}
            </button>
            {busy && (
              <button
                type="button"
                onClick={cancel}
                className="flex h-10 items-center rounded-xl border border-shell-border px-3.5 text-[12.5px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                Cancel
              </button>
            )}
          </div>

          {progress && (
            <div
              role="status"
              className="mt-3 flex items-start gap-2.5 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3"
            >
              <Loader2 size={16} className="mt-0.5 flex-none animate-spin text-accent" />
              <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-shell-text-secondary">
                <span className="font-semibold text-shell-text">Designing your site.</span> {progress.detail}
              </div>
            </div>
          )}

          {error && (
            <div role="alert" className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3 text-[12.5px] text-red-200">
              {error}
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

          <div className="mt-3 flex items-center gap-1.5 text-[11.5px] text-shell-text-tertiary">
            <Sparkles size={13} />
            You can refine everything in the Edit view afterwards.
          </div>
        </div>
      </div>
    </div>
  );
}
