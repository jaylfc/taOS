import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Hash,
  Users,
  AtSign,
  Bot,
  MessageCircle,
  ChevronDown,
  MessagesSquare,
  Search,
  PanelRight,
  Check,
  CheckCheck,
  Globe,
  Pencil,
  Copy,
  X,
} from "lucide-react";
import Picker, { Theme } from "emoji-picker-react";
import { Button } from "@/components/ui";
import { MessageAvatar } from "./MessageAvatar";
import { MessageHoverActions } from "./MessageHoverActions";
import { MessageEditor } from "./MessageEditor";
import { MessageTombstone } from "./MessageTombstone";
import { ThreadIndicator } from "./ThreadIndicator";
import { AttachmentGallery } from "./AttachmentGallery";
import { PinBadge } from "./PinBadge";
import { PinnedMessagesPopover, type PinnedMessage } from "./PinnedMessagesPopover";
import { TypingFooter, type AgentTyping } from "./TypingFooter";
import { PinRequestAffordance } from "./PinRequestAffordance";
import { ReactionBar } from "./ReactionBar";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { startDrag, endDrag } from "@/shell/dnd/dnd-bus";
import { renderContent, dayLabel, relativeTime, toMs, resolveAuthorDisplayState } from "../MessagesApp";
import type { ContentBlock } from "../MessagesApp";
import { copyText } from "@/lib/clipboard";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";
import type { Receipt } from "@/lib/a2a-receipts-api";
import { displayAuthor, isOwnMessage } from "./format-author";
import type { LiveAgent, ArchivedAgentEntry, Channel } from "./types";

const EMOJI_PICKER = ["👍", "❤️", "😂", "🎉", "🤔", "👀", "🚀", "✅"];

/** Per-person name colours for mobile bubbles; readable on dark surfaces. */
const NAME_COLORS = [
  "hsl(28 72% 68%)",
  "hsl(200 72% 68%)",
  "hsl(100 72% 68%)",
  "hsl(330 72% 68%)",
  "hsl(260 72% 68%)",
  "hsl(50 72% 68%)",
  "hsl(170 72% 68%)",
  "hsl(5 72% 68%)",
];

function nameColorFor(authorId: string): string {
  let hash = 0;
  for (let i = 0; i < authorId.length; i++) {
    hash = (hash * 31 + authorId.charCodeAt(i)) | 0;
  }
  return NAME_COLORS[Math.abs(hash) % NAME_COLORS.length] as string;
}

const RUN_GAP_MS = 5 * 60 * 1000;

type ReceiptTickState = "sent" | "delivered" | "seen";

function computeReceiptTick(
  isOwn: boolean,
  currentUserId: string | null,
  channelMembers: string[] | undefined,
  receipts: Receipt[],
): ReceiptTickState {
  if (!isOwn) return "sent";
  const addressees = (channelMembers ?? []).filter((m) => m !== currentUserId && m !== "user");
  if (addressees.length === 0) return "sent";
  const receiptMap = new Map(receipts.map((r) => [r.agent_id, r]));
  const allHaveRow = addressees.every((a) => receiptMap.has(a));
  if (!allHaveRow) return "sent";
  const allSeen = addressees.every((a) => receiptMap.get(a)?.seen_at != null);
  return allSeen ? "seen" : "delivered";
}

function ReceiptTick({ state, seenAt }: { state: ReceiptTickState; seenAt?: number }) {
  const ariaLabel = state === "sent" ? "Sent" : state === "delivered" ? "Delivered" : "Seen";
  const title =
    state === "seen" && seenAt != null
      ? `Seen ${new Date(seenAt * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`
      : undefined;
  if (state === "sent") {
    return <Check size={12} aria-label={ariaLabel} className="inline-block" />;
  }
  return (
    <CheckCheck
      size={12}
      aria-label={ariaLabel}
      className={`inline-block ${state === "seen" ? "text-accent" : "text-shell-text-tertiary"}`}
    >
      {title ? <title>{title}</title> : null}
    </CheckCheck>
  );
}

export interface MessageRow {
  id: string;
  channel_id: string;
  author_id: string;
  author_type: "user" | "agent";
  content: string;
  thread_id?: string;
  content_type?: "text" | "canvas" | string;
  content_blocks?: ContentBlock[];
  metadata?: {
    canvas_id?: string;
    canvas_url?: string;
    canvas_title?: string;
    pin_requested?: boolean;
    [key: string]: unknown;
  };
  state?: "pending" | "streaming" | "complete" | "error";
  created_at: number | string;
  reactions?: Record<string, string[]>;
  edited_at?: number | string;
  deleted_at?: number | null;
  ephemeral?: number | boolean;
  delivered_at?: number | null;
  attachments?: AttachmentRecord[];
  reply_count?: number;
  last_reply_at?: number | null;
  /** A2A per-recipient receipts loaded via GET /a2a/messages/{id}/receipts. */
  receipts?: Receipt[];
}

export interface MessageListProps {
  /** Messages to render. */
  messages: MessageRow[];
  /** ID of the channel whose messages were last fetched. */
  fetchedChannel: string | null;
  /** Current channel. */
  channel: Channel | undefined;
  /** Current selected channel id. */
  selectedChannel: string | null;
  /** Whether mobile layout is active. */
  isMobile: boolean;
  /** Skip the channel header block (the parent supplies its own, e.g. mobile). Default false. */
  hideHeader?: boolean;
  /** Keyboard inset for mobile safe-area handling. */
  keyboardInset: number;
  /** Current time for relative timestamps. */
  nowMs: number;

  /* ---- author resolution ---- */
  liveAgents: LiveAgent[];
  archivedAgents: ArchivedAgentEntry[];
  currentUserId: string | null;
  currentUserDisplayName: string | null;

