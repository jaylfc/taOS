import React, { forwardRef, useImperativeHandle, useRef } from "react";
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
import type { AttachmentRecord } from "@/lib/chat-attachments-api";
import { displayAuthor } from "./format-author";
import type { LiveAgent, ArchivedAgentEntry, Channel } from "./types";

const EMOJI_PICKER = ["👍", "❤️", "😂", "🎉", "🤔", "👀", "🚀", "✅"];

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
  attachments?: AttachmentRecord[];
  reply_count?: number;
  last_reply_at?: number | null;
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
}: MessageListProps, ref: React.Ref<MessageListHandle>) {
  const messageListRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    scrollToBottom: () => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    },
  }), []);

  if (!selectedChannel) {
    return (
      <div className="flex-1 flex items-center justify-center text-white/20">
        <div className="text-center px-6">
          <MessageCircle size={48} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm mb-3">Pick a channel or start a DM</p>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* channel header */}
      <div className="px-4 py-2.5 border-b border-white/[0.06] flex items-center gap-3 shrink-0">
        {channel?.type === "topic" ? (
          <Hash size={16} className="text-white/40" />
        ) : channel?.type === "group" ? (
          <Users size={16} className="text-white/40" />
        ) : (
          <AtSign size={16} className="text-white/40" />
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
              {pinnedPopoverOpen && (
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
              )}
            </div>
            <button
              type="button"
              onClick={onToggleAllThreads}
              className="ml-2 p-1 rounded hover:bg-white/10 text-white/60 hover:text-white"
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
              className="ml-2 p-1 rounded hover:bg-white/10 text-white/60 hover:text-white"
              aria-label={showSearch ? "Hide search" : "Search messages"}
              aria-expanded={showSearch}
              aria-controls="search-panel"
              title="Search"
            >
              <Search size={14} aria-hidden="true" />
            </button>
          </div>
          {channel?.description && (
            <div className="text-[11px] text-white/35 truncate">
              {channel.description}
            </div>
          )}
        </div>
        {channel?.members && (
          <div className="text-[11px] text-white/30 flex items-center gap-1">
            <Users size={12} /> {channel.members.length}
          </div>
        )}
      </div>

      {/* message list */}
      <div
        ref={messageListRef}
        onScroll={onScroll}
        className={`flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-0.5 select-text message-list-drop-target ${
          dropTarget.isOver
            ? "ring-2 ring-sky-400/60 ring-inset bg-sky-500/5"
            : dropTarget.isValidTarget
              ? "ring-2 ring-sky-400/30 ring-inset"
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
          <div className="flex flex-col items-center justify-center h-full text-white/25 text-center px-6">
            <MessageCircle size={40} className="mb-3 opacity-30" />
            <p className="text-sm">
              No messages yet. Say hello to{" "}
              {channel?.type === "dm"
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

          return (
            <React.Fragment key={msg.id}>
              {showDaySeparator && (
                <div className="flex items-center gap-3 my-4 select-none">
                  <div className="flex-1 h-px bg-white/10" />
                  <span className="text-[11px] text-white/40 font-medium">
                    {dayLabel(msg.created_at)}
                  </span>
                  <div className="flex-1 h-px bg-white/10" />
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
                    />
                  ) : (
                    <div className="relative">
                      {msg.content &&
                        msg.author_type === "agent" &&
                        msg.state !== "streaming" &&
                        msg.state !== "pending" && (
                          <CopyButton
                            content={msg.content}
                            className="absolute -top-1 right-0 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 bg-shell-surface border border-white/10 text-shell-text-secondary hover:text-shell-text transition-opacity select-none z-10"
                          />
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

                  {/* canvas attachment */}
                  {msg.content_type === "canvas" &&
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
                    )}

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

                  {/* hover actions */}
                  {(isMobile || hoveredMessageId === msg.id) &&
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
                            onReplyInThread={() =>
                              onReplyInThread(
                                msg.channel_id ?? selectedChannel ?? "",
                                msg.id,
                              )
                            }
                            onOverflow={(e) => {
                              e.preventDefault();
                              onOverflow(e, msg.id);
                            }}
                            dragHandle={
                              msgChannelId ? (
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
                    })()}
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

                  {/* emoji picker portal */}
                  {showEmoji &&
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
                                  className="text-lg hover:bg-white/10 rounded p-0.5 transition-colors"
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
                    )}
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
}: {
  content: string;
  className?: string;
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
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setClipError(false);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
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
      {copied ? (
        <span>✓</span>
      ) : clipError ? (
        <span className="text-red-400">✕</span>
      ) : (
        <span>📋</span>
      )}
    </button>
  );
}
