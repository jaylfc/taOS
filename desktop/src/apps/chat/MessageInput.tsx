import { useRef, useCallback, useEffect, useState } from "react";
import {
  Send,
  Paperclip,
  AtSign,
  Plus,
  Smile,
  ArrowUp,
  MoreHorizontal,
  MessagesSquare,
  Search,
  Users,
  Info,
} from "lucide-react";
import Picker, { Theme } from "emoji-picker-react";
import { Button, Textarea } from "@/components/ui";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { MessageAvatar } from "./MessageAvatar";
import { SlashMenu, type SlashCommandsBySlug } from "./SlashMenu";
import { AttachmentsBar, type PendingAttachment } from "./AttachmentsBar";
import type { Channel, LiveAgent } from "./types";

export interface ChannelParty {
  /** Display name for the header / composer. */
  title: string;
  /** "group" for multi-member channels, "agent" for a DM with a known agent, else "person". */
  kind: "group" | "agent" | "person";
  /** Muted line under the title (member count, or where an agent runs). */
  subtitle: string;
  /** The agent for an agent DM, when known. */
  agent?: LiveAgent;
}

/**
 * Derive the mobile title / subtitle for a channel. Only reads data the
 * channel and the live agent roster really expose; nothing is invented.
 * `where` selects what the subtitle says for an agent DM: the framework and
 * model it runs on (composer) or just its kind (header).
 */
