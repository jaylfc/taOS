import { useCallback, useEffect, useState } from "react";
import { FolderOpen, Pencil, Trash2, Loader2, AlertCircle, PenLine } from "lucide-react";
import { deleteDesign, listDesigns, renameDesign, type SavedDesign } from "./designs-api";

/* ------------------------------------------------------------------ */
/*  LibraryView -- grid of saved designs                                */
/*                                                                     */
/*  Delete is backend-confirmed: the list only drops a row after the    */
/*  DELETE call succeeds and the list has been refetched, never          */
/*  optimistically (same pattern as Game Studio's LibraryView).          */
/* ------------------------------------------------------------------ */

export interface LibraryViewProps {
  onOpenDesign: (id: string) => void;
  /** Called after a successful rename so the shell can sync the currently
   *  open design's name if it's the one that was just renamed. */
  onRenamed?: (id: string, name: string) => void;
}

function relativeTime(epochSeconds: number): string {
  const diffMs = Date.now() - epochSeconds * 1000;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function LibraryView({ onOpenDesign, onRenamed }: LibraryViewProps) {
  const [designs, setDesigns] = useState<SavedDesign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDesigns(await listDesigns());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRename = useCallback(
    async (design: SavedDesign) => {
      const next = window.prompt("Rename design:", design.name);
      if (!next?.trim() || next.trim() === design.name) return;
      const name = next.trim();
      setBusyId(design.id);
      try {
        await renameDesign(design.id, name);
        await load();
        onRenamed?.(design.id, name);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [load, onRenamed],
  );

  const handleDelete = useCallback(
    async (design: SavedDesign) => {
      const ok = window.confirm(`Delete "${design.name}"? This can't be undone.`);
      if (!ok) return;
      setBusyId(design.id);
      try {
        await deleteDesign(design.id);
        // Backend-confirmed: only refresh the list (from the server) after
        // the delete call has actually succeeded -- never splice the row
        // out of local state optimistically.
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex flex-col gap-3.5 p-[22px]">
        <header>
          <h2 className="text-[17px] font-bold tracking-[-0.02em]">Your designs</h2>
          <p className="mt-1 text-[12.5px] text-shell-text-secondary">
            Every design you've saved, ready to reopen and keep editing.
          </p>
        </header>

        {error && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3 text-[12.5px] text-red-200"
          >
            <AlertCircle size={15} />
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-8 text-[13px] text-shell-text-tertiary">
            <Loader2 size={16} className="animate-spin" />
            Loading your designs...
          </div>
        ) : designs.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-shell-border py-14 text-center text-shell-text-tertiary">
            <PenLine size={28} />
            <p className="text-[13px]">No saved designs yet. Save one to see it here.</p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
            {designs.map((d) => {
              const busy = busyId === d.id;
              return (
                <div
                  key={d.id}
                  className="flex flex-col overflow-hidden rounded-2xl border border-shell-border bg-shell-surface shadow-card"
                >
                  <div
                    className="relative flex h-[92px] items-center justify-center"
                    style={{ background: "linear-gradient(140deg,#2c3142,#171a24)" }}
                  >
                    <span
                      className="absolute bottom-2 left-3 text-[14px] font-extrabold text-white"
                      style={{ textShadow: "0 2px 8px rgba(0,0,0,0.6)" }}
                    >
                      {d.name}
                    </span>
                  </div>
                  <div className="flex flex-1 flex-col gap-2 p-3">
                    <span className="text-[11px] text-shell-text-tertiary">
                      updated {relativeTime(d.updated_at ?? 0)}
                    </span>
                    <div className="mt-1 flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => onOpenDesign(d.id)}
                        aria-label={`Open ${d.name}`}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-shell-border bg-shell-surface-active px-2.5 py-1.5 text-[11.5px] font-bold text-shell-text hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                      >
                        <FolderOpen size={13} />
                        Open
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleRename(d)}
                        disabled={busy}
                        aria-label={`Rename ${d.name}`}
                        className="flex h-8 w-8 flex-none items-center justify-center rounded-lg border border-shell-border text-shell-text-secondary hover:bg-white/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                      >
                        {busy ? <Loader2 size={13} className="animate-spin" /> : <Pencil size={13} />}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(d)}
                        disabled={busy}
                        aria-label={`Delete ${d.name}`}
                        className="flex h-8 w-8 flex-none items-center justify-center rounded-lg border border-shell-border text-red-400 hover:bg-red-500/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
