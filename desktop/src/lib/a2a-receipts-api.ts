export interface Receipt {
  message_id: string;
  agent_id: string;
  delivered_at: number | null;
  seen_at: number | null;
}

export async function getReceipts(messageId: string): Promise<Receipt[]> {
  const r = await fetch(`/api/a2a/messages/${encodeURIComponent(messageId)}/receipts`);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error((body as { error?: string }).error || `HTTP ${r.status}`);
  }
  const data = await r.json();
  return (data as { receipts?: Receipt[] }).receipts ?? [];
}

export async function markSeen(messageId: string): Promise<void> {
  const r = await fetch(`/api/a2a/receipts`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error((body as { error?: string }).error || `HTTP ${r.status}`);
  }
}
