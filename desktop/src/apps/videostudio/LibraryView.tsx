import { Download, Trash2, Clapperboard } from "lucide-react";
import { type GeneratedVideo } from "./types";

/* ------------------------------------------------------------------ */
/*  LibraryView — generated video grid + detail pane                   */
/* ------------------------------------------------------------------ */

export interface LibraryViewProps {
  videos: GeneratedVideo[];
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (filename: string) => void;
  onDownload: (video: GeneratedVideo) => void;
  onDelete: (filename: string) => void;
}

function VcardSkeleton() {
  return (
    <div
      className="taos-shimmer aspect-video rounded-2xl border border-shell-border bg-shell-surface-active"
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
  videos,
  loading,
  error,
  selectedId,
  onSelect,
  onDownload,
  onDelete,
}: LibraryViewProps) {
  // Only fall back to the first video when there is no explicit selection --
  // don't silently swap in a different clip if the selected one disappears.
  const selected = selectedId
    ? (videos.find((v) => v.filename === selectedId) ?? null)
    : (videos[0] ?? null);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Library</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          {videos.length} {videos.length === 1 ? "clip" : "clips"}
        </span>
      </div>

      {error && (
        <div
          role="alert"
          className="border-b border-red-500/30 bg-red-500/10 px-[22px] py-2 text-xs text-red-400"
        >
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* grid */}
        <div
          className={`grid flex-1 content-start gap-3.5 overflow-auto p-[22px] ${
            selected ? "grid-cols-2" : "grid-cols-3"
          }`}
        >
          {loading ? (
            [0, 1, 2, 3].map((i) => <VcardSkeleton key={i} />)
          ) : videos.length === 0 ? (
            <div className="col-span-full flex flex-col items-center justify-center gap-2 py-16 text-shell-text-tertiary">
              <Clapperboard size={40} className="opacity-30" />
              <p className="text-sm">No videos yet</p>
              <p className="text-xs">Head to Create to make your first one.</p>
            </div>
          ) : (
            videos.map((v) => {
              const sel = v.filename === (selected?.filename ?? null);
              return (
                <button
                  key={v.filename}
                  type="button"
                  aria-label={`Open video: ${v.prompt.slice(0, 40)}`}
                  aria-pressed={sel}
                  onClick={() => onSelect(v.filename)}
                  className={`group relative aspect-video overflow-hidden rounded-2xl border text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-card-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    sel
                      ? "border-accent ring-2 ring-accent/30"
                      : "border-shell-border hover:border-shell-border-strong"
                  }`}
                >
                  {v.url ? (
                    <video
                      src={v.url}
                      muted
                      preload="none"
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <span className="flex h-full w-full items-center justify-center bg-shell-bg-deep text-shell-text-tertiary">
                      <Clapperboard size={22} />
                    </span>
                  )}
                  <span className="pointer-events-none absolute inset-0 flex items-end bg-gradient-to-t from-black/60 via-transparent to-transparent p-2.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                    <span className="line-clamp-2 text-[10.5px] leading-snug text-white">
                      {v.prompt}
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>

        {/* detail pane */}
        {selected && (
          <div className="flex w-[320px] flex-none flex-col gap-4 overflow-auto border-l border-shell-border p-[18px]">
            <div className="aspect-video overflow-hidden rounded-2xl border border-shell-border bg-shell-bg-deep">
              {selected.url ? (
                <video src={selected.url} controls className="h-full w-full object-contain" />
              ) : (
                <span className="flex h-full w-full items-center justify-center text-shell-text-tertiary">
                  <Clapperboard size={28} />
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
              <MetaRow label="Resolution" value={selected.resolution || "—"} />
              <MetaRow label="Duration" value={`${selected.duration || 0}s`} />
              <MetaRow label="Seed" value={String(selected.seed || "—")} />
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => onDownload(selected)}
                aria-label="Download video"
                className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl border border-transparent bg-accent text-[11.5px] font-semibold text-white transition-all hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Download size={14} />
                Download
              </button>
              <button
                type="button"
                onClick={() => onDelete(selected.filename)}
                aria-label="Delete video"
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
