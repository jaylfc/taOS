import { useState } from "react";
import { Trash2, RotateCcw, ChevronRight, Archive } from "lucide-react";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { Button, Card } from "@/components/ui";
import { type ArchivedAgent } from "./types";

/* ------------------------------------------------------------------ */
/*  Archived agents helpers + panel                                    */
/* ------------------------------------------------------------------ */

function parseArchiveTimestamp(ts: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(ts);
  if (!m) return null;
  return new Date(Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!, +m[6]!));
}

function relativeTimeFromTs(ts: string): string {
  const d = parseArchiveTimestamp(ts);
  if (!d) return ts;
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}

export function ArchivedAgentRow({
  entry,
  onRestore,
  onPurge,
}: {
  entry: ArchivedAgent;
  onRestore: (id: string, name: string) => void;
  onPurge: (id: string, name: string) => void;
}) {
  const displayName = entry.original?.display_name || entry.original?.name || entry.archived_slug;
  const color = entry.original?.color || "#6b7280";
  const emoji = resolveAgentEmoji(entry.original?.emoji, entry.original?.framework);
  const model = entry.original?.model;
  const when = relativeTimeFromTs(entry.archived_at);

  return (
    <Card className="flex items-center gap-4 px-4 py-3 hover:bg-shell-surface/50 transition-colors opacity-80">
      <div className="flex items-center gap-2.5 flex-1 min-w-0">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: color }}
          aria-hidden
        />
        <span className="text-base leading-none shrink-0" aria-hidden="true">
          {emoji}
        </span>
        <span className="font-medium text-sm truncate">{displayName}</span>
        {model && (
          <span className="text-[11px] text-shell-text-tertiary truncate">{model}</span>
        )}
      </div>
      <span className="text-xs text-shell-text-tertiary shrink-0">archived {when}</span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 hover:bg-emerald-500/15 hover:text-emerald-400"
          onClick={() => onRestore(entry.id, displayName)}
          aria-label={`Restore ${displayName}`}
          title="Restore agent"
        >
          <RotateCcw size={15} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 hover:bg-red-500/15 hover:text-red-400"
          onClick={() => onPurge(entry.id, displayName)}
          aria-label={`Permanently delete ${displayName}`}
          title="Delete permanently"
        >
          <Trash2 size={15} />
        </Button>
      </div>
    </Card>
  );
}

export function ArchivedAgentsPanel({
  archived,
  onRestore,
  onPurge,
}: {
  archived: ArchivedAgent[];
  onRestore: (id: string, name: string) => void;
  onPurge: (id: string, name: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (archived.length === 0) return null;
  return (
    <section className="mt-4" aria-label="Archived agents">
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex items-center gap-2 text-xs text-shell-text-secondary hover:text-shell-text transition-colors mb-2"
        aria-expanded={expanded}
        aria-controls="archived-agents-panel"
      >
        <ChevronRight size={14} className={`transition-transform ${expanded ? "rotate-90" : ""}`} />
        <Archive size={13} />
        Archived ({archived.length})
      </button>
      <div
        id="archived-agents-panel"
        className={`space-y-2 ${expanded ? "" : "hidden"}`}
      >
        {archived.map(entry => (
          <ArchivedAgentRow
            key={entry.id}
            entry={entry}
            onRestore={onRestore}
            onPurge={onPurge}
          />
        ))}
      </div>
    </section>
  );
}
