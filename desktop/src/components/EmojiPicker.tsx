import { useEffect, useRef, useState } from "react";
import Picker, { EmojiClickData, Theme } from "emoji-picker-react";

export function EmojiPickerField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", handleKey);
    document.addEventListener("mousedown", handleClick);
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.removeEventListener("mousedown", handleClick);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="border border-white/10 rounded w-10 h-10 text-xl flex items-center justify-center bg-shell-bg-deep hover:bg-white/5 transition-colors"
        aria-label="Open emoji picker"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        {value || "+"}
      </button>
      {open && (
        <div className="absolute z-50 mt-1" role="dialog" aria-label="Emoji picker">
          <Picker
            theme={Theme.DARK}
            onEmojiClick={(d: EmojiClickData) => {
              onChange(d.emoji);
              setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
