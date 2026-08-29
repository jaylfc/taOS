export function PinBadge({ count, onClick }: { count: number; onClick: () => void }) {
  if (count === 0) return null;
  return (
    <button
      onClick={onClick}
      className="ml-1 px-1.5 py-0.5 text-xs bg-shell-surface rounded opacity-70 hover:opacity-100"
      aria-label={`Pinned messages (${count})`}
    >📌 {count}</button>
  );
}
