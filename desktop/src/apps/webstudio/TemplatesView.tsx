import { PALETTES, type Site } from "./types";
import { TEMPLATES, siteFromTemplate, type SiteTemplate } from "./templates";

export function TemplatesView({ onLoad }: { onLoad: (site: Site) => void }) {
  const use = (tpl: SiteTemplate) => onLoad(siteFromTemplate(tpl));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Templates</h2>
        <span className="text-[12px] text-shell-text-tertiary">{TEMPLATES.length} starter sites</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-[22px]">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {TEMPLATES.map((tpl) => {
            const p = PALETTES[tpl.theme.palette].colors;
            return (
              <button
                key={tpl.id}
                type="button"
                onClick={() => use(tpl)}
                aria-label={`Use ${tpl.name} template`}
                className="group flex flex-col overflow-hidden rounded-2xl border border-shell-border bg-shell-surface text-left transition-all hover:-translate-y-0.5 hover:border-shell-border-strong hover:shadow-[var(--shadow-card-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <div className="relative h-32 overflow-hidden" style={{ background: p.bg }}>
                  <div className="absolute left-4 top-4 h-2.5 w-20 rounded-full" style={{ background: p.accent }} />
                  <div className="absolute left-4 top-8 h-2 w-28 rounded-full" style={{ background: p.muted }} />
                  <div className="absolute bottom-4 left-4 right-4 grid grid-cols-3 gap-2">
                    <div className="h-8 rounded-md" style={{ background: p.surface, border: `1px solid ${p.border}` }} />
                    <div className="h-8 rounded-md" style={{ background: p.surface, border: `1px solid ${p.border}` }} />
                    <div className="h-8 rounded-md" style={{ background: p.surface, border: `1px solid ${p.border}` }} />
                  </div>
                </div>
                <div className="flex flex-col gap-0.5 p-3.5">
                  <span className="text-[13.5px] font-bold text-shell-text">{tpl.name}</span>
                  <span className="text-[11.5px] text-shell-text-tertiary">{tpl.tagline}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
