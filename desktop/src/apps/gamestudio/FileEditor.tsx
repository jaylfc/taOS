import { useRef } from "react";

/* ------------------------------------------------------------------ */
/*  FileEditor -- textarea-based code editor with line numbers          */
/*                                                                     */
/*  Coding Studio's CodeMirror setup (codingstudio/CodeView.tsx) is a    */
/*  private, unexported component -- not importable as-is. Per the      */
/*  agreed v1 fallback this is a clean textarea editor: monospace,       */
/*  synced-scroll line-number gutter, Tab-to-indent.                    */
/* ------------------------------------------------------------------ */

export interface FileEditorProps {
  path: string;
  content: string;
  onChange: (next: string) => void;
}

export function FileEditor({ path, content, onChange }: FileEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const lineCount = content.split("\n").length;

  const syncScroll = () => {
    if (gutterRef.current && textareaRef.current) {
      gutterRef.current.scrollTop = textareaRef.current.scrollTop;
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const el = e.currentTarget;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const next = content.slice(0, start) + "  " + content.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + 2;
    });
  };

  return (
    <div className="flex h-full min-h-0 w-full font-mono text-[12.5px]">
      <div
        ref={gutterRef}
        aria-hidden="true"
        className="select-none overflow-hidden whitespace-pre bg-[#15161f] px-3 py-3 text-right text-white/25"
        style={{ lineHeight: "1.6" }}
      >
        {Array.from({ length: lineCount }, (_, i) => i + 1).join("\n")}
      </div>
      <textarea
        ref={textareaRef}
        key={path}
        value={content}
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncScroll}
        onKeyDown={handleKeyDown}
        spellCheck={false}
        aria-label={`Editing ${path}`}
        className="min-h-0 flex-1 resize-none overflow-auto bg-[#1a1b2e] px-3 py-3 text-white/85 outline-none"
        style={{ lineHeight: "1.6", tabSize: 2 }}
      />
    </div>
  );
}
