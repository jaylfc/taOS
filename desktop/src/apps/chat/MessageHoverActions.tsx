export function MessageHoverActions({
  onReact,
  onReplyInThread,
  onOverflow,
  dragHandle,
}: {
  onReact: () => void;
  onReplyInThread: () => void;
  onOverflow: (e: React.MouseEvent) => void;
  dragHandle?: React.ReactNode;
}) {
  return (
    <div
      role="toolbar"
      aria-label="Message actions"
      className="inline-flex items-center gap-0.5 bg-shell-bg-deep border border-shell-border-strong rounded-lg shadow-md px-1 py-0.5"
    >
      {dragHandle}
      <button aria-label="Add reaction" onClick={onReact} className="p-1 rounded hover:bg-shell-surface-hover">😀</button>
      <button aria-label="Reply in thread" onClick={onReplyInThread} className="p-1 rounded hover:bg-shell-surface-hover">💬</button>
      <button aria-label="More" onClick={onOverflow} className="p-1 rounded hover:bg-shell-surface-hover">⋯</button>
    </div>
  );
}
