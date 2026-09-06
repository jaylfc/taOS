import { useCallback, useRef } from "react";

/**
 * Returns a function the composer calls on each keystroke. Emits POST
 * /api/chat/channels/{id}/typing at most once per second; the server
 * TTL is 3s so one POST per second is enough to keep the indicator alive
 * while the user is actively typing.
 */
export function useTypingEmitter(
  channelId: string | null,
  authorId: string,
  debounceMs = 1000,
): () => void {
  const lastSentAt = useRef(0);

  return useCallback(() => {
    if (!channelId) return;
    const now = Date.now();
    if (now - lastSentAt.current < debounceMs) return;
    lastSentAt.current = now;
    fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/typing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ author_id: authorId }),
    }).catch(() => {});
  }, [channelId, authorId, debounceMs]);
}