  /* ---- pinned messages ---- */
  pinnedMessages: PinnedMessage[];
  pinnedPopoverOpen: boolean;
  onTogglePinnedPopover: () => void;

  /* ---- editing ---- */
  editingMessageId: string | null;
  onCancelEdit: () => void;
  onSaveEdit: (msgId: string, content: string) => void;
  /** Start editing the last completed agent reply in place. */
  onEditAgentReply?: (msgId: string) => void;
  /** Error message when saving an edit fails. */
  editError?: string | null;

  /* ---- reactions ---- */
  onToggleReaction: (messageId: string, emoji: string) => void;
  showEmoji: { messageId: string; rect: DOMRect } | null;
  onShowEmoji: (v: { messageId: string; rect: DOMRect } | null) => void;

  /* ---- hover / overflow ---- */
  hoveredMessageId: string | null;
  onHoverMessage: (id: string | null) => void;
  onReplyInThread: (channelId: string, parentId: string) => void;
  onOverflow: (e: React.MouseEvent, messageId: string) => void;

  /* ---- thread indicator ---- */
  onOpenThread: (channelId: string, parentId: string) => void;

  /* ---- pin request ---- */
  onApprovePinRequest: (messageId: string) => void;

  /* ---- canvas ---- */
  onViewCanvas: (v: { url: string; title?: string } | null) => void;

  /* ---- new divider ---- */
  newDividerAtId: string | null;

  /* ---- scroll-to-bottom ---- */
  atBottom: boolean;
  newCount: number;
  onScrollToLatest: () => void;
  onScroll: (e: React.UIEvent<HTMLDivElement>) => void;

  /* ---- drop target ---- */
  dropTarget: {
    isOver: boolean;
    isValidTarget: boolean;
    handlers: {
      onDragEnter: (e: React.DragEvent) => void;
      onDragOver: (e: React.DragEvent) => void;
      onDragLeave: (e: React.DragEvent) => void;
      onDrop: (e: React.DragEvent) => void;
    };
  };

  /* ---- thread/view toggles ---- */
  showAllThreads: boolean;
  onToggleAllThreads: () => void;
  showSearch: boolean;
  onToggleSearch: () => void;
  onOpenSettings: () => void;

  /* ---- typing ---- */
  typingHumans: string[];
  typingAgents: AgentTyping[];
  /** A2A receipts keyed by message id. Used to render read-receipt ticks. */
  receipts: Record<string, Receipt[]>;
}

export interface MessageListHandle {
  scrollToBottom: () => void;
}

