import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench, CheckCircle2, XCircle, Loader2, AlertCircle } from "lucide-react";
import type { ContentBlock } from "../MessagesApp";

/* ------------------------------------------------------------------ */
/*  TextBlock                                                         */
/* ------------------------------------------------------------------ */

export function TextBlock({ text }: { text: string }) {
  return (
    <div className="text-[15px] leading-[1.46] whitespace-pre-wrap break-words select-text">
      {text}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ThinkingBlock (collapsible)                                       */
/* ------------------------------------------------------------------ */

export function ThinkingBlock({ text, collapsed = true }: { text: string; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);

  return (
    <div className="my-1.5 rounded-lg border border-white/10 bg-black/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] text-shell-text-tertiary hover:text-shell-text transition-colors w-full text-left"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium uppercase tracking-wide">Thinking</span>
      </button>
      {open && (
        <div className="px-3 pb-2.5 text-[13px] text-shell-text-secondary whitespace-pre-wrap italic border-t border-white/5 pt-2">
          {text}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ToolCallBlock                                                     */
/* ------------------------------------------------------------------ */

export function ToolCallBlock({
  block,
}: {
  block: ContentBlock;
}) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = block.status === "running";
  const isDone = block.status === "done";
  const isError = block.status === "error";

  return (
    <div
      className={`my-1.5 rounded-lg border overflow-hidden ${
        isRunning
          ? "border-accent/30 bg-accent/5"
          : isError
            ? "border-red-500/30 bg-red-500/5"
            : "border-white/10 bg-shell-bg-deep"
      }`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex items-center gap-2 px-3 py-2 w-full text-left"
      >
        <span className="shrink-0">
          {isRunning && <Loader2 size={14} className="text-accent animate-spin" />}
          {isDone && <CheckCircle2 size={14} className="text-emerald-400" />}
          {isError && <XCircle size={14} className="text-red-400" />}
        </span>
        <Wrench size={12} className="text-shell-text-tertiary shrink-0" />
        <span className="text-[13px] font-medium text-shell-text flex-1 min-w-0 truncate">
          {block.name || "tool"}
        </span>
        {block.input_preview && !expanded && (
          <span className="text-[11px] text-shell-text-tertiary truncate max-w-[200px]">
            {block.input_preview}
          </span>
        )}
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {expanded && (
        <div className="px-3 pb-2.5 space-y-1.5 border-t border-white/5 pt-2">
          {block.input_preview && (
            <div>
              <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary">Input</span>
              <pre className="mt-0.5 text-[12px] font-mono text-shell-text-secondary bg-black/20 rounded p-2 overflow-x-auto">
                {block.input_preview}
              </pre>
            </div>
          )}
          {block.result_preview && (
            <div>
              <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary">Result</span>
              <pre className={`mt-0.5 text-[12px] font-mono rounded p-2 overflow-x-auto ${
                isError ? "text-red-300 bg-red-500/10" : "text-shell-text-secondary bg-black/20"
              }`}>
                {block.result_preview}
              </pre>
            </div>
          )}
          {!block.input_preview && !block.result_preview && (
            <span className="text-[11px] text-shell-text-tertiary">No details</span>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  StatusBlock (+ question variant)                                  */
/* ------------------------------------------------------------------ */

export function StatusBlock({ block }: { block: ContentBlock }) {
  const isQuestion = block.kind === "question";

  return (
    <div
      className={`my-1.5 rounded-lg border px-3 py-2 ${
        isQuestion
          ? "border-accent/40 bg-accent/10 text-shell-text"
          : "border-white/10 bg-shell-bg-deep text-shell-text-secondary"
      }`}
    >
      <div className="flex items-start gap-2">
        <AlertCircle size={14} className={`shrink-0 mt-0.5 ${isQuestion ? "text-accent" : "text-shell-text-tertiary"}`} />
        <div className="text-[13px] leading-relaxed">
          {block.text}
          {isQuestion && block.options && block.options.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {block.options.map((opt) => (
                <span
                  key={opt}
                  className="px-2 py-0.5 rounded text-[11px] bg-white/10 border border-white/10 text-shell-text"
                >
                  {opt}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  UnknownBlock                                                      */
/* ------------------------------------------------------------------ */

export function UnknownBlock({ kind }: { kind: string }) {
  return (
    <div className="my-1.5 rounded-lg border border-white/5 bg-black/10 px-3 py-2 text-[12px] text-shell-text-tertiary">
      Unsupported block: {kind}
    </div>
  );
}