export function describeChannelParty(
  channel: Channel | undefined,
  liveAgents: LiveAgent[] = [],
  where: "runtime" | "kind" = "runtime",
): ChannelParty {
  const name = channel?.name ?? "";
  if (channel?.type === "dm") {
    const other = (channel.members ?? []).find((m) => m !== "user") ?? name;
    const agent = liveAgents.find((a) => a.name === other);
    if (agent) {
      const runtime = [agent.framework, agent.model].filter(Boolean).join(" \u00b7 ");
      return {
        title: agent.display_name || agent.name,
        kind: "agent",
        subtitle: where === "runtime" && runtime ? runtime : "Agent",
        agent,
      };
    }
    return { title: other || name, kind: "person", subtitle: "" };
  }
  const n = channel?.members?.length ?? 0;
  return {
    title: name,
    kind: "group",
    subtitle: n > 0 ? `${n} ${n === 1 ? "person" : "people"}` : "",
  };
}

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

  /* ---- mobile context row (all optional; only used when isMobile) ---- */
  liveAgents?: LiveAgent[];
  onOpenSettings?: () => void;
  onToggleAllThreads?: () => void;
  onToggleSearch?: () => void;
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
  liveAgents,
  onOpenSettings,
  onToggleAllThreads,
  onToggleSearch,
}: MessageInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const menuWrapRef = useRef<HTMLDivElement>(null);
  const emojiWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen && !emojiOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        setEmojiOpen(false);
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuOpen && !menuWrapRef.current?.contains(t)) setMenuOpen(false);
      if (emojiOpen && !emojiWrapRef.current?.contains(t)) setEmojiOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [menuOpen, emojiOpen]);

  const insertEmoji = useCallback(
    (emoji: string) => {
      const el = textareaRef.current;
      const start = el?.selectionStart ?? value.length;
      const end = el?.selectionEnd ?? value.length;
      onChange(value.slice(0, start) + emoji + value.slice(end));
      setEmojiOpen(false);
      const caret = start + emoji.length;
      requestAnimationFrame(() => {
        el?.focus();
        el?.setSelectionRange(caret, caret);
      });
    },
    [value, onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        onSend();
      }
    },
    [onSend],
  );

  const overlays = (
    <>
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
              className="absolute bottom-full left-0 mb-2 w-full max-w-md bg-shell-surface border border-shell-border-strong rounded-lg shadow-xl max-h-60 overflow-y-auto text-sm"
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
                    i === mentionSel ? "bg-shell-border-strong" : "hover:bg-shell-surface-hover"
                  }`}
                >
                  <AtSign size={13} className="text-shell-text-tertiary" aria-hidden="true" />
                  <span className="font-mono text-[13px]">@{slug}</span>
                </button>
              ))}
            </div>
          )}
    </>
  );

  if (isMobile) {
    const party = describeChannelParty(channel, liveAgents, "runtime");
    const hasContent = value.trim().length > 0 || pendingAttachments.length > 0;
    const canSend = !isArchived && hasContent && !pendingAttachments.some((a) => a.uploading);
    const placeholder = isArchived
      ? "This chat is archived"
      : `Message ${party.title || channel?.name || ""}`.trimEnd();
    const members = (channel?.members ?? []).filter((m) => m !== "user");
    const menuItems: { label: string; icon: React.ReactNode; run: () => void }[] = [];
    if (channel && channel.type !== "dm" && onOpenSettings) {
      menuItems.push({ label: "Channel info", icon: <Info size={15} aria-hidden="true" />, run: onOpenSettings });
    }
    if (onToggleAllThreads) {
      menuItems.push({ label: "Threads", icon: <MessagesSquare size={15} aria-hidden="true" />, run: onToggleAllThreads });
    }
    if (onToggleSearch) {
      menuItems.push({ label: "Search", icon: <Search size={15} aria-hidden="true" />, run: onToggleSearch });
    }
    if (channel && channel.type !== "dm" && onOpenSettings) {
      // The channel settings panel is where the member list lives.
      menuItems.push({ label: "Members", icon: <Users size={15} aria-hidden="true" />, run: onOpenSettings });
    }
    return (
      <>
        <AttachmentsBar
          items={pendingAttachments}
          onRemove={onRemoveAttachment}
          onRetry={onRetryAttachment}
        />
        <div
          className="px-[10px] pt-1 shrink-0"
          style={{ paddingBottom: `max(env(safe-area-inset-bottom), ${keyboardInset}px, 8px)` }}
        >
          <div
            data-testid="mobile-composer"
            className={`relative rounded-[22px] border border-shell-border bg-shell-surface backdrop-blur-xl shadow-lg ${
              isArchived ? "opacity-50" : ""
            }`}
          >
            {overlays}
            {/* context row */}
            <div className="flex items-center gap-3 px-3.5 py-2.5 border-b border-shell-border">
              {party.kind === "group" ? (
                <div className="flex -space-x-2 shrink-0" aria-hidden="true">
                  {members.slice(0, 3).map((m) => {
                    const ag = liveAgents?.find((a) => a.name === m);
                    return (
                      <div key={m} className="rounded-[11px] ring-2 ring-shell-bg">
                        <MessageAvatar
                          size={22}
                          authorId={m}
                          displayName={ag?.display_name || m}
                          kind={ag ? "agent" : "user"}
                          emoji={ag ? resolveAgentEmoji(ag.emoji, ag.framework) : undefined}
                        />
                      </div>
                    );
                  })}
                </div>
              ) : party.kind === "agent" && party.agent ? (
                <div className="shrink-0">
                  <MessageAvatar
                    size={22}
                    authorId={party.agent.name}
                    displayName={party.title}
                    kind="agent"
                    emoji={resolveAgentEmoji(party.agent.emoji, party.agent.framework)}
                  />
                </div>
              ) : null}
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold leading-tight truncate text-shell-text">
                  {party.title}
                </div>
                {party.subtitle && (
                  <div className="text-[12px] leading-tight truncate text-shell-text-tertiary">
                    {party.subtitle}
                  </div>
                )}
              </div>
              {menuItems.length > 0 && (
                <div className="relative shrink-0" ref={menuWrapRef}>
                  <button
                    type="button"
                    onClick={() => {
                      setEmojiOpen(false);
                      setMenuOpen((o) => !o);
                    }}
                    aria-label="More options"
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    className="h-9 w-9 rounded-full flex items-center justify-center text-shell-text-secondary hover:bg-shell-surface-hover"
                  >
                    <MoreHorizontal size={18} aria-hidden="true" />
                  </button>
                  {menuOpen && (
                    <div
                      role="menu"
                      aria-label="Chat options"
                      className="absolute right-0 bottom-full mb-2 min-w-[180px] rounded-xl border border-shell-border-strong bg-shell-bg shadow-xl py-1 z-30"
                    >
                      {menuItems.map((it) => (
                        <button
                          key={it.label}
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setMenuOpen(false);
                            it.run();
                          }}
                          className="w-full flex items-center gap-2.5 px-3 py-2.5 text-[14px] text-left text-shell-text hover:bg-shell-surface-hover"
                        >
                          {it.icon}
                          {it.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            {/* input row */}
            <div className="flex items-end gap-2 px-2.5 py-2.5">
              <button
                type="button"
                onClick={onFileUpload}
                disabled={isArchived}
                aria-label="Add attachment"
                className="h-10 w-10 shrink-0 rounded-full flex items-center justify-center bg-shell-bg text-shell-text-secondary disabled:opacity-50"
              >
                <Plus size={20} aria-hidden="true" />
              </button>
              <div className="flex-1 min-w-0 flex items-end rounded-[22px] bg-shell-bg pl-4 pr-1.5 min-h-10">
                <Textarea
                  ref={textareaRef}
                  value={value}
                  onChange={(e) => !isArchived && onChange(e.target.value)}
                  onKeyDown={(e) => !isArchived && handleKeyDown(e)}
                  onBlur={() => onDismissMention()}
                  onPaste={onPaste}
                  placeholder={placeholder}
                  rows={1}
                  disabled={isArchived}
                  className="flex-1 bg-transparent border-0 px-0 py-2.5 min-h-0 text-[15px] focus-visible:ring-0 focus-visible:border-0 max-h-[120px] disabled:cursor-not-allowed"
                  aria-label="Message input"
                />
                <div className="relative shrink-0" ref={emojiWrapRef}>
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      setMenuOpen(false);
                      setEmojiOpen((o) => !o);
                    }}
                    disabled={isArchived}
                    aria-label="Insert emoji"
                    aria-haspopup="dialog"
                    aria-expanded={emojiOpen}
                    className="h-10 w-9 flex items-center justify-center text-shell-text-secondary disabled:opacity-50"
                  >
                    <Smile size={19} aria-hidden="true" />
                  </button>
                  {emojiOpen && (
                    <div
                      role="dialog"
                      aria-label="Emoji picker"
                      className="absolute right-0 bottom-full mb-2 z-30"
                    >
                      <Picker
                        theme={Theme.DARK}
                        width={300}
                        height={360}
                        onEmojiClick={(d) => insertEmoji(d.emoji)}
                      />
                    </div>
                  )}
                </div>
              </div>
              {hasContent && (
                <button
                  type="button"
                  onClick={onSend}
                  disabled={!canSend}
                  aria-label="Send message"
                  className="h-10 w-10 shrink-0 rounded-full flex items-center justify-center bg-accent text-white disabled:opacity-50"
                >
                  <ArrowUp size={20} strokeWidth={2.5} aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
        </div>
      </>
    );
  }

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
          {overlays}
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