export const MessageList = forwardRef<MessageListHandle, MessageListProps>(function MessageList({
  messages,
  fetchedChannel,
  channel,
  selectedChannel,
  isMobile,
  hideHeader = false,
  keyboardInset,
  nowMs,
  liveAgents,
  archivedAgents,
  currentUserId,
  currentUserDisplayName,
  pinnedMessages,
  pinnedPopoverOpen,
  onTogglePinnedPopover,
  editingMessageId,
  onCancelEdit,
  onSaveEdit,
  onEditAgentReply,
  editError,
  onToggleReaction,
  showEmoji,
  onShowEmoji,
  hoveredMessageId,
  onHoverMessage,
  onReplyInThread,
  onOverflow,
  onOpenThread,
  onApprovePinRequest,
  onViewCanvas,
  newDividerAtId,
  atBottom,
  newCount,
  onScrollToLatest,
  onScroll,
  dropTarget,
  showAllThreads,
  onToggleAllThreads,
  showSearch,
  onToggleSearch,
  onOpenSettings,
  typingHumans,
  typingAgents,
  receipts,
}: MessageListProps, ref: React.Ref<MessageListHandle>) {
  const messageListRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  /* ---- mobile selection (long-press / context menu / keyboard) ---- */
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const pressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pressStartRef = useRef<{ x: number; y: number } | null>(null);
  const lastLongPressAtRef = useRef(0);

  const clearPressTimer = () => {
    if (pressTimerRef.current) {
      clearTimeout(pressTimerRef.current);
      pressTimerRef.current = null;
    }
    pressStartRef.current = null;
  };

  useEffect(() => clearPressTimer, []);

  // Selection only exists on mobile; drop it when the mode or channel changes.
  useEffect(() => {
    if (!isMobile) setSelectedMessageId(null);
  }, [isMobile]);
  useEffect(() => {
    setSelectedMessageId(null);
  }, [selectedChannel]);

  // Tapping anywhere outside the selected row (and outside the emoji popover)
  // clears the selection. Registered only while something is selected.
  useEffect(() => {
    if (!selectedMessageId) return;
    const onDocPointerDown = (e: Event) => {
      const target = e.target as Element | null;
      if (target?.closest?.("[data-emoji-popover]")) return;
      const row = document.querySelector(
        `[data-message-id="${selectedMessageId}"]`,
      );
      if (row && target && row.contains(target)) return;
      setSelectedMessageId(null);
    };
    document.addEventListener("pointerdown", onDocPointerDown);
    return () => document.removeEventListener("pointerdown", onDocPointerDown);
  }, [selectedMessageId]);

  const toggleSelected = (id: string) =>
    setSelectedMessageId((prev) => (prev === id ? null : id));

  const LONG_PRESS_MS = 450;
  const PRESS_SLOP_PX = 8;
  const INTERACTIVE = "a, button, [role=button], img, video";

  /** Pointer / keyboard handlers that implement mobile selection for one row. */
  const mobileRowHandlers = (id: string) => ({
    tabIndex: 0,
    onPointerDown: (e: React.PointerEvent) => {
      if ((e.target as Element).closest?.(INTERACTIVE)) return;
      clearPressTimer();
      pressStartRef.current = { x: e.clientX ?? 0, y: e.clientY ?? 0 };
      pressTimerRef.current = setTimeout(() => {
        pressTimerRef.current = null;
        lastLongPressAtRef.current = Date.now();
        toggleSelected(id);
      }, LONG_PRESS_MS);
    },
    onPointerMove: (e: React.PointerEvent) => {
      const start = pressStartRef.current;
      if (!start) return;
      if (Math.hypot((e.clientX ?? 0) - start.x, (e.clientY ?? 0) - start.y) > PRESS_SLOP_PX) {
        clearPressTimer();
      }
    },
    onPointerUp: clearPressTimer,
    onPointerCancel: clearPressTimer,
    onContextMenu: (e: React.MouseEvent) => {
      if ((e.target as Element).closest?.(INTERACTIVE)) return;
      e.preventDefault();
      // Android fires contextmenu right after our own long-press; do not toggle twice.
      if (Date.now() - lastLongPressAtRef.current < 800) return;
      clearPressTimer();
      toggleSelected(id);
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === "Enter" || e.key === "ContextMenu") {
        e.preventDefault();
        toggleSelected(id);
      } else if (e.key === "Escape") {
        setSelectedMessageId(null);
      }
    },
  });

  useImperativeHandle(ref, () => ({
    scrollToBottom: () => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    },
  }), []);

  // Only the most recent non-ephemeral agent reply of each thread (top level keyed "") is
  // editable, and only once it has finished streaming; the server applies
  // the same per-thread rule and refuses anything else.
  const lastAgentByThread = new Map<string, string>();
  for (const m of messages) {
    if (m.author_type === "agent" && !m.deleted_at && !m.ephemeral) {
      lastAgentByThread.set(m.thread_id ?? "", m.id);
    }
  }

  const isDm = channel?.type === "dm" || channel?.type === "dm-remote";

  /** True when `b` does not continue the sender run that `a` belongs to. */
  const breaksRun = (a: MessageRow | undefined, b: MessageRow | undefined) => {
    if (!a || !b) return true;
    if (a.author_id !== b.author_id || a.author_type !== b.author_type) return true;
    const am = toMs(a.created_at);
    const bm = toMs(b.created_at);
    if (new Date(am).toDateString() !== new Date(bm).toDateString()) return true;
    return Math.abs(bm - am) > RUN_GAP_MS;
  };

  // The one receipt line on mobile lives under the last own message only.
  let lastOwnIndex = -1;
  if (isMobile) {
    for (let k = messages.length - 1; k >= 0; k--) {
      const m = messages[k] as MessageRow;
      if (isOwnMessage(m, currentUserId)) {
        lastOwnIndex = k;
        break;
      }
    }
  }

  if (!selectedChannel) {
    return (
      <div className="flex-1 flex items-center justify-center text-shell-text-tertiary">
        <div className="text-center px-6">
          <MessageCircle size={48} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm mb-3">Pick a channel or start a DM</p>
        </div>
      </div>
    );
  }

  const pinnedPopover = (
    <PinnedMessagesPopover
      pins={pinnedMessages}
      authorCtx={{ currentUserId, currentUserDisplayName }}
      onJumpTo={(id) => {
        onTogglePinnedPopover();
        const el = document.querySelector(
          `[data-message-id="${id}"]`,
        ) as HTMLElement | null;
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("data-highlight");
          setTimeout(() => el.classList.remove("data-highlight"), 2000);
        }
      }}
      onClose={onTogglePinnedPopover}
    />
  );

  return (
    <>
      {/* With hideHeader the parent owns the header; keep the pinned popover reachable. */}
      {hideHeader && pinnedPopoverOpen && (
        <div className="relative h-0 shrink-0 self-end mr-2 z-40">{pinnedPopover}</div>
      )}
      {/* channel header */}
      {!hideHeader && (
      <div className="px-4 py-2.5 border-b border-shell-border flex items-center gap-3 shrink-0">
        {channel?.type === "topic" ? (
          <Hash size={16} className="text-shell-text-tertiary" />
        ) : channel?.type === "group" ? (
          <Users size={16} className="text-shell-text-tertiary" />
        ) : channel?.type === "dm-remote" ? (
          <Globe size={16} className="text-shell-text-tertiary" />
        ) : (
          <AtSign size={16} className="text-shell-text-tertiary" />
        )}
        {/* DM agent emoji */}
        {channel?.type === "dm" &&
          (() => {
            const agentName = (channel.members ?? []).find((m) => m !== "user");
            if (!agentName) return null;
            const agent = liveAgents.find((a) => a.name === agentName);
            if (!agent) return null;
            return (
              <span className="text-base leading-none shrink-0" aria-hidden="true">
                {resolveAgentEmoji(agent.emoji, agent.framework)}
              </span>
            );
          })()}
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium truncate flex items-center gap-1">
            {channel?.name ?? "Unknown"}
            {channel && channel.type !== "dm" && (
              <button
                aria-label="Channel settings"
                onClick={onOpenSettings}
                className="ml-1 opacity-60 hover:opacity-100"
              >
                ⓘ
              </button>
            )}
            <a
              aria-label="Open chat guide"
              href="https://github.com/jaylfc/tinyagentos/blob/master/docs/chat-guide.md"
              target="_blank"
              rel="noreferrer"
              className="ml-1 opacity-60 hover:opacity-100 text-[12px]"
            >
              ?
            </a>
            <div className="relative">
              <PinBadge
                count={pinnedMessages.length}
                onClick={onTogglePinnedPopover}
              />
              {pinnedPopoverOpen && pinnedPopover}
            </div>
            <button
              type="button"
              onClick={onToggleAllThreads}
              className="ml-2 p-1 rounded hover:bg-shell-border-strong text-shell-text-secondary hover:text-shell-text"
              aria-label={showAllThreads ? "Hide all threads" : "Show all threads"}
              aria-expanded={showAllThreads}
              aria-controls="all-threads-panel"
              title="All threads"
            >
              <MessagesSquare size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={onToggleSearch}
              className="ml-2 p-1 rounded hover:bg-shell-border-strong text-shell-text-secondary hover:text-shell-text"
              aria-label={showSearch ? "Hide search" : "Search messages"}
              aria-expanded={showSearch}
              aria-controls="search-panel"
              title="Search"
            >
              <Search size={14} aria-hidden="true" />
            </button>
        </div>
        {channel?.topic || channel?.description ? (
          <div className="text-[11px] text-shell-text-tertiary truncate">
            {channel.topic || channel.description}
          </div>
        ) : null}
        </div>
        {channel?.members && (
          <div className="text-[11px] text-shell-text-tertiary flex items-center gap-1">
            <Users size={12} /> {channel.members.length}
          </div>
        )}
      </div>
      )}

      {/* message list */}
      <div
        ref={messageListRef}
        onScroll={onScroll}
        className={`flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-0.5 select-text message-list-drop-target ${
          dropTarget.isOver
            ? "ring-2 ring-accent/60 ring-inset bg-accent/5"
            : dropTarget.isValidTarget
              ? "ring-2 ring-accent/30 ring-inset"
              : ""
        }`}
        style={
          isMobile && keyboardInset > 0
            ? { paddingBottom: `${keyboardInset + 60}px` }
            : undefined
        }
        onDragEnter={dropTarget.handlers.onDragEnter}
        onDragOver={(e) => {
          dropTarget.handlers.onDragOver(e);
          if (!e.defaultPrevented) e.preventDefault();
        }}
        onDragLeave={dropTarget.handlers.onDragLeave}
        onDrop={dropTarget.handlers.onDrop}
      >
        {/* scroll-to-bottom affordance */}
        {selectedChannel && !atBottom && (
          <button
            type="button"
            onClick={onScrollToLatest}
            aria-label="Jump to latest"
            className="absolute right-4 bottom-24 z-20 flex items-center gap-1.5 px-3 h-9 rounded-full bg-shell-surface-active border border-shell-border-strong text-shell-text/80 hover:text-shell-text shadow-lg hover:bg-shell-surface-hover backdrop-blur-xl transition-colors"
          >
            <ChevronDown size={16} aria-hidden="true" />
            {newCount > 0 && (
              <span className="text-[11px] font-semibold">{newCount} new</span>
            )}
          </button>
        )}

        {messages.length === 0 && fetchedChannel === selectedChannel && (
          <div className="flex flex-col items-center justify-center h-full text-shell-text-hint text-center px-6">
            <MessageCircle size={40} className="mb-3 opacity-30" />
            <p className="text-sm">
              No messages yet. Say hello to{" "}
              {channel?.type === "dm" || channel?.type === "dm-remote"
                ? `@${(channel.members ?? []).find((m) => m !== "user") ?? "them"}`
                : channel?.name
                  ? `#${channel.name}`
                  : "this channel"}
              .
            </p>
          </div>
        )}

        {messages.map((msg, i) => {
          const isAgent = msg.author_type === "agent";
          const prev = i > 0 ? messages[i - 1] : undefined;
          const showAuthor = !prev || prev.author_id !== msg.author_id;
          const prevDay = prev
            ? new Date(toMs(prev.created_at)).toDateString()
            : null;
          const currDay = new Date(toMs(msg.created_at)).toDateString();
          const showDaySeparator = !prev || prevDay !== currDay;
          const authorState = resolveAuthorDisplayState(
            msg.author_id,
            msg.author_type,
            liveAgents,
            archivedAgents,
          );
          const isDeadAgent = isAgent && authorState !== "active";
          const authorTooltip =
            authorState === "archived"
              ? "Agent no longer active"
              : authorState === "removed"
                ? "Agent removed"
                : undefined;

          const own = isOwnMessage(msg, currentUserId);
          const showActions = isMobile
            ? selectedMessageId === msg.id
            : hoveredMessageId === msg.id;
          const canvasBlock = msg.content_type === "canvas" &&
                    (msg.metadata?.canvas_url || msg.metadata?.canvas_id) && (
                      <div className="mt-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            const url =
                              msg.metadata?.canvas_url ??
                              `/canvas/${msg.metadata?.canvas_id}`;
                            onViewCanvas({
                              url,
                              title: msg.metadata
                                ?.canvas_title as string | undefined,
                            });
                          }}
                          className="h-7 px-2.5 text-[12px] gap-1.5 bg-shell-surface border-shell-border-strong hover:bg-shell-surface-hover"
                          aria-label="View canvas"
                        >
                          <PanelRight size={13} />
                          View Canvas
                          {msg.metadata?.canvas_title
                            ? `: ${msg.metadata.canvas_title}`
                            : ""}
                        </Button>
                      </div>
                    );
          const hoverActions = showActions &&
                    (() => {
                      const excerpt = (msg.content || "").slice(0, 80);
                      const msgChannelId =
                        msg.channel_id ?? selectedChannel ?? "";
                      return (
                        <div className="absolute -top-3 right-2 z-10">
                          <MessageHoverActions
                            onReact={() => {
                              if (
                                showEmoji &&
                                showEmoji.messageId === msg.id
                              ) {
                                onShowEmoji(null);
                                return;
                              }
                              const row = document.querySelector(
                                `[data-message-id="${msg.id}"]`,
                              ) as HTMLElement | null;
                              const rect = row?.getBoundingClientRect();
                              if (!rect) return;
                              onShowEmoji({
                                messageId: msg.id,
                                rect,
                              });
                            }}
                            onReplyInThread={() => {
                              setSelectedMessageId(null);
                              onReplyInThread(
                                msg.channel_id ?? selectedChannel ?? "",
                                msg.id,
                              );
                            }}
                            onOverflow={(e) => {
                              e.preventDefault();
                              onOverflow(e, msg.id);
                            }}
                            dragHandle={
                              msgChannelId && !isMobile ? (
                                <span
                                  draggable
                                  onDragStart={(e) => {
                                    e.stopPropagation();
                                    e.dataTransfer.effectAllowed =
                                      "copy";
                                    try {
                                      e.dataTransfer.setData(
                                        "text/plain",
                                        `@${msg.author_id}: ${excerpt}`,
                                      );
                                      e.dataTransfer.setData(
                                        "text/uri-list",
                                        `${window.location.origin}/chat/${msgChannelId}?msg=${msg.id}`,
                                      );
                                    } catch {
                                      /* best-effort */
                                    }
                                    startDrag({
                                      kind: "message",
                                      channel_id: msgChannelId,
                                      message_id: msg.id,
                                      author_id: msg.author_id,
                                      excerpt,
                                    });
                                  }}
                                  onDragEnd={() => endDrag()}
                                  className="p-1 opacity-40 hover:opacity-100 cursor-grab select-none"
                                  aria-label="Drag message"
                                  title="Drag this message"
                                >
                                  &#8942;&#8942;
                                </span>
                              ) : undefined
                            }
                          />
                        </div>
                      );
                    })();
          const emojiPortal = showEmoji &&
                    showEmoji.messageId === msg.id &&
                    createPortal(
                      (() => {
                        const POPOVER_W = 300;
                        const POPOVER_H = 360;
                        const vw = window.innerWidth;
                        const vh = window.innerHeight;
                        const r = showEmoji.rect;
                        const top = Math.max(
                          8,
                          Math.min(r.top, Math.max(8, vh - POPOVER_H - 8)),
                        );
                        const left = Math.max(
                          8,
                          Math.min(
                            r.right - POPOVER_W,
                            Math.max(8, vw - POPOVER_W - 8),
                          ),
                        );
                        return (
                          <div
                            data-emoji-popover="1"
                            role="dialog"
                            aria-label="Emoji reactions"
                            className="fixed z-50 bg-shell-bg border border-shell-border-strong rounded-lg shadow-xl p-2 w-[300px] h-[360px] flex flex-col gap-2 backdrop-blur-xl"
                            style={{ top, left }}
                          >
                            <div className="flex gap-1 shrink-0">
                              {EMOJI_PICKER.map((em) => (
                                <button
                                  key={em}
                                  onClick={() =>
                                    onToggleReaction(msg.id, em)
                                  }
                                  className="text-lg hover:bg-shell-border-strong rounded p-0.5 transition-colors"
                                >
                                  {em}
                                </button>
                              ))}
                            </div>
                            <div className="flex-1 min-h-0">
                              <Picker
                                theme={Theme.DARK}
                                width="100%"
                                height="100%"
                                onEmojiClick={(d) => {
                                  onToggleReaction(msg.id, d.emoji);
                                }}
                              />
                            </div>
                          </div>
                        );
                      })(),
                      document.body,
                    );

          if (isMobile) {
            const next = i < messages.length - 1 ? messages[i + 1] : undefined;
            const firstOfRun = breaksRun(prev, msg);
            const lastOfRun = breaksRun(msg, next);
            const nameText = displayAuthor(msg, { currentUserId, currentUserDisplayName });
            const showNameRow = !own && firstOfRun && (!isDm || isDeadAgent);
            const selected = selectedMessageId === msg.id;
            const settled = msg.state !== "pending" && msg.state !== "streaming";
            const showAgentFooter =
              isAgent &&
              !!msg.content &&
              settled &&
              !msg.deleted_at &&
              editingMessageId !== msg.id;
            const canEditReply =
              !!onEditAgentReply &&
              msg.id === lastAgentByThread.get(msg.thread_id ?? "") &&
              (msg.state ?? "complete") === "complete" &&
              !msg.content_blocks?.length;
            const hasReactions = !!msg.reactions && Object.keys(msg.reactions).length > 0;

            let receiptLine: React.ReactNode = null;
            if (i === lastOwnIndex && settled && !msg.deleted_at) {
              let label: string | null = null;
              let seen = false;
              if (channel?.type === "dm-remote") {
                if (msg.delivered_at) label = "Delivered";
              } else {
                const list = msg.receipts ?? receipts[msg.id] ?? [];
                const tick = computeReceiptTick(own, currentUserId, channel?.members, list);
                if (tick === "seen") {
                  const seenAt = list.find(
                    (r) =>
                      (channel?.members ?? []).includes(r.agent_id) &&
                      r.agent_id !== currentUserId &&
                      r.agent_id !== "user",
                  )?.seen_at;
                  seen = true;
                  label =
                    seenAt != null
                      ? `Read ${new Date(seenAt * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
                      : "Read";
                } else if (tick === "delivered") {
                  label = "Delivered";
                }
              }
              if (label) {
                receiptLine = (
                  <div
                    data-receipt-line="true"
                    className="mt-1 flex items-center justify-end gap-1 text-[11px] text-shell-text-tertiary select-none"
                  >
                    {seen && <CheckCheck size={12} aria-hidden="true" />}
                    <span>{label}</span>
                  </div>
                );
              }
            }

            const bubbleRadius = own
              ? `rounded-[18px] ${lastOfRun ? "rounded-br-[6px]" : ""}`
              : `rounded-[18px] ${lastOfRun ? "rounded-bl-[6px]" : ""}`;
            const bubbleTone = own
              ? "bg-accent text-shell-bg-deep"
              : "bg-shell-surface border border-shell-border/60 text-shell-text";

            return (
              <React.Fragment key={msg.id}>
                {showDaySeparator && (
                  <div className="flex justify-center my-3 select-none">
                    <span className="text-[11px] text-shell-text-tertiary font-medium">
                      {dayLabel(msg.created_at)}
                    </span>
                  </div>
                )}
                {newDividerAtId === msg.id && (
                  <div
                    role="separator"
                    aria-label="New messages"
                    className="flex items-center gap-3 my-3 select-none"
                  >
                    <div className="flex-1 h-px bg-accent/25" />
                    <span className="text-[11px] text-accent-strong font-semibold">New</span>
                    <div className="flex-1 h-px bg-accent/25" />
                  </div>
                )}
                <div
                  data-message-id={msg.id}
                  data-own={own ? "true" : "false"}
                  className={`relative flex items-end gap-2 px-1 select-none outline-none ${
                    own ? "justify-end" : "justify-start"
                  } ${firstOfRun ? "mt-2.5" : ""} ${hasReactions ? "mb-3" : ""}`}
                  title={new Date(toMs(msg.created_at)).toLocaleString()}
                  {...mobileRowHandlers(msg.id)}
                >
                  {!own && (
                    <div className="w-7 shrink-0">
                      {lastOfRun && (
                        <MessageAvatar
                          size={28}
                          variant="tile"
                          authorId={msg.author_id}
                          displayName={nameText}
                          kind={isAgent ? "agent" : "user"}
                          dead={isDeadAgent}
                          emoji={
                            isAgent
                              ? resolveAgentEmoji(
                                  liveAgents.find((a) => a.name === msg.author_id)?.emoji,
                                  liveAgents.find((a) => a.name === msg.author_id)?.framework,
                                )
                              : undefined
                          }
                        />
                      )}
                    </div>
                  )}
                  <div
                    className={`flex flex-col min-w-0 max-w-[78%] ${own ? "items-end" : "items-start"}`}
                  >
                    {showNameRow && (
                      <div className="flex items-baseline gap-1.5 mb-0.5 px-1">
                        <span
                          className={`text-[12px] font-semibold ${isDeadAgent ? "line-through" : ""}`}
                          style={{
                            color: isDeadAgent ? undefined : nameColorFor(msg.author_id),
                            opacity: isDeadAgent ? 0.55 : undefined,
                          }}
                          title={authorTooltip}
                        >
                          {nameText}
                        </span>
                        {isAgent && !isDeadAgent && !isDm && (
                          <span className="text-[10px] text-shell-text-tertiary">agent</span>
                        )}
                        {isDeadAgent && (
                          <span className="text-[10px] text-shell-text-tertiary">
                            {authorState === "archived" ? "inactive" : "removed"}
                          </span>
                        )}
                      </div>
                    )}
                    <div
                      className={`relative max-w-full ${bubbleRadius} ${bubbleTone} px-3 py-1.5 ${
                        selected ? "ring-1 ring-accent-line" : ""
                      }`}
                    >
                      {msg.deleted_at ? (
                        <MessageTombstone />
                      ) : editingMessageId === msg.id ? (
                        <MessageEditor
                          initial={msg.content}
                          onSave={(content) => onSaveEdit(msg.id, content)}
                          onCancel={onCancelEdit}
                          error={editError}
                        />
                      ) : (
                        <>
                          <div
                            className={`text-[15px] leading-[1.4] whitespace-pre-wrap break-words ${
                              isDeadAgent ? "opacity-80" : ""
                            }`}
                          >
                            {renderContent(msg.content, msg.content_blocks)}
                            {msg.state === "pending" && <span className="ml-1 opacity-70">...</span>}
                            {msg.state === "streaming" && (
                              <span className="ml-1 inline-flex gap-0.5">
                                <span className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:0ms]" />
                                <span className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:150ms]" />
                                <span className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:300ms]" />
                              </span>
                            )}
                            {msg.state === "error" && (
                              <span className="ml-1 text-red-400 text-[11px]">(error)</span>
                            )}
                            {msg.edited_at && (
                              <span className="ml-1.5 text-[10px] opacity-70">(edited)</span>
                            )}
                          </div>
                          {showAgentFooter && (
                            <div className="mt-1.5 pt-1 border-t border-shell-border/60 flex items-center gap-2 text-[11px] text-shell-text-tertiary">
                              <CopyButton
                                content={msg.content}
                                icon
                                className="p-1 -ml-1 rounded text-shell-text-tertiary hover:text-shell-text select-none"
                              />
                              {canEditReply && (
                                <button
                                  type="button"
                                  onClick={() => onEditAgentReply?.(msg.id)}
                                  aria-label="Edit reply"
                                  title="Edit reply"
                                  className="p-1 rounded text-shell-text-tertiary hover:text-shell-text select-none"
                                >
                                  <Pencil size={13} aria-hidden="true" />
                                </button>
                              )}
                            </div>
                          )}
                        </>
                      )}
                      {hasReactions && (
                        <ReactionBar
                          compact
                          side={own ? "left" : "right"}
                          reactions={msg.reactions as Record<string, string[]>}
                          messageId={msg.id}
                          currentUserId={currentUserId}
                          onToggle={onToggleReaction}
                        />
                      )}
                    </div>
                    {msg.metadata?.pin_requested && msg.author_type === "agent" && (
                      <PinRequestAffordance
                        authorId={msg.author_id}
                        onApprove={() => onApprovePinRequest(msg.id)}
                      />
                    )}
                    {canvasBlock}
                    <AttachmentGallery attachments={msg.attachments || []} />
                    {typeof msg.reply_count === "number" && msg.reply_count > 0 && (
                      <ThreadIndicator
                        replyCount={msg.reply_count}
                        lastReplyAt={msg.last_reply_at ?? null}
                        onOpen={() =>
                          onOpenThread(msg.channel_id ?? selectedChannel ?? "", msg.id)
                        }
                      />
                    )}
                    {receiptLine}
                    {emojiPortal}
                  </div>
                  {hoverActions}
                </div>
              </React.Fragment>
            );
          }

          return (
            <React.Fragment key={msg.id}>
              {showDaySeparator && (
                <div className="flex items-center gap-3 my-4 select-none">
                  <div className="flex-1 h-px bg-shell-border-strong" />
                  <span className="text-[11px] text-shell-text-tertiary font-medium">
                    {dayLabel(msg.created_at)}
                  </span>
                  <div className="flex-1 h-px bg-shell-border-strong" />
                </div>
              )}
              {newDividerAtId === msg.id && (
                <div
                  role="separator"
                  aria-label="New messages"
                  className="flex items-center gap-3 my-3 select-none"
                >
                  <div className="flex-1 h-px bg-red-400/40" />
                  <span className="text-[11px] text-red-400 font-semibold">
                    New
                  </span>
                  <div className="flex-1 h-px bg-red-400/40" />
                </div>
              )}
              <div
                data-message-id={msg.id}
                className={`group relative flex gap-2.5 px-3 py-0.5 rounded-md transition-colors hover:bg-shell-surface ${
                  showAuthor ? (isMobile ? "mt-2" : "mt-3") : ""
                }`}
                onMouseEnter={() => onHoverMessage(msg.id)}
                onMouseLeave={() => onHoverMessage(null)}
                {...(isMobile ? mobileRowHandlers(msg.id) : {})}
              >
                {/* avatar gutter */}
                <div
                  className="flex-shrink-0 flex justify-end pt-0.5"
                  style={{ width: isMobile ? 34 : 38 }}
                >
                  {showAuthor ? (
                    (() => {
                      const agent = isAgent
                        ? liveAgents.find((a) => a.name === msg.author_id)
                        : undefined;
                      return (
                        <MessageAvatar
                          size={isMobile ? 34 : 38}
                          authorId={msg.author_id}
                          displayName={displayAuthor(msg, {
                            currentUserId,
                            currentUserDisplayName,
                          })}
                          kind={isAgent ? "agent" : "user"}
                          dead={isDeadAgent}
                          emoji={
                            agent
                              ? resolveAgentEmoji(agent.emoji, agent.framework)
                              : isAgent
                                ? resolveAgentEmoji(undefined, undefined)
                                : undefined
                          }
                        />
                      );
                    })()
                  ) : (
                    <span
                      className="text-[10px] leading-none text-shell-text-tertiary opacity-0 group-hover:opacity-100 transition-opacity self-center select-none"
                      aria-hidden="true"
                      title={new Date(toMs(msg.created_at)).toLocaleString()}
                    >
                      {new Date(toMs(msg.created_at)).toLocaleTimeString(
                        undefined,
                        { hour: "2-digit", minute: "2-digit" },
                      )}
                    </span>
                  )}
                </div>
                {/* content column */}
                <div className="flex-1 min-w-0">
                  {showAuthor && (
                    <div className="flex items-center gap-2 mb-0.5">
                      <span
                        className={`${isMobile ? "text-[14px]" : "text-[15px]"} font-bold tracking-tight ${
                          isDeadAgent
                            ? "line-through text-shell-text-tertiary"
                            : "text-shell-text"
                        }`}
                        style={isDeadAgent ? { opacity: 0.55 } : undefined}
                        title={authorTooltip}
                      >
                        {displayAuthor(msg, {
                          currentUserId,
                          currentUserDisplayName,
                        })}
                      </span>
                      {isAgent && !isDeadAgent && (
                        <span className="text-[10px] uppercase tracking-wide bg-accent-soft text-accent-strong border border-accent-line px-1.5 py-0.5 rounded font-semibold flex items-center gap-0.5">
                          <Bot size={10} aria-hidden="true" /> Agent
                        </span>
                      )}
                      {isDeadAgent && (
                        <span className="text-[10px] uppercase tracking-wide bg-shell-surface-active text-shell-text-tertiary px-1.5 py-0.5 rounded font-semibold flex items-center gap-0.5">
                          <Bot size={10} aria-hidden="true" />
                          {authorState === "archived"
                            ? "inactive"
                            : "removed"}
                        </span>
                      )}
                      <span
                        className="text-[11px] text-shell-text-tertiary"
                        title={new Date(
                          toMs(msg.created_at),
                        ).toLocaleString()}
                      >
                        {relativeTime(msg.created_at, nowMs)}
                      </span>
                      {msg.edited_at && (
                        <span className="text-[10px] text-shell-text-tertiary">
                          (edited)
                        </span>
                      )}
                    </div>
                  )}
                  {msg.deleted_at ? (
                    <MessageTombstone />
                  ) : editingMessageId === msg.id ? (
                    <MessageEditor
                      initial={msg.content}
                      onSave={(content) => onSaveEdit(msg.id, content)}
                      onCancel={onCancelEdit}
                      error={editError}
                    />
                  ) : (
                    <div className="relative">
                      {msg.content &&
                        msg.author_type === "agent" &&
                        msg.state !== "streaming" &&
                        msg.state !== "pending" && (
                          <CopyButton
                            content={msg.content}
                            className="absolute -top-1 right-0 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 bg-shell-surface border border-shell-border-strong text-shell-text-secondary hover:text-shell-text transition-opacity select-none z-10"
                          />
                        )}
                      {onEditAgentReply &&
                        msg.id === lastAgentByThread.get(msg.thread_id ?? "") &&
                        (msg.state ?? "complete") === "complete" &&
                        !msg.content_blocks?.length && (
                          <button
                            type="button"
                            onClick={() => onEditAgentReply(msg.id)}
                            aria-label="Edit reply"
                            title="Edit reply"
                            className="absolute -top-1 right-7 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 bg-shell-surface border border-shell-border-strong text-shell-text-secondary hover:text-shell-text transition-opacity select-none z-10"
                          >
                            <Pencil size={14} aria-hidden="true" />
                          </button>
                        )}
                      <div
                        className={`${isMobile ? "text-[14px]" : "text-[15px]"} leading-[1.46] whitespace-pre-wrap break-words select-text ${
                          isDeadAgent
                            ? "text-shell-text-secondary"
                            : "text-shell-text"
                        }`}
                      >
                        {renderContent(msg.content, msg.content_blocks)}
                        {msg.state === "pending" && (
                          <span className="ml-1 text-shell-text-tertiary">
                            ...
                          </span>
                        )}
                        {msg.state === "streaming" && (
                          <span className="ml-1 inline-flex gap-0.5">
                            <span className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:0ms]" />
                            <span className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:150ms]" />
                            <span className="w-1 h-1 bg-accent rounded-full animate-bounce [animation-delay:300ms]" />
                          </span>
                        )}
                        {msg.state === "error" && (
                          <span className="ml-1 text-red-400 text-[11px]">
                            (error)
                          </span>
                        )}
                        {channel?.type === "dm-remote" &&
                          own &&
                          !["pending", "streaming"].includes(msg.state ?? "") && (
                            <span className="ml-1 text-shell-text-tertiary inline-flex items-center">
                              {msg.delivered_at ? (
                                <CheckCheck size={12} aria-hidden="true" />
                              ) : (
                                <Check size={12} aria-hidden="true" />
                              )}
                            </span>
                          )}
                        {own &&
                          channel?.type !== "dm-remote" &&
                          msg.state !== "pending" &&
                          msg.state !== "streaming" &&
                          (() => {
                            const tickState = computeReceiptTick(
                              own,
                              currentUserId,
                              channel?.members,
                              msg.receipts ?? receipts[msg.id] ?? [],
                            );
                            const seenAt = tickState === "seen"
                              ? (msg.receipts ?? receipts[msg.id] ?? []).find(
                                  (r) => (channel?.members ?? []).includes(r.agent_id) && r.agent_id !== currentUserId && r.agent_id !== "user",
                                )?.seen_at ?? undefined
                              : undefined;
                            return (
                              <span className="ml-1 inline-flex items-center">
                                <ReceiptTick state={tickState} seenAt={seenAt} />
                              </span>
                            );
                          })()}
                      </div>
                    </div>
                  )}
                  {msg.metadata?.pin_requested &&
                    msg.author_type === "agent" && (
                      <PinRequestAffordance
                        authorId={msg.author_id}
                        onApprove={() => onApprovePinRequest(msg.id)}
                      />
                    )}

                  {canvasBlock}

                  {/* reactions */}
                  {msg.reactions &&
                    Object.keys(msg.reactions).length > 0 && (
                      <ReactionBar
                        reactions={msg.reactions}
                        messageId={msg.id}
                        currentUserId={currentUserId}
                        onToggle={onToggleReaction}
                      />
                    )}

                  {hoverActions}
                  <AttachmentGallery attachments={msg.attachments || []} />
                  {typeof msg.reply_count === "number" &&
                    msg.reply_count > 0 && (
                      <ThreadIndicator
                        replyCount={msg.reply_count}
                        lastReplyAt={msg.last_reply_at ?? null}
                        onOpen={() =>
                          onOpenThread(
                            msg.channel_id ?? selectedChannel ?? "",
                            msg.id,
                          )
                        }
                      />
                    )}

                  {emojiPortal}
                </div>
              </div>
            </React.Fragment>
          );
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* typing footer */}
      <TypingFooter
        humans={typingHumans}
        agents={typingAgents}
        selfId="user"
      />
    </>
  );
});

/* ------------------------------------------------------------------ */
/*  CopyButton (hover copy affordance)                                 */
/* ------------------------------------------------------------------ */

function CopyButton({
  content,
  className,
  icon = false,
}: {
  content: string;
  className?: string;
  /** Render lucide icons instead of the emoji glyphs (mobile bubble footer). */
  icon?: boolean;
}) {
  const [copied, setCopied] = React.useState(false);
  const [clipError, setClipError] = React.useState(false);
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  React.useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    };
  }, []);

  const handleCopy = async () => {
    if (!content) return;
    const ok = await copyText(content);
    if (ok) {
      setCopied(true);
      setClipError(false);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 1500);
    } else {
      setClipError(true);
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
      errorTimerRef.current = setTimeout(() => setClipError(false), 2500);
    }
  };

  return (
    <button
      onClick={handleCopy}
      aria-label={
        copied
          ? "Copied"
          : clipError
            ? "Copy failed — check clipboard permissions"
            : "Copy message"
      }
      className={className}
    >
      {icon ? (
        copied ? (
          <Check size={14} aria-hidden="true" />
        ) : clipError ? (
          <X size={14} aria-hidden="true" className="text-red-400" />
        ) : (
          <Copy size={14} aria-hidden="true" />
        )
      ) : copied ? (
        <span>✓</span>
      ) : clipError ? (
        <span className="text-red-400">✕</span>
      ) : (
        <span>📋</span>
      )}
    </button>
  );
}
