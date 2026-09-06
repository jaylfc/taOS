import { useState } from "react";
import { Plus, Save, ArrowUp, ArrowDown, Trash2, Palette, Type, Layers } from "lucide-react";
import { SectionBlock } from "./SectionBlock";
import { newSection } from "./templates";
import {
  PALETTES,
  FONTS,
  SECTION_LABELS,
  type Site,
  type Section,
  type SectionContent,
  type SectionType,
  type PaletteId,
  type FontId,
} from "./types";

const ADDABLE: SectionType[] = ["hero", "features", "textBlock", "gallery", "cta", "contact", "footer"];

function formatUpdated(ts?: number): string {
  if (!ts) return "Unsaved";
  return `Updated ${new Date(ts * 1000).toLocaleString()}`;
}

export interface SavedSite {
  id: string;
  title: string;
  updated_at?: number;
}

interface Props {
  site: Site;
  onChange: (site: Site) => void;
  saved: SavedSite[];
  activeId: string | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onSave: () => void;
  onDelete: (id: string) => void;
}

export function EditView({
  site,
  onChange,
  saved,
  activeId,
  loading,
  saving,
  error,
  onNew,
  onOpen,
  onSave,
  onDelete,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(site.sections[0]?.id ?? null);
  const colors = PALETTES[site.theme.palette].colors;
  const fontStack = FONTS[site.theme.font].stack;

  const selectedIndex = site.sections.findIndex((s) => s.id === selectedId);
  const selected: Section | null = selectedIndex >= 0 ? site.sections[selectedIndex] ?? null : null;

  const setSections = (sections: Section[]) => onChange({ ...site, sections });

  const updateContent = (id: string, content: SectionContent) =>
    setSections(site.sections.map((s) => (s.id === id ? { ...s, content } : s)));

  const addSection = (type: SectionType) => {
    const sec = newSection(type);
    const at = selectedIndex >= 0 ? selectedIndex + 1 : site.sections.length;
    const next = [...site.sections];
    next.splice(at, 0, sec);
    setSections(next);
    setSelectedId(sec.id);
  };

  const move = (dir: -1 | 1) => {
    if (selectedIndex < 0) return;
    const to = selectedIndex + dir;
    if (to < 0 || to >= site.sections.length) return;
    const next = [...site.sections];
    const a = next[selectedIndex]!;
    next[selectedIndex] = next[to]!;
    next[to] = a;
    setSections(next);
  };

  const removeSelected = () => {
    if (!selected) return;
    const next = site.sections.filter((s) => s.id !== selected.id);
    setSections(next);
    setSelectedId(next[Math.max(0, selectedIndex - 1)]?.id ?? null);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* toolbar */}
      <div className="flex h-[46px] flex-none items-center gap-2 border-b border-shell-border bg-shell-bg-deep px-4">
        <input
          value={site.title}
          onChange={(e) => onChange({ ...site, title: e.target.value })}
          aria-label="Site title"
          className="h-8 w-[240px] rounded-lg border border-shell-border bg-shell-surface px-3 text-[12.5px] font-semibold text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        />
        <span className="text-[11px] text-shell-text-tertiary">{site.sections.length} sections</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={onNew}
            className="flex h-8 items-center gap-1.5 rounded-[9px] border border-shell-border px-3 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Plus size={14} /> New
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="flex h-8 items-center gap-1.5 rounded-[9px] bg-gradient-to-br from-accent to-accent/70 px-3.5 text-[12px] font-bold text-white disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Save size={14} /> {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* saved sites sidebar */}
        <aside className="flex w-[188px] flex-none flex-col border-r border-shell-border bg-shell-bg-deep">
          <div className="border-b border-shell-border px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-shell-text-tertiary">
            My sites
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {loading && <p className="px-2 py-1 text-[12px] text-shell-text-tertiary">Loading...</p>}
            {!loading && saved.length === 0 && (
              <p className="px-2 py-1 text-[12px] text-shell-text-tertiary">No saved sites yet</p>
            )}
            {saved.map((s) => (
              <div key={s.id} className="group relative mb-1">
                <button
                  type="button"
                  onClick={() => onOpen(s.id)}
                  className={`w-full rounded-lg px-2 py-2 pr-7 text-left text-[12px] transition-colors hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    activeId === s.id ? "bg-shell-surface text-shell-text" : "text-shell-text-secondary"
                  }`}
                >
                  <div className="truncate font-semibold">{s.title}</div>
                  <div className="truncate text-[10px] text-shell-text-tertiary">{formatUpdated(s.updated_at)}</div>
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${s.title}`}
                  onClick={() => onDelete(s.id)}
                  className="absolute right-1 top-1.5 flex h-6 w-6 items-center justify-center rounded-md text-shell-text-tertiary opacity-0 transition-opacity hover:bg-shell-surface-active hover:text-red-400 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 group-hover:opacity-100"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        </aside>

        {/* canvas */}
        <div className="min-h-0 flex-1 overflow-auto bg-shell-bg-deep p-5">
          <div className="mx-auto max-w-[860px] overflow-hidden rounded-xl border border-shell-border">
            {site.sections.length === 0 && (
              <div className="flex h-64 items-center justify-center text-[13px] text-shell-text-tertiary">
                No sections. Add one from the panel on the right.
              </div>
            )}
            {site.sections.map((s) => (
              <SectionBlock
                key={s.id}
                section={s}
                colors={colors}
                fontStack={fontStack}
                editable
                selected={selectedId === s.id}
                onSelect={() => setSelectedId(s.id)}
                onChange={(content) => updateContent(s.id, content)}
              />
            ))}
          </div>
          {error && (
            <p className="mx-auto mt-3 max-w-[860px] text-[12px] text-red-400" role="alert">
              {error}
            </p>
          )}
        </div>

        {/* inspector */}
        <aside className="flex w-[264px] flex-none flex-col gap-4 overflow-auto border-l border-shell-border bg-shell-bg p-[18px]">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[13px] font-bold">
              <Layers size={15} className="text-accent" /> Selected section
            </div>
            {selected ? (
              <>
                <div className="mb-2 rounded-lg border border-shell-border bg-shell-surface px-3 py-2 text-[12px] font-semibold text-shell-text">
                  {SECTION_LABELS[selected.type]}
                </div>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    aria-label="Move section up"
                    onClick={() => move(-1)}
                    disabled={selectedIndex <= 0}
                    className="flex h-8 flex-1 items-center justify-center rounded-lg border border-shell-border text-shell-text-secondary hover:bg-shell-surface-active disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  >
                    <ArrowUp size={15} />
                  </button>
                  <button
                    type="button"
                    aria-label="Move section down"
                    onClick={() => move(1)}
                    disabled={selectedIndex >= site.sections.length - 1}
                    className="flex h-8 flex-1 items-center justify-center rounded-lg border border-shell-border text-shell-text-secondary hover:bg-shell-surface-active disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  >
                    <ArrowDown size={15} />
                  </button>
                  <button
                    type="button"
                    aria-label="Delete section"
                    onClick={removeSelected}
                    className="flex h-8 flex-1 items-center justify-center rounded-lg border border-shell-border text-shell-text-secondary hover:bg-shell-surface-active hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
                <p className="mt-2 text-[11px] text-shell-text-tertiary">
                  Click any text on the canvas to edit it inline.
                </p>
              </>
            ) : (
              <p className="text-[12px] text-shell-text-tertiary">Select a section on the canvas.</p>
            )}
          </div>

          <div>
            <div className="mb-2 flex items-center gap-2 text-[13px] font-bold">
              <Plus size={15} className="text-accent" /> Add section
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {ADDABLE.map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => addSection(type)}
                  className="rounded-lg border border-shell-border bg-shell-surface px-2 py-2 text-[11.5px] font-semibold text-shell-text-secondary transition-colors hover:border-shell-border-strong hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                >
                  {SECTION_LABELS[type]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center gap-2 text-[13px] font-bold">
              <Palette size={15} className="text-accent" /> Palette
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {(Object.keys(PALETTES) as PaletteId[]).map((id) => {
                const on = site.theme.palette === id;
                return (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onChange({ ...site, theme: { ...site.theme, palette: id } })}
                    className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                      on ? "border-accent bg-accent-soft text-accent" : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-shell-surface-active"
                    }`}
                  >
                    <span className="h-3 w-3 rounded-full" style={{ background: PALETTES[id].colors.accent }} />
                    {PALETTES[id].label}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center gap-2 text-[13px] font-bold">
              <Type size={15} className="text-accent" /> Type style
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {(Object.keys(FONTS) as FontId[]).map((id) => {
                const on = site.theme.font === id;
                return (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onChange({ ...site, theme: { ...site.theme, font: id } })}
                    className={`rounded-lg border px-2.5 py-2 text-[11.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                      on ? "border-accent bg-accent-soft text-accent" : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-shell-surface-active"
                    }`}
                    style={{ fontFamily: FONTS[id].stack }}
                  >
                    {FONTS[id].label}
                  </button>
                );
              })}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
