import type { Song } from "./types";

/** Thin client for tinyagentos/routes/songs.py's persistence endpoints. */

export interface SongMeta {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
}

interface SongRecord extends SongMeta {
  content: string;
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body?.error) return String(body.error);
  } catch {
    // non-JSON error body; fall through to the status-based message
  }
  return `Request failed (${res.status})`;
}

/** The persisted `content` column holds everything but id/name (those are
 *  first-class columns on the songs table, mirroring OfficeDocStore). */
function songToContent(song: Song): string {
  return JSON.stringify({ tempo: song.tempo, key: song.key, timeSig: song.timeSig, tracks: song.tracks });
}

function recordToSong(record: SongRecord): Song {
  const parsed = JSON.parse(record.content || "{}") as Partial<Song>;
  return {
    id: record.id,
    name: record.name,
    tempo: parsed.tempo ?? 92,
    key: parsed.key ?? "C maj",
    timeSig: parsed.timeSig ?? "4/4",
    tracks: parsed.tracks ?? [],
  };
}

export async function listSongs(): Promise<SongMeta[]> {
  const res = await fetch("/api/songs");
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as SongMeta[];
}

export async function getSong(id: string): Promise<Song> {
  const res = await fetch(`/api/songs/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(await readError(res));
  return recordToSong((await res.json()) as SongRecord);
}

/** Create or update a song. Returns the song with its (possibly new,
 *  server-assigned) id. */
export async function saveSong(song: Song): Promise<Song> {
  const body = JSON.stringify({ name: song.name, content: songToContent(song) });
  const isNew = song.id.startsWith("local-");
  const res = await fetch(isNew ? "/api/songs" : `/api/songs/${encodeURIComponent(song.id)}`, {
    method: isNew ? "POST" : "PUT",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!res.ok) throw new Error(await readError(res));
  return recordToSong((await res.json()) as SongRecord);
}

export async function renameSong(id: string, name: string): Promise<Song> {
  const current = await getSong(id);
  return saveSong({ ...current, id, name });
}

export async function deleteSong(id: string): Promise<void> {
  const res = await fetch(`/api/songs/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await readError(res));
}

/** Phase 1's "share" affordance: download the song as a plain JSON file
 *  rather than a self-contained player package -- see the TODO in
 *  routes/songs.py for why the .taosapp player was deferred. */
export function exportSongFile(song: Song): void {
  const blob = new Blob([JSON.stringify(song, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slugify(song.name)}.taosong`;
  a.click();
  URL.revokeObjectURL(url);
}

export function slugify(text: string): string {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return base || "song";
}
