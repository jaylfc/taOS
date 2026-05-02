import { useEffect, useRef, useState } from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";
import { displayAuthor, type AuthorContext } from "./format-author";

type Msg = {
  id: string;
  author_id: string;
  author_type?: "user" | "agent" | "system";
  content: string;
  created_at?: number;
  [key: string]: unknown;
};

export function ThreadPanel({
  channelId,
  parentId,
  onClose,
  onSend,
  isFullscreen = false,
  authorCtx = { currentUserId: null, currentUserDisplayName: null },
}: {
  channelId: string;
  parentId: string;
  onClose: () => void;
  onSend: (content: string, attachments: AttachmentRecord[]) => Promise<void>;
  isFullscreen?: boolean;
  authorCtx?: AuthorContext;
}) {
  const [parent, setParent] = useState<Msg | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    fetch(`/api/chat/messages/${parentId}`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`parent fetch failed (${r.status})`);
        return r.json();
      })
      .then((d) => setParent(d))
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setLoadError("couldn't load this thread");
      });
    return () => controller.abort();
  }, [parentId]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/chat/channels/${channelId}/threads/${parentId}/messages`,
      { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : { messages: [] }))
      .then((d) => setMsgs(d.messages || []))
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setLoadError("couldn't load this thread");
      });
    return () => controller.abort();
  }, [channelId, parentId]);

  async function submit() {
    const content = input.trim();
    if (!content || sending) return;
    setSending(true);
    setSendError(null);
    try {
      await onSend(content, []);
      // Only clear on success so users don't lose their draft on failure.
      setInput("");
    } catch (e) {
      setSendError((e as Error).message || "couldn't send reply");
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div
      className={
        isFullscreen
          ? "fixed inset-0 z-50 bg-shell-surface flex flex-col"
          : "fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 flex flex-col z-40"
      }
      role="complementary"
      aria-label="Thread panel"
      style={isFullscreen ? { paddingTop: "env(safe-area-inset-top, 0px)" } : undefined}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <span className="font-semibold text-sm">Thread</span>
        <button
          aria-label={isFullscreen ? "Back" : "Close thread"}
          onClick={onClose}
          className="p-1 hover:bg-white/5 rounded"
        >{isFullscreen ? "◀" : "✕"}</button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3">
        {parent && (
          <div className="pb-3 border-b border-white/10">
            <div className="text-xs text-white/50 mb-1">{displayAuthor(parent, authorCtx)}</div>
            <div className="text-sm">{parent.content}</div>
          </div>
        )}
        {msgs.map((m) => (
          <div key={m.id}>
            <div className="text-xs text-white/50 mb-0.5">{displayAuthor(m, authorCtx)}</div>
            <div className="text-sm">{m.content}</div>
          </div>
        ))}
        {loadError && (
          <div role="alert" className="text-xs text-red-300">{loadError}</div>
        )}
      </div>

      <div className="px-4 py-3 border-t border-white/10">
        {sendError && (
          <div role="alert" className="text-xs text-red-300 mb-2">{sendError}</div>
        )}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Reply in thread…"
          aria-label="Thread reply"
          rows={2}
          disabled={sending}
          className="w-full bg-white/5 rounded px-3 py-2 text-sm resize-none outline-none border border-white/10 focus:border-sky-400 disabled:opacity-50"
        />
      </div>
    </div>
  );
}
