/** Thin client for tinyagentos/routes/game_assets.py -- Game Studio AI assets. */

export type AssetKind = "texture" | "sprite";

export interface GenerateTextureBody {
  prompt: string;
  width?: number;
  height?: number;
  tileable?: boolean;
  kind?: AssetKind;
}

/** Shape of POST /api/games/{id}/assets/texture.
 *  ``available:false`` is a tier-gate signal (no GPU/NPU worker), NOT an error,
 *  so the UI shows a "needs a GPU worker" state rather than a failure toast. */
export interface TextureResult {
  available: boolean;
  reason?: string;
  status?: string;
  filename?: string;
  path?: string;
  kind?: AssetKind;
  tier?: string;
  tileable?: boolean;
  seed?: number;
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

export async function generateTexture(
  gameId: string,
  body: GenerateTextureBody,
): Promise<TextureResult> {
  const res = await fetch(`/api/games/${encodeURIComponent(gameId)}/assets/texture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TextureResult;
}
