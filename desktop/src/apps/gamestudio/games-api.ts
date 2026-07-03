import type { GameMeta, GameRecord } from "./types";

/** Thin client for tinyagentos/routes/games.py's persistence endpoints. */

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body?.error) return String(body.error);
  } catch {
    // non-JSON error body; fall through to the status-based message
  }
  return `Request failed (${res.status})`;
}

export async function listGames(): Promise<GameMeta[]> {
  const res = await fetch("/api/games");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { games: GameMeta[] };
  return data.games;
}

export async function getGame(id: string): Promise<GameRecord> {
  const res = await fetch(`/api/games/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as GameRecord;
}

export interface SaveGameBody {
  name?: string;
  prompt?: string;
  template?: string;
  files: Record<string, string>;
}

export async function saveGame(id: string, body: SaveGameBody): Promise<GameRecord> {
  const res = await fetch(`/api/games/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as GameRecord;
}

export async function renameGame(id: string, name: string): Promise<GameRecord> {
  const current = await getGame(id);
  return saveGame(id, { name, prompt: current.prompt, template: current.template, files: current.files });
}

export async function deleteGame(id: string): Promise<void> {
  const res = await fetch(`/api/games/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await readError(res));
}

export function gamePreviewUrl(id: string, path = "index.html"): string {
  return `/api/games/${encodeURIComponent(id)}/preview/${path}`;
}

export function gamePackageUrl(id: string): string {
  return `/api/games/${encodeURIComponent(id)}/package`;
}

/** Fetch a game's .taosapp package as a downloadable File -- used both to
 *  install it (POST to /api/userspace-apps/install) and to export it. */
export async function fetchGamePackage(id: string): Promise<File> {
  const res = await fetch(gamePackageUrl(id));
  if (!res.ok) throw new Error(await readError(res));
  const blob = await res.blob();
  return new File([blob], `${id}.taosapp`, { type: "application/zip" });
}

/** Turn a game id (or prompt text) into a filesystem/URL-safe slug matching
 *  the backend's `[a-z0-9-]{1,64}` requirement. */
export function slugify(text: string): string {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return base || "game";
}

/** A short random suffix so two games created from similar prompts/names
 *  never collide on id. */
export function uniqueSuffix(): string {
  return Math.random().toString(36).slice(2, 6);
}
