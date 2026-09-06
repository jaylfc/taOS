export function PinRequestAffordance({
  authorId, onApprove,
}: {
  authorId: string;
  onApprove: () => void;
}) {
  return (
    <div className="mt-1 flex items-center gap-2 text-xs">
      <span className="text-white/60">@{authorId} wants to pin this</span>
      <button
        onClick={onApprove}
        className="px-2 py-0.5 bg-sky-500/20 text-sky-200 rounded hover:bg-sky-500/30"
        aria-label={`Pin this message from ${authorId}`}
      >📌 Pin this</button>
    </div>
  );
}
