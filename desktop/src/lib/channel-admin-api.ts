/**
 * Thin REST client for Phase 1 chat admin endpoints. Used by
 * ChannelSettingsPanel and AgentContextMenu — the UI layer on top of
 * PATCH /api/chat/channels/{id}, POST /members, POST /muted.
 *
 * Errors: every call throws Error(body.error || `HTTP ${status}`) on
 * non-OK response so callers can surface the server's message inline.
 */

type ChannelPatch = Partial<{
  response_mode: "quiet" | "lively";
  max_hops: number;
  cooldown_seconds: number;
  ephemeral_ttl_seconds: number | null;
  topic: string;
  name: string;
}>;

async function _json(r: Response): Promise<unknown> {
  try { return await r.json(); } catch { return null; }
}

async function _ensureOk(r: Response): Promise<void> {
  if (r.ok) return;
  const body = (await _json(r)) as { error?: string } | null;
  throw new Error(body?.error || `HTTP ${r.status}`);
}

export async function patchChannel(channelId: string, body: ChannelPatch): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await _ensureOk(r);
}

export async function addChannelMember(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "add", slug }),
  });
  await _ensureOk(r);
}

export async function removeChannelMember(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "remove", slug }),
  });
  await _ensureOk(r);
}

export async function muteAgent(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/muted`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "add", slug }),
  });
  await _ensureOk(r);
}

export async function unmuteAgent(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/muted`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "remove", slug }),
  });
  await _ensureOk(r);
}
