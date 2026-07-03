import { useState } from "react";
import { Sparkles, Wand2, Info } from "lucide-react";
import { matchTemplate } from "./match-template";
import { siteFromTemplate } from "./templates";
import { PALETTES, FONTS, type Site, type PaletteId, type FontId } from "./types";

const PROMPT_IDEAS = [
  "A landing page for a to-do app",
  "A portfolio for a photographer",
  "A cafe with a menu and bookings",
  "An event page for a tech meetup",
];

export function GenerateView({ onGenerate }: { onGenerate: (site: Site) => void }) {
  const [prompt, setPrompt] = useState("");
  const [palette, setPalette] = useState<PaletteId | null>(null);
  const [font, setFont] = useState<FontId | null>(null);

  const generate = () => {
    const tpl = matchTemplate(prompt);
    const site = siteFromTemplate(tpl, prompt.trim() ? prompt.trim().slice(0, 60) : undefined);
    if (palette) site.theme.palette = palette;
    if (font) site.theme.font = font;
    onGenerate(site);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Generate</h2>
        <span className="text-[12px] text-shell-text-tertiary">Describe your site, we scaffold it</span>
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
            placeholder="e.g. A landing page for a meal-planning app for busy families"
            className="h-28 w-full resize-none rounded-xl border border-shell-border bg-shell-surface px-4 py-3 text-[13px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          />

          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Prompt ideas">
            {PROMPT_IDEAS.map((idea) => (
              <button
                key={idea}
                type="button"
                onClick={() => setPrompt(idea)}
                className="rounded-full border border-shell-border bg-shell-surface px-3 py-1.5 text-[11.5px] font-medium text-shell-text-secondary transition-colors hover:border-shell-border-strong hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
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
                  onClick={() => setPalette(on ? null : id)}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
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
                  onClick={() => setFont(on ? null : id)}
                  className={`rounded-full border px-3 py-1.5 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    on ? "border-accent bg-accent-soft text-accent" : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-shell-surface-active"
                  }`}
                  style={{ fontFamily: FONTS[id].stack }}
                >
                  {FONTS[id].label}
                </button>
              );
            })}
          </div>

          <button
            type="button"
            onClick={generate}
            className="mt-7 flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-accent to-accent/70 text-[13px] font-bold text-white transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Wand2 size={16} />
            Generate site
          </button>

          <div className="mt-4 flex items-start gap-2 rounded-xl border border-accent/30 bg-accent-soft px-3.5 py-3 text-[12px] text-shell-text-secondary" role="status">
            <Info size={15} className="mt-0.5 shrink-0 text-accent" />
            <span>
              Phase 1 matches your prompt to the closest starter template and seeds it into the editor, ready to
              edit. Full offline-model generation that writes bespoke copy and layout arrives in a later phase.
            </span>
          </div>

          <div className="mt-3 flex items-center gap-1.5 text-[11.5px] text-shell-text-tertiary">
            <Sparkles size={13} />
            You can refine everything in the Edit view afterwards.
          </div>
        </div>
      </div>
    </div>
  );
}
