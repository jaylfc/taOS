import { useCallback, useEffect, useState } from "react";
import { FolderOpen, Pencil, Trash2, Loader2, AlertCircle, Music2, Plus } from "lucide-react";
import { deleteSong, getSong, listSongs, renameSong, type SongMeta } from "./songs-api";
import type { Song } from "./types";

/* ------------------------------------------------------------------ */
/*  LibraryView -- list of saved songs. Mirrors gamestudio/LibraryView:  */
/*  delete is backend-confirmed (list only drops a row after the DELETE  */
/*  call has actually succeeded, never optimistically).                 */
/* ------------------------------------------------------------------ */

export interface LibraryViewProps {
  onOpenSong: (song: Song) => void;
  onCreateNew: () => void;
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

export function LibraryView({ onOpenSong, onCreateNew }: LibraryViewProps) {
  const [songs, setSongs] = useState<SongMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSongs(await listSongs());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleOpen = useCallback(
    async (meta: SongMeta) => {
      setBusyId(meta.id);
      try {
        onOpenSong(await getSong(meta.id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [onOpenSong],
  );

  const handleRename = useCallback(
    async (meta: SongMeta) => {
      const next = window.prompt("Rename song:", meta.name);
      if (!next?.trim() || next.trim() === meta.name) return;
      setBusyId(meta.id);
      try {
        await renameSong(meta.id, next.trim());
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  const handleDelete = useCallback(
    async (meta: SongMeta) => {
      const ok = window.confirm(`Delete "${meta.name}"? This can't be undone.`);
      if (!ok) return;
      setBusyId(meta.id);
      try {
        await deleteSong(meta.id);
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
        <header className="flex items-center justify-between">
          <div>
            <h2 className="text-[17px] font-bold tracking-[-0.02em]">Your songs</h2>
            <p className="mt-1 text-[12.5px] text-shell-text-secondary">
              Every song saved on this taOS.
            </p>
          </div>
          <button
            type="button"
            onClick={onCreateNew}
            className="flex items-center gap-1.5 rounded-lg border border-shell-border bg-shell-surface px-3 py-1.5 text-[12px] font-bold text-shell-text hover:bg-white/10"
          >
            <Plus size={14} />
            New song
          </button>
        </header>

        {error && (
          <div role="alert" className="flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3 text-[12.5px] text-red-200">
            <AlertCircle size={15} />
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-8 text-[13px] text-shell-text-tertiary">
            <Loader2 size={16} className="animate-spin" />
            Loading your songs...
          </div>
        ) : songs.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-shell-border py-14 text-center text-shell-text-tertiary">
            <Music2 size={28} />
            <p className="text-[13px]">No songs saved yet. Save one from the Studio to see it here.</p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
            {songs.map((s) => {
              const busy = busyId === s.id;
              return (
                <div
                  key={s.id}
                  className="flex flex-col overflow-hidden rounded-2xl border border-shell-border bg-shell-surface shadow-card"
                >
                  <div className="relative flex h-[92px] items-end p-3" style={{ background: "linear-gradient(140deg,#2c3142,#171a24)" }}>
                    <span className="text-[14px] font-extrabold text-white" style={{ textShadow: "0 2px 8px rgba(0,0,0,0.6)" }}>
                      {s.name}
                    </span>
                  </div>
                  <div className="flex flex-1 flex-col gap-2 p-3">
                    <span className="text-[11px] text-shell-text-tertiary">updated {relativeTime(s.updated_at)}</span>
                    <div className="mt-1 flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => void handleOpen(s)}
                        disabled={busy}
                        aria-label={`Open ${s.name}`}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-shell-border bg-shell-surface-active px-2.5 py-1.5 text-[11.5px] font-bold text-shell-text hover:bg-white/10 disabled:opacity-50"
                      >
                        {busy ? <Loader2 size={13} className="animate-spin" /> : <FolderOpen size={13} />}
                        Open
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleRename(s)}
                        disabled={busy}
                        aria-label={`Rename ${s.name}`}
                        className="flex h-8 w-8 flex-none items-center justify-center rounded-lg border border-shell-border text-shell-text-secondary hover:bg-white/10 disabled:opacity-50"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(s)}
                        disabled={busy}
                        aria-label={`Delete ${s.name}`}
                        className="flex h-8 w-8 flex-none items-center justify-center rounded-lg border border-shell-border text-red-400 hover:bg-red-500/10 disabled:opacity-50"
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
