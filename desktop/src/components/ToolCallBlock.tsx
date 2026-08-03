import { Check, Loader2, AlertTriangle } from "lucide-react";
import { Card } from "@/components/ui";
import type { ToolCallContentBlock } from "@/apps/MessagesApp";

export type ToolCallStatus = "running" | "done" | "error";

/**
 * Compact card for a `{kind: "tool_call"}` content block.
 *
 * Shows the tool name, a truncated input preview, and a status indicator that
 * is driven by `block.status`: an animated spinner for `running`, a check mark
 * for `done`, and an alert icon for `error`. Once the call has settled
 * (`done`/`error`) the result preview is surfaced beneath the input.
 */
export function ToolCallBlock({ block }: { block: ToolCallContentBlock }) {
  const { name, input_preview, status, result_preview } = block;
  const isDone = status === "done" || status === "error";

  return (
    <Card
      data-tool-call="true"
      data-status={status}
      className="py-1.5 text-[13px]"
    >
      {/* header: name + status indicator */}
      <div className="flex items-center justify-between px-3 py-1.5">
        <span
          className="font-mono text-[12px] font-semibold text-shell-text-secondary truncate"
          title={name}
          aria-label={`Tool ${name}`}
        >
          {name}
        </span>
        <StatusIndicator status={status} />
      </div>

      {/* body: input preview */}
      {input_preview ? (
        <pre className="mx-3 mb-1 max-h-24 overflow-hidden text-[11px] font-mono text-shell-text-tertiary break-all whitespace-pre-wrap">
          {input_preview}
        </pre>
      ) : (
        <div className="mx-3 mb-1 text-[11px] text-shell-text-tertiary">no input</div>
      )}

      {/* footer: result preview (only once the call is terminal) */}
      {isDone && result_preview ? (
        <pre
          aria-label="result"
          className={`mx-3 mt-1 max-h-28 overflow-hidden text-[11px] font-mono break-all whitespace-pre-wrap ${
            status === "error" ? "text-red-300" : "text-shell-text-secondary"
          }`}
        >
          {result_preview}
        </pre>
      ) : null}
    </Card>
  );
}

function StatusIndicator({ status }: { status: ToolCallStatus }) {
  switch (status) {
    case "running":
      return (
        <span
          data-status-indicator="true"
          aria-label="running"
          className="inline-flex items-center"
        >
          <Loader2 size={12} className="animate-spin text-shell-text-tertiary" />
        </span>
      );
    case "done":
      return (
        <span
          data-status-indicator="true"
          aria-label="done"
          className="inline-flex items-center"
        >
          <Check size={12} className="text-green-400" />
        </span>
      );
    case "error":
      return (
        <span
          data-status-indicator="true"
          aria-label="error"
          className="inline-flex items-center"
        >
          <AlertTriangle size={12} className="text-red-400" />
        </span>
      );
  }
}
