async function _ensureOk(r: Response): Promise<void> {
  if (r.ok) return;
  let body: { error?: string } | null = null;
  try { body = await r.json(); } catch { /* ignore */ }
  throw new Error(body?.error || `HTTP ${r.status}`);
}

export async function pinMessage(messageId: string): Promise<void> {
  const r = await fetch(`/api/chat/messages/${messageId}/pin`, { method: "POST" });
  await _ensureOk(r);
}

export async function unpinMessage(messageId: string): Promise<void> {
  const r = await fetch(`/api/chat/messages/${messageId}/pin`, { method: "DELETE" });
  await _ensureOk(r);
}

export async function listPins(channelId: string): Promise<unknown[]> {
  const r = await fetch(`/api/chat/channels/${channelId}/pins`);
  await _ensureOk(r);
  const body = await r.json();
  return body.pins || [];
}

export async function editMessage(messageId: string, content: string): Promise<unknown> {
  const r = await fetch(`/api/chat/messages/${messageId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  await _ensureOk(r);
  return r.json();
}

export async function deleteMessage(messageId: string): Promise<void> {
  const r = await fetch(`/api/chat/messages/${messageId}`, { method: "DELETE" });
  await _ensureOk(r);
}

export async function markUnread(channelId: string, beforeMessageId: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${channelId}/read-cursor/rewind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ before_message_id: beforeMessageId }),
  });
  await _ensureOk(r);
}
