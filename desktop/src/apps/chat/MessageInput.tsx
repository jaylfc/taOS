import { useRef, useCallback } from "react";
import { Send, Paperclip, AtSign } from "lucide-react";
import { Button, Textarea } from "@/components/ui";
import { SlashMenu, type SlashCommandsBySlug } from "./SlashMenu";
import { AttachmentsBar, type PendingAttachment } from "./AttachmentsBar";
import type { Channel } from "./types";

export interface MessageInputProps {
  /** Current input text. */
  value: string;
  /** Called when the user types. */
  onChange: (value: string) => void;
  /** Called on Enter (no shift). */
  onSend: () => void;
  /** The active channel. */
  channel: Channel | undefined;
  /** Whether the current channel is archived. */
  isArchived: boolean;
  /** Whether mobile layout is active. */
  isMobile: boolean;
  /** Keyboard inset for mobile safe-area handling. */
  keyboardInset: number;
  /** Slash commands by slug. */
  slashCommands: SlashCommandsBySlug;

  /* ---- slash menu derived state (computed by parent) ---- */
  showSlash: boolean;
  slashQuery: string;
  slashAgent: string | undefined;

  /* ---- @mention state (computed by parent) ---- */
  mention: { partial: string; atIndex: number } | null;
  mentionCandidates: string[];
  mentionSel: number;
  onMentionSelChange: (sel: number) => void;
  onInsertMention: (slug: string | undefined) => void;
  onDismissMention: () => void;

  /* ---- attachments ---- */
  pendingAttachments: PendingAttachment[];
  onRemoveAttachment: (id: string) => void;
  onRetryAttachment: (id: string) => void;

  /* ---- callbacks ---- */
  onFileUpload: () => void;
  onSlashPick: (slug: string, cmd: string) => void;
  onSlashClose: () => void;
  /** Optional paste handler (e.g. image upload from clipboard). */
  onPaste?: (e: React.ClipboardEvent) => void;
}

export function MessageInput({
  value,
  onChange,
  onSend,
  channel,
  isArchived,
  isMobile,
  keyboardInset,
  slashCommands,
  showSlash,
  slashQuery,
  slashAgent,
  mention,
  mentionCandidates,
  mentionSel,
  onMentionSelChange,
  onInsertMention,
  onDismissMention,
  pendingAttachments,
  onRemoveAttachment,
  onRetryAttachment,
  onFileUpload,
  onSlashPick,
  onSlashClose,
  onPaste,
}: MessageInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        onSend();
      }
    },
    [onSend],
  );

  return (
    <>
      {/* pending attachments bar */}
      <AttachmentsBar
        items={pendingAttachments}
        onRemove={onRemoveAttachment}
        onRetry={onRetryAttachment}
      />

      {/* input area */}
      <div
        className="px-4 py-3 border-t border-white/[0.06] shrink-0"
        style={
          isMobile
            ? { paddingBottom: `max(env(safe-area-inset-bottom), ${keyboardInset}px)` }
            : undefined
        }
      >
        <div className="relative">
          {showSlash && (
            <SlashMenu
              commands={slashCommands}
              queryAfterSlash={slashQuery}
              members={channel?.members || []}
              scopedAgent={slashAgent}
              onPick={onSlashPick}
              onClose={onSlashClose}
            />
          )}
          {mention && mentionCandidates.length > 0 && !showSlash && (
            <div
              role="listbox"
              aria-label="Mention a member"
              className="absolute bottom-full left-0 mb-2 w-full max-w-md bg-shell-surface border border-white/10 rounded-lg shadow-xl max-h-60 overflow-y-auto text-sm"
            >
              {mentionCandidates.map((slug, i) => (
                <button
                  key={slug}
                  role="option"
                  aria-selected={i === mentionSel}
                  onMouseEnter={() => onMentionSelChange(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    onInsertMention(slug);
                  }}
                  className={`w-full text-left px-3 py-1.5 flex items-center gap-2 ${
                    i === mentionSel ? "bg-white/10" : "hover:bg-white/5"
                  }`}
                >
                  <AtSign size={13} className="text-white/40" aria-hidden="true" />
                  <span className="font-mono text-[13px]">@{slug}</span>
                </button>
              ))}
            </div>
          )}
          <div
            className={`flex items-end gap-2 rounded-2xl border px-2 py-1.5 ${
              isArchived
                ? "bg-white/[0.02] border-white/[0.04] opacity-50"
                : "bg-shell-surface border-shell-border-strong"
            }`}
          >
            <Button
              variant="ghost"
              size="icon"
              onClick={onFileUpload}
              className="h-8 w-8 shrink-0 mb-0.5"
              aria-label="Upload file"
              disabled={isArchived}
            >
              <Paperclip size={16} />
            </Button>
            <Textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => !isArchived && onChange(e.target.value)}
              onKeyDown={(e) => !isArchived && handleKeyDown(e)}
              onBlur={() => onDismissMention()}
              onPaste={onPaste}
              placeholder={
                isArchived
                  ? "This chat is archived"
                  : `Message #${channel?.name ?? ""}...`
              }
              rows={1}
              disabled={isArchived}
              className="flex-1 bg-transparent border-0 px-1 py-1.5 min-h-0 text-[13px] focus-visible:ring-0 focus-visible:border-0 max-h-[120px] disabled:cursor-not-allowed"
              aria-label="Message input"
            />
            <Button
              size="icon"
              onClick={onSend}
              disabled={
                (!value.trim() && pendingAttachments.length === 0) ||
                isArchived ||
                pendingAttachments.some((a) => a.uploading)
              }
              className="h-8 w-8 shrink-0 mb-0.5"
              aria-label="Send message"
            >
              <Send size={15} />
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
