export function ThreadIndicator({
  replyCount,
  lastReplyAt,
  onOpen,
}: {
  replyCount: number;
  lastReplyAt?: number | null;
  onOpen: () => void;
}) {
  if (replyCount === 0) return null;
  const label = lastReplyAt
    ? `💬 ${replyCount} repl${replyCount === 1 ? "y" : "ies"} · last reply ${relative(lastReplyAt)}`
    : `💬 ${replyCount} repl${replyCount === 1 ? "y" : "ies"}`;
  return (
    <button
      onClick={onOpen}
      className="mt-1 px-2 py-0.5 text-xs text-sky-200 hover:bg-white/5 rounded"
      aria-label="Open thread"
    >{label}</button>
  );
}

function relative(ts: number): string {
  const now = Date.now() / 1000;
  const delta = Math.max(0, now - ts);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}
