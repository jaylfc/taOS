import { Download, Link, AlertCircle, Image as ImageIcon, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui";
import type { LibraryItem, LibraryArtifact, LibraryJob } from "@/lib/library";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const KIND_LABELS: Record<string, string> = {
  text: "Text",
  pdf: "PDF",
  image: "Image",
  archive: "Archive",
  file: "File",
  "url:youtube": "YouTube",
  "url:web": "Web",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  processing: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  ready: "bg-green-500/15 text-green-400 border-green-500/30",
  error: "bg-red-500/15 text-red-400 border-red-500/30",
};

const JOB_STATE_COLORS: Record<string, string> = {
  queued: "bg-white/10 text-shell-text-tertiary border-white/10",
  processing: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  done: "bg-green-500/15 text-green-400 border-green-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
};

const TEXT_ARTIFACT_KINDS = ["text", "transcript", "description", "ocr"];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function parseMeta(metaJson: string): Record<string, unknown> {
  try {
    return JSON.parse(metaJson || "{}") as Record<string, unknown>;
  } catch {
    return {};
  }
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? STATUS_COLORS.pending ?? "";
}

function jobStateColor(state: string): string {
  return JOB_STATE_COLORS[state] ?? JOB_STATE_COLORS.queued ?? "";
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export interface LibraryItemCardProps {
  item: LibraryItem;
  artifacts?: LibraryArtifact[];
  jobs?: LibraryJob[];
  onLinkToCollection?: (item: LibraryItem) => void;
  onDownload?: (item: LibraryItem) => void;
  onOpenSource?: (item: LibraryItem) => void;
}

export function LibraryItemCard({
  item,
  artifacts = [],
  jobs = [],
  onLinkToCollection,
  onDownload,
  onOpenSource,
}: LibraryItemCardProps) {
  const itemMeta = parseMeta(item.meta_json);
  const duration = itemMeta.duration ? Number(itemMeta.duration) : 0;
  const durationStr = formatDuration(duration);
  const preview = itemMeta.preview ? String(itemMeta.preview) : null;
  const error =
    item.status === "error"
      ? (String(itemMeta.error || "") || "Processing failed")
      : null;

  const thumbnailArtifact = artifacts.find((a) => a.kind === "thumbnail");
  const textArtifacts = artifacts.filter((a) => TEXT_ARTIFACT_KINDS.includes(a.kind));

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-sm font-medium leading-snug line-clamp-1">
            {item.title || "Untitled"}
          </CardTitle>
          <span
            className={cn(
              "shrink-0 text-[10px] px-1.5 py-0.5 rounded border",
              statusColor(item.status),
            )}
          >
            {item.status}
          </span>
        </div>
        <CardDescription className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span
            className={cn(
              "px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20",
            )}
          >
            {kindLabel(item.kind)}
          </span>
          {durationStr && <span>{durationStr}</span>}
          <span>{formatBytes(item.bytes)}</span>
          {item.source_url && (
            <button
              type="button"
              onClick={() => onOpenSource?.(item)}
              aria-label={`Open source ${item.source_url}`}
              className="underline decoration-accent/40 hover:decoration-accent"
            >
              <ExternalLink size={10} className="inline align-middle" />
            </button>
          )}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Thumbnail */}
        {thumbnailArtifact ? (
          <img
            src={thumbnailArtifact.path}
            alt={`Thumbnail for ${item.title || "item"}`}
            className="w-full aspect-video object-cover rounded-lg bg-shell-surface"
            onError={(e) => {
              const target = e.target as HTMLImageElement;
              target.style.display = "none";
            }}
          />
        ) : (
          <div className="flex flex-col items-center justify-center w-full aspect-video rounded-lg bg-shell-surface text-shell-text-tertiary">
            <ImageIcon size={20} />
            <span className="text-[10px] mt-1">No thumbnail</span>
          </div>
        )}

        {/* Error state -- failure must be visible, never silent */}
        {error && (
          <div role="alert" className="flex items-start gap-2 text-xs text-red-400">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Pipeline stages (jobs shape from spec section 2) */}
        {jobs.length > 0 ? (
          <div className="space-y-1">
            <div className="text-[10px] text-shell-text-tertiary uppercase">
              Pipeline
            </div>
            <div className="flex flex-wrap gap-1">
              {jobs.map((job) => (
                <span
                  key={job.id}
                  className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded border",
                    jobStateColor(job.state),
                  )}
                  title={job.error || undefined}
                >
                  {job.stage}: {job.state}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <div className="text-[10px] text-shell-text-tertiary">
            No pipeline stages
          </div>
        )}

        {/* Artifacts with preview */}
        {textArtifacts.length > 0 ? (
          <div className="space-y-2">
            <div className="text-[10px] text-shell-text-tertiary uppercase">
              Artifacts
            </div>
            {textArtifacts.map((art) => {
              const artMeta = parseMeta(art.meta_json);
              const charCount = artMeta.char_count
                ? `${Number(artMeta.char_count)} chars`
                : "";
              return (
                <div key={art.id} className="text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-shell-text-secondary">
                      {art.kind}
                    </span>
                    {charCount && (
                      <span className="text-shell-text-tertiary">{charCount}</span>
                    )}
                  </div>
                  {preview ? (
                    <p className="text-[11px] text-shell-text-secondary line-clamp-3 mt-1 leading-relaxed">
                      {preview}
                    </p>
                  ) : (
                    <p className="text-[11px] text-shell-text-tertiary mt-1">
                      No preview available
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-[10px] text-shell-text-tertiary">
            No artifacts
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 pt-2 border-t border-white/[0.06]">
          <button
            type="button"
            onClick={() => onLinkToCollection?.(item)}
            className="text-xs px-2 py-1 rounded hover:bg-white/[0.05] transition-colors flex items-center gap-1"
            aria-label="Link to collection"
          >
            <Link size={12} />
            Link to collection
          </button>
          <button
            type="button"
            onClick={() => onDownload?.(item)}
            disabled
            className="text-xs px-2 py-1 rounded text-shell-text-tertiary cursor-not-allowed flex items-center gap-1"
            aria-label="Download (not available yet)"
          >
            <Download size={12} />
            Download
          </button>
        </div>
      </CardContent>
    </Card>
  );
}
