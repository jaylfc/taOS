import { useState } from "react";

export function MessageEditor({
  initial, onSave, onCancel,
}: {
  initial: string;
  onSave: (content: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <textarea
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
        else if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          const trimmed = value.trim();
          if (trimmed) onSave(trimmed);
          else onCancel();
        }
      }}
      aria-label="Edit message"
      rows={1}
      className="w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-sm"
    />
  );
}
