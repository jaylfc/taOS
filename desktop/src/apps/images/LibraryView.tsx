import { RefreshCw, Download, Trash2, Pencil, ImageIcon } from "lucide-react";
import { Segmented } from "./controls";
import { type GeneratedImage, type LibraryFilter } from "./types";

/* ------------------------------------------------------------------ */
/*  LibraryView — gallery grid + detail pane                           */
/* ------------------------------------------------------------------ */

export interface LibraryViewProps {
  images: GeneratedImage[];
  loading: boolean;
  filter: LibraryFilter;
  onFilterChange: (f: LibraryFilter) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onReroll: (img: GeneratedImage) => void;
  onDownload: (img: GeneratedImage) => void;
  onDelete: (id: string) => void;
  onEdit: (img: GeneratedImage) => void;
}

function GcardSkeleton() {
  return (
    <div
      className="taos-shimmer aspect-square rounded-2xl border border-shell-border bg-shell-surface-active"
      aria-hidden="true"
    />
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-[11.5px]">
      <span className="text-shell-text-tertiary">{label}</span>
      <b className="font-semibold tabular-nums text-shell-text">{value}</b>
    </div>
  );
}

export function LibraryView({
  images,
  loading,
  filter,
  onFilterChange,
  selectedId,
  onSelect,
  onReroll,
  onDownload,
  onDelete,
  onEdit,
}: LibraryViewProps) {
  const filtered = images.filter((img) => {
    if (filter === "all") return true;
    const m = img.model.toLowerCase();
    if (filter === "flux") return m.includes("flux");
    if (filter === "sdxl") return m.includes("sdxl") || m.includes("sd-xl");
    return true;
  });

  const selected =
    filtered.find((i) => i.id === selectedId) ?? filtered[0] ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Library</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          {images.length} {images.length === 1 ? "creation" : "creations"}
        </span>
        <div className="ml-auto">
          <Segmented<LibraryFilter>
            ariaLabel="Filter by model"
            value={filter}
            onChange={onFilterChange}
            options={[
              { value: "all", label: "All" },
              { value: "flux", label: "FLUX" },
              { value: "sdxl", label: "SDXL" },
            ]}
          />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* grid */}
        <div
          className={`grid flex-1 content-start gap-3.5 overflow-auto p-[22px] grid-cols-2 sm:grid-cols-3 ${
            selected ? "md:grid-cols-3" : "md:grid-cols-4"
          }`}
        >
          {loading ? (
            [0, 1, 2, 3, 4, 5].map((i) => <GcardSkeleton key={i} />)
          ) : filtered.length === 0 ? (
            <div className="col-span-full flex flex-col items-center justify-center gap-2 py-16 text-shell-text-tertiary">
              <ImageIcon size={40} className="opacity-30" />
              <p className="text-sm">No images yet</p>
              <p className="text-xs">Head to Create to make your first one.</p>
            </div>
          ) : (
            filtered.map((img) => {
              const sel = img.id === (selected?.id ?? null);
              return (
                <button
                  key={img.id}
                  type="button"
                  aria-label={`Open image: ${img.prompt.slice(0, 40)}`}
                  aria-pressed={sel}
                  onClick={() => onSelect(img.id)}
                  className={`group relative aspect-square overflow-hidden rounded-2xl border text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-card-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    sel
                      ? "border-accent ring-2 ring-accent/30"
                      : "border-shell-border hover:border-shell-border-strong"
                  }`}
                >
                  {img.url ? (
                    <img
                      src={img.url}
                      alt={img.prompt}
                      className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                    />
                  ) : (
                    <span className="flex h-full w-full items-center justify-center bg-shell-bg-deep text-shell-text-tertiary">
                      <ImageIcon size={22} />
                    </span>
                  )}
                  <span className="pointer-events-none absolute inset-0 flex items-end bg-gradient-to-t from-black/60 via-transparent to-transparent p-2.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                    <span className="line-clamp-2 text-[10.5px] leading-snug text-white">
                      {img.prompt}
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>

        {/* detail pane */}
        {selected && (
          <div className="flex w-full md:w-[300px] flex-none flex-col gap-4 overflow-auto border-t md:border-t-0 md:border-l border-shell-border p-[18px]">
            <div className="aspect-square overflow-hidden rounded-2xl border border-shell-border bg-shell-bg-deep">
              {selected.url ? (
                <img
                  src={selected.url}
                  alt={selected.prompt}
                  className="h-full w-full object-cover"
                />
              ) : (
                <span className="flex h-full w-full items-center justify-center text-shell-text-tertiary">
                  <ImageIcon size={28} />
                </span>
              )}
            </div>

            <h3 className="text-sm font-bold tracking-[-0.01em] line-clamp-1">
              {selected.prompt.split(" ").slice(0, 4).join(" ") || "Untitled"}
            </h3>
            <p className="text-[12.5px] leading-relaxed text-shell-text-secondary">
              {selected.prompt}
            </p>

            <div className="flex flex-col gap-2">
              <MetaRow label="Model" value={selected.model || "—"} />
              <MetaRow
                label="Size"
                value={
                  typeof selected.size === "number"
                    ? `${selected.size} × ${selected.size}`
                    : String(selected.size || "—")
                }
              />
              <MetaRow label="Steps" value={String(selected.steps || "—")} />
              <MetaRow label="Seed" value={String(selected.seed || "—")} />
              <MetaRow label="Backend" value={selected.backend || "—"} />
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => onReroll(selected)}
                aria-label="Re-roll with a new seed"
                className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl border border-transparent bg-accent text-[11.5px] font-semibold text-white transition-all hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <RefreshCw size={14} />
                Re-roll
              </button>
              <button
                type="button"
                onClick={() => onEdit(selected)}
                aria-label="Edit image"
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border bg-shell-surface text-shell-text transition-colors hover:bg-white/10 hover:border-shell-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                onClick={() => onDownload(selected)}
                aria-label="Download image"
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border bg-shell-surface text-shell-text transition-colors hover:bg-white/10 hover:border-shell-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Download size={14} />
              </button>
              <button
                type="button"
                onClick={() => onDelete(selected.id)}
                aria-label="Delete image"
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border bg-shell-surface text-shell-text transition-colors hover:bg-red-500/15 hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
