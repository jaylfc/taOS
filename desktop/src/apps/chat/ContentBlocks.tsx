import React, { useState, useId } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { renderInline } from "../MessagesApp";

/* ------------------------------------------------------------------ */
/*  ContentBlock type                                                  */
/* ------------------------------------------------------------------ */

/**
 * Structured content block for session-turn messages (taOStalk #1953).
 *
 * The `kind` field discriminates the block type. Known kinds are
 * rendered by dedicated components; unknown kinds fall through to
 * the dim "unsupported block" fallback. The index signature keeps
 * the type permissive so server-sent blocks with extra fields still
 * type-check without forcing every optional field to be listed.
 */
export type ContentBlock = {
  kind: string;
  text?: string;
  collapsed?: boolean;
  call_id?: string;
  name?: string;
  input_preview?: string;
  status?: string;
  options?: string[];
  [key: string]: unknown;
};

/* ------------------------------------------------------------------ */
/*  TextBlock — reuses the existing markdown renderer                  */
/* ------------------------------------------------------------------ */

/**
 * Renders a `{kind:"text"}` block by delegating to the same
 * `renderInline` markdown pipeline used for ordinary chat messages,
 * so formatting (bold, lists, code, links) is identical.
 */
export function TextBlock({ text }: { text: string }) {
  const id = useId();
  return <>{renderInline(text, `text-${id}`)}</>;
}

/* ------------------------------------------------------------------ */
/*  ThinkingBlock — collapsed-by-default disclosure, dim styling      */
/* ------------------------------------------------------------------ */

/**
 * Renders a `{kind:"thinking"}` block as a collapsed-by-default
 * disclosure. The toggle is a button with `aria-expanded` and
 * `aria-controls` so screen readers announce the expand/collapse
 * state. Thinking text is dimmed to match the Store/Images design
 * bar (subtle tertiary text on a muted surface).
 */
export function ThinkingBlock({
  text,
  collapsed = true,
}: {
  text: string;
  collapsed?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(() => !collapsed);
  const contentId = `thinking-content-${id}`;

  return (
    <div className="mt-2 first:mt-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[12px] font-medium text-shell-text-tertiary hover:text-shell-text transition-colors"
        aria-expanded={open}
        aria-controls={contentId}
      >
        <span>Thinking</span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && (
        <div
          id={contentId}
          className="mt-1.5 text-[13px] text-shell-text-tertiary whitespace-pre-wrap opacity-70"
        >
          {text}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  UnknownBlock — dim fallback for unrecognised kinds                */
/* ------------------------------------------------------------------ */

/**
 * Renders any block whose `kind` is not yet handled by a dedicated
 * component. Shows a dim "unsupported block" line with the kind name
 * so the user can see something was emitted even if the UI doesn't
 * know how to render it yet. This is the slice-2 seam.
 */
export function UnknownBlock({ kind }: { kind: string }) {
  return (
    <div className="text-[12px] text-shell-text-tertiary opacity-70">
      Unsupported block: {kind}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Dispatcher                                                         */
/* ------------------------------------------------------------------ */

/**
 * Maps a single content block to its renderer via a `kind` switch.
 * Unknown kinds fall through to `UnknownBlock`. Each returned element
 * gets a stable key based on its position so React can reconcile
 * block arrays without duplicate-key warnings.
 */
export function renderContentBlock(
  block: ContentBlock,
  index: number,
): React.ReactElement {
  switch (block.kind) {
    case "text":
      return <TextBlock key={`block-${index}`} text={block.text ?? ""} />;
    case "thinking":
      return (
        <ThinkingBlock
          key={`block-${index}`}
          text={block.text ?? ""}
          collapsed={block.collapsed}
        />
      );
    default:
      return <UnknownBlock key={`block-${index}`} kind={block.kind} />;
  }
}
