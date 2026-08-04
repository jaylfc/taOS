import React, { useState, useEffect, useRef, useCallback, useId } from "react";
import {
  MessageCircle,
  Hash,
  Users,
  Plus,
  X,
  AtSign,
  ChevronDown,
  PanelRight,
  Archive,
  CircleDot,
  PauseCircle,
  AlertTriangle,
  Loader2,
} from "lucide-react";
import {
  Button,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Input,
  Label,
} from "@/components/ui";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { useVisualViewport } from "@/hooks/use-visual-viewport";
import { useDropTarget } from "@/shell/dnd/use-drop-target";
import { ChannelSettingsPanel } from "./chat/ChannelSettingsPanel";
import { AgentContextMenu } from "./chat/AgentContextMenu";
import { type SlashCommandsBySlug } from "./chat/SlashMenu";
import { type AgentTyping } from "./chat/TypingFooter";
import { useTypingEmitter } from "@/lib/use-typing-emitter";
import { ThreadPanel } from "./chat/ThreadPanel";
import { type PendingAttachment } from "./chat/AttachmentsBar";
import { uploadDiskFile, attachmentFromPath, type AttachmentRecord } from "@/lib/chat-attachments-api";
import { useThreadPanel } from "@/lib/use-thread-panel";
import { openFilePicker } from "@/shell/file-picker-api";
import { MessageOverflowMenu } from "./chat/MessageOverflowMenu";
import { BottomSheet } from "@/shell/BottomSheet";
import { type PinnedMessage } from "./chat/PinnedMessagesPopover";
import { AllThreadsList } from "./chat/AllThreadsList";
import { ChannelSwitcher } from "./chat/ChannelSwitcher";
import { useChatNotifications } from "./chat/useChatNotifications";
import { MessageInput } from "./chat/MessageInput";
import { MessageList, type MessageListHandle } from "./chat/MessageList";
import {
  pinMessage, unpinMessage, listPins,
  editMessage as apiEditMessage, deleteMessage as apiDeleteMessage,
  markUnread as apiMarkUnread,
} from "@/lib/chat-messages-api";
import { projectsApi, type Project } from "@/lib/projects";
import {
  findA2aChannelId,
  readLastChannel,
  writeLastChannel,
} from "./MessagesApp.a2aSelection";
import { bucketAgentChannels } from "./MessagesApp.agentSections";
import {
  pickWatchAgent,
  computeStallInfo,
  type StallWatch,
} from "./MessagesApp.stallWatch";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { CodeBlock } from "@/components/CodeBlock";
import { ToolCallBlock } from "@/components/ToolCallBlock";
import { StatusBlock } from "@/components/StatusBlock";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SearchPanel } from "./chat/SearchPanel";
import { ChannelSidebar } from "./chat/ChannelSidebar";
import { A2aBusMessageView, useBusChannels } from "./chat/A2aBusPanel";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AdminPrompt {
  name: string;
  body: string;
  description?: string;
}

interface OpenMessagesDetail {
  channelId: string;
  prefillPromptName?: string;
  prefillAgent?: string;
}

interface Channel {
  id: string;
  name: string;
  type: "dm" | "topic" | "group";
  description?: string;
  topic?: string;
  members?: string[];
  created_at?: string;
  last_message_at?: string;
  lastPreview?: string;
  project_id?: string;
  settings?: {
    archived?: boolean;
    archived_at?: string;
    archived_agent_id?: string;
    archived_agent_slug?: string;
    muted?: string[];
    kind?: string;
    taostalk_agent?: string;
  };
}

interface LiveAgent {
  name: string;
  display_name?: string;
  emoji?: string;
  framework?: string;
  model?: string;
  status?: string;
}

interface ArchivedAgentEntry {
  id: string;
  archived_slug: string;
  original?: {
    name?: string;
    display_name?: string;
  };
}

/** Resolved display state for a message author. */
export type AuthorDisplayState = "active" | "archived" | "removed";

/**
 * Resolve the display state of a message author.
 * Pure function — exported for unit testing.
 */
export function resolveAuthorDisplayState(
  authorId: string,
  authorType: "user" | "agent",
  liveAgents: LiveAgent[],
  archivedAgents: ArchivedAgentEntry[],
): AuthorDisplayState {
  if (authorType === "user") return "active";
  // Check live agents by name
  if (liveAgents.some((a) => a.name === authorId)) return "active";
  // Check archived agents by slug or original name
  if (
    archivedAgents.some(
      (a) =>
        a.archived_slug === authorId ||
        a.original?.name === authorId,
    )
  )
    return "archived";
  return "removed";
}

interface TextContentBlock {
  kind: "text";
  text: string;
}

interface ThinkingContentBlock {
  kind: "thinking";
  text: string;
  collapsed?: boolean;
}

export interface ToolCallContentBlock {
  kind: "tool_call";
  call_id: string;
  name: string;
  input_preview?: string;
  status: "running" | "done" | "error";
  result_preview?: string;
}

export interface StatusContentBlock {
  kind: "status";
  text: string;
}

export interface QuestionContentBlock {
  kind: "question";
  text: string;
  options?: string[];
}

/**
 * Structured message content for taOStalk session turns.
 * Known kinds are handled by dedicated block components (separate cards);
 * any unrecognized kind falls through to the unknown-block fallback in
 * renderContent, which is the slice-2 seam.
 */
export type ContentBlock =
  | TextContentBlock
  | ThinkingContentBlock
  | ToolCallContentBlock
  | StatusContentBlock
  | QuestionContentBlock
  | { kind: string; [key: string]: unknown };

interface Message {
  id: string;
  channel_id: string;
  author_id: string;
  author_type: "user" | "agent";
  content: string;
  /** Parent message id when this message is a thread reply. */
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
  // Server uses Python time.time() — Unix epoch in seconds. The runtime
  // value is a number; the type was historically annotated string and
  // fed straight into Date() (which expects ms), so every chat message
  // rendered as 21/01/1970. Pass through toMs() before instantiating.
  created_at: number | string;
  reactions?: Record<string, string[]>;
  edited_at?: number | string;
  deleted_at?: number | null;
  attachments?: AttachmentRecord[];
  reply_count?: number;
  last_reply_at?: number | null;
}

type WsStatus = "connecting" | "connected" | "disconnected";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/**
 * Coerce a server timestamp (number = seconds since epoch, string = ISO
 * or numeric) to milliseconds suitable for `new Date(...)`. The 1e12
 * threshold safely distinguishes seconds (~1.7e9 today) from ms (~1.7e12).
 */
export function toMs(ts: number | string): number {
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  if (ts === "" || ts == null) return Date.now();
  const n = Number(ts);
  if (!Number.isNaN(n)) return n < 1e12 ? n * 1000 : n;
  return new Date(ts).getTime();
}

export function relativeTime(ts: number | string, nowMs: number = Date.now()): string {
  const ms = toMs(ts);
  const mins = Math.floor((nowMs - ms) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  // Older than an hour: show the clock time. The day context comes from the
  // date separators rendered between message groups.
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * Dispatch a single content block to its renderer. All four slice-1 kinds now
 * have dedicated components: text and thinking (cards 3+4), tool call and
 * status/question (cards 5+6). Any unrecognised kind still falls through to
 * the unknown-block fallback -- the slice-2 seam for the renderer registry.
 */
function renderContentBlock(block: ContentBlock, index: number): React.ReactElement {
  switch (block.kind) {
    case "text": {
      const textBlock = block as TextContentBlock;
      return <TextBlock block={textBlock} index={index} key={`block-${index}`} />;
    }
    case "thinking": {
      const thinkingBlock = block as ThinkingContentBlock;
      return <ThinkingBlock block={thinkingBlock} index={index} key={`block-${index}`} />;
    }
    case "tool_call":
      return (
        <ToolCallBlock block={block as ToolCallContentBlock} key={`block-${index}`} />
      );
    case "status":
      return (
        <StatusBlock block={block as StatusContentBlock} key={`block-${index}`} />
      );
    case "question":
      return (
        <StatusBlock block={block as QuestionContentBlock} key={`block-${index}`} />
      );
    default:
      return (
        <div key={`block-${index}`} className="text-shell-text-tertiary text-[12px]">
          unsupported block: {block.kind}
        </div>
      );
  }
}

export function renderContent(text: string, content_blocks?: ContentBlock[]) {
  if (content_blocks && content_blocks.length > 0) {
    return content_blocks.map((block, i) => renderContentBlock(block, i));
  }
  // Split on fenced code blocks first, then apply inline markdown to non-code segments.
  const result: (string | React.ReactElement)[] = [];
  const fenceRegex = /```(?:[^\n]*)?\n([\s\S]*?)```/g;
  let lastFence = 0;
  let fenceMatch: RegExpExecArray | null;
  let seg = 0;

  // Each segment gets a distinct key prefix so keys can never collide no
  // matter how many inline elements one segment produces.
  while ((fenceMatch = fenceRegex.exec(text)) !== null) {
    if (fenceMatch.index > lastFence) {
      result.push(...renderInline(text.slice(lastFence, fenceMatch.index), `s${seg++}`));
    }
    result.push(<CodeBlock key={`cb-${seg++}`} code={fenceMatch[1] ?? ""} />);
    lastFence = fenceMatch.index + fenceMatch[0].length;
  }
  if (lastFence < text.length) {
    result.push(...renderInline(text.slice(lastFence), `s${seg}`));
  }
  return result;
}

export function renderInline(text: string, keyPrefix: string) {
  return [
    <div key={keyPrefix}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        disallowedElements={["img"]}
        components={{
          p: ({ node, ...props }) => <p className="mb-1 last:mb-0" {...props} />,
          a: ({ node, ...props }) => (
            <a className="text-blue-400 underline" target="_blank" rel="noopener noreferrer" {...props} />
          ),
          code: ({ node, className, children, ...props }) => {
            const isBlock = typeof className === "string" && /language-/.test(className);
            if (isBlock) {
              return <code className={className} {...props}>{children}</code>;
            }
            return (
              <code className="bg-shell-surface-active px-1.5 py-0.5 rounded text-[13px] font-mono" {...props}>
                {children}
              </code>
            );
          },
          ul: ({ node, ...props }) => <ul className="list-disc pl-5 mb-1" {...props} />,
          ol: ({ node, ...props }) => <ol className="list-decimal pl-5 mb-1" {...props} />,
          blockquote: ({ node, ...props }) => (
            <blockquote className="border-l-2 border-shell-border pl-3 text-shell-text-secondary" {...props} />
          ),
          pre: ({ node, ...props }) => (
            <pre className="my-2 overflow-x-auto max-w-full bg-shell-bg-deep border border-shell-border rounded p-2 text-[13px]" {...props} />
          ),
          table: ({ node, ...props }) => (
            <div className="my-2 overflow-x-auto">
              <table className="min-w-full text-left text-[13px]" {...props} />
            </div>
          ),
          th: ({ node, ...props }) => (
            <th className="border-b border-shell-border px-2 py-1 font-semibold" {...props} />
          ),
          td: ({ node, ...props }) => (
            <td className="border-b border-shell-border px-2 py-1 align-top" {...props} />
          ),
          h1: ({ node, ...props }) => <p className="font-semibold mb-1" {...props} />,
          h2: ({ node, ...props }) => <p className="font-semibold mb-1" {...props} />,
          h3: ({ node, ...props }) => <p className="font-semibold mb-1" {...props} />,
          h4: ({ node, ...props }) => <p className="font-semibold mb-1" {...props} />,
          h5: ({ node, ...props }) => <p className="font-semibold mb-1" {...props} />,
          h6: ({ node, ...props }) => <p className="font-semibold mb-1" {...props} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>,
  ];
}

/* ------------------------------------------------------------------ */
/*  Content block renderers (taOStalk session turns)                 */
/* ------------------------------------------------------------------ */

/**
 * TextBlock -- renders a {kind:"text"} content block by reusing the existing
 * inline markdown renderer (renderInline), so a text block renders
 * identically to a plain message's markdown body.
 */
export function TextBlock({ block, index }: { block: TextContentBlock; index: number }): React.ReactElement {
  return <>{renderInline(block.text, `text-block-${index}`)}</>;
}

/**
 * ThinkingBlock -- renders a {kind:"thinking"} content block as a
 * collapsed-by-default disclosure. The toggle button carries the ARIA
 * disclosure contract (aria-expanded / aria-controls) and a chevron; the
 * panel is dim-styled to de-emphasize the agent's internal reasoning. The
 * container matches the Store/Images card bar (rounded border, shell
 * surface background, dim tertiary text).
 */
export function ThinkingBlock({ block, index }: { block: ThinkingContentBlock; index: number }): React.ReactElement {
  const [open, setOpen] = useState(block.collapsed === false);
  const summaryRef = useId();
  const contentId = useId();
  const summaryAria = `taostalk-thinking-summary-${summaryRef}`;
  const contentAria = `taostalk-thinking-content-${contentId}`;
  return (
    <div className="rounded-2xl border border-shell-border bg-shell-surface/60 shadow-card overflow-hidden">
      <button
        type="button"
        id={summaryAria}
        aria-expanded={open}
        aria-controls={contentAria}
        aria-label={open ? "Collapse thinking" : "Expand thinking"}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-[12px] font-semibold text-shell-text-tertiary hover:text-shell-text-secondary hover:bg-shell-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      >
        <ChevronDown
          size={14}
          aria-hidden={true}
          className="transition-transform duration-150"
          style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)" }}
        />
        <span>Thinking</span>
      </button>
      <div
        id={contentAria}
        aria-labelledby={summaryAria}
        hidden={!open}
        className="px-3 py-2 text-[13px] text-shell-text-tertiary"
      >
        {renderInline(block.text, `thinking-${index}`)}
      </div>
    </div>
  );
}


// Best-effort per-channel draft storage. Drafts are user input that may
// contain sensitive material; they are kept in localStorage (the same
// mechanism Slack's web client uses) and not synced to the server. Stored
// unencrypted at rest in the browser profile. Users on shared machines
// should clear site data to remove drafts.
const draftKey = (channelId: string) => `taos-chat-draft:${channelId}`;
function loadDraft(channelId: string): string {
  try { return localStorage.getItem(draftKey(channelId)) || ""; } catch { return ""; }
}
function saveDraft(channelId: string, text: string) {
  try {
    if (text) localStorage.setItem(draftKey(channelId), text);
    else localStorage.removeItem(draftKey(channelId));
  } catch { /* storage full or unavailable: drafts are best-effort */ }
}

export function dayLabel(ts: string | number): string {
  const d = new Date(toMs(ts));
  const now = new Date();
  // Compare local calendar days, not UTC. Build local-midnight Dates for
  // both, then divide by 86400000ms. A local day is 23-25 hours across
  // DST, so the division can still produce fractional values; use
  // Math.round so a one-calendar-day difference is reported as exactly
  // 1 day. (A diff of 0.96 days is still a single calendar-day gap
  // before noon, and a diff of 1.04 days is one calendar day after.)
  const localMidnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diffDays = Math.round((localMidnight(now).getTime() - localMidnight(d).getTime()) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

/* ------------------------------------------------------------------ */
/*  MessagesApp                                                        */
/* ------------------------------------------------------------------ */

export function MessagesApp({
  windowId: _windowId,
  title,
  scope,
}: {
  windowId: string;
  title?: string;
  scope?: { projectId?: string };
}) {
  const isMobile = useIsMobile();
  const { keyboardInset } = useVisualViewport();
  const openWindow = useProcessStore((s) => s.openWindow);
  const openAgentsApp = () => {
    const app = getApp("agents");
    if (app) openWindow("agents", app.defaultSize);
  };

  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelsLoaded, setChannelsLoaded] = useState(false);
  const shellFileDropTarget = useDropTarget({
    accept: ["file"],
    onDrop: async (payload) => {
      if (payload.kind !== "file" || !selectedChannel) return;
      const ch = allChannels.find((c) => c.id === selectedChannel);
      if (ch?.settings?.archived) return;
      const id = Math.random().toString(36).slice(2);
      setPendingAttachments((p) => [...p, {
        id, filename: payload.name, size: payload.size, uploading: true,
      }]);
      try {
        const isAgentWs = payload.path.startsWith("/workspaces/agent/");
        const source: "workspace" | "agent-workspace" = isAgentWs ? "agent-workspace" : "workspace";
        const slug = isAgentWs ? payload.path.split("/")[3] : undefined;
        const rec = await attachmentFromPath({ path: payload.path, source, slug });
        setPendingAttachments((p) =>
          p.map((x) => x.id === id ? { ...x, record: rec, uploading: false } : x)
        );
      } catch (e) {
        setPendingAttachments((p) =>
          p.map((x) => x.id === id ? { ...x, uploading: false, error: (e as Error).message } : x)
        );
      }
    },
  });
  const [archivedChannels, setArchivedChannels] = useState<Channel[]>([]);
  const [archivedExpanded, setArchivedExpanded] = useState(false);
  // Collapsible sidebar sections, keyed by section label / project id, persisted.
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem("taos-chat-collapsed") || "{}"); } catch { return {}; }
  });
  const [projectsExpanded, setProjectsExpanded] = useState(true);
  const [projectChannelExpanded, setProjectChannelExpanded] = useState<Record<string, boolean>>({});
  const [liveAgents, setLiveAgents] = useState<LiveAgent[]>([]);
  const [archivedAgents, setArchivedAgents] = useState<ArchivedAgentEntry[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  // External taOSmd coordination bus (read-only). Selecting a bus channel is a
  // separate mode from the internal project channels: when busSelected is set,
  // the detail pane shows the read-only bus viewer instead of the chat panel.
  const [busSelected, setBusSelected] = useState<string | null>(null);
  const bus = useBusChannels();
  const [messages, setMessages] = useState<Message[]>([]);
  const [unread, setUnread] = useState<Record<string, number>>({});
  const unreadRef = useRef<Record<string, number>>({});
  const pendingNewCountRef = useRef(0);
  const [newDividerAtId, setNewDividerAtId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const [showCreate, setShowCreate] = useState(false);
  const [showEmoji, setShowEmoji] = useState<{ messageId: string; rect: DOMRect } | null>(null); // message id + anchor
  const [viewingCanvas, setViewingCanvas] = useState<{ url: string; title?: string } | null>(null);
  const [newChannel, setNewChannel] = useState({ name: "", type: "topic" as "topic" | "group", description: "" });
  const [prefillBanner, setPrefillBanner] = useState<{ promptName: string; agentName?: string } | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ slug: string; x: number; y: number } | null>(null);
  const [agentInfoPopover, setAgentInfoPopover] = useState<
    { slug: string; framework: string; model: string; status: string; x: number; y: number } | null
  >(null);
  const [slashCommands, setSlashCommands] = useState<SlashCommandsBySlug>({});
  const [typingHumans, setTypingHumans] = useState<string[]>([]);
  const [typingAgents, setTypingAgents] = useState<AgentTyping[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  // #1741 stall watchdog. The backend streams a reply via WS deltas, but if a
  // generation stalls (endless model loop, broken stream, or a missing
  // completion event) no further frames arrive and the window looks frozen with
  // no hint. We arm a watch when the user sends to an agent and clear it on
  // completion; `lastActivityAt` is bumped on every inbound frame so the render
  // can surface an escalating "taking longer / may be stalled" banner once
  // activity stops. Fast, healthy responses never trip it.
  const [responseWatch, setResponseWatch] = useState<StallWatch | null>(null);
  const [, bumpStallClock] = useState(0);
  const [hoveredMessageId, setHoveredMessageId] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [overflowMenu, setOverflowMenu] = useState<{ messageId: string; x: number; y: number } | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [pinnedPopoverOpen, setPinnedPopoverOpen] = useState(false);
  const [pinnedMessages, setPinnedMessages] = useState<PinnedMessage[]>([]);
  const [showAllThreads, setShowAllThreads] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [showSwitcher, setShowSwitcher] = useState(false);
  // Which channel's message fetch has completed, so the "empty channel"
  // placeholder only shows after a real fetch (never mid-load or mid-switch).
  const [fetchedChannel, setFetchedChannel] = useState<string | null>(null);
  // Scroll-to-bottom affordance: whether the list is near the bottom, and how
  // many messages have arrived while scrolled away (shown as a badge).
  const [atBottom, setAtBottom] = useState(true);
  const [newCount, setNewCount] = useState(0);
  const prevMsgCountRef = useRef(0);
  // One 60s tick for the whole list so relative timestamps ("3m") stay fresh
  // without a reload. Only sub-hour labels depend on it; cheap re-render.
  const [nowMs, setNowMs] = useState(() => Date.now());
  // @mention autocomplete: the partial after "@" at the cursor + the @ index,
  // or null when not in mention mode. mentionSel is the highlighted candidate.
  const [mention, setMention] = useState<{ partial: string; atIndex: number } | null>(null);
  const [mentionSel, setMentionSel] = useState(0);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [currentUserDisplayName, setCurrentUserDisplayName] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const { openThread, openThreadFor, closeThread } = useThreadPanel();
  // Live thread replies: messages whose thread_id matches the open thread,
  // captured from the main WS so the panel updates without a reopen. The ref
  // lets the (long-lived) WS closure read the current open thread id.
  const [threadLiveReplies, setThreadLiveReplies] = useState<Message[]>([]);
  const openThreadIdRef = useRef<string | null>(null);
  useEffect(() => {
    openThreadIdRef.current = openThread?.parentId ?? null;
    setThreadLiveReplies([]); // reset when the open thread changes or closes
  }, [openThread?.parentId]);

  // Browser notifications for messages in background channels. Refs so the
  // long-lived WS closure reads the current user id + channel list.
  const { notify } = useChatNotifications();
  const currentUserIdRef = useRef<string | null>(null);
  const channelsRef = useRef<Channel[]>([]);
  useEffect(() => { currentUserIdRef.current = currentUserId; }, [currentUserId]);
  useEffect(() => { channelsRef.current = channels; }, [channels]);

  const wsRef = useRef<WebSocket | null>(null);
  const messageListHandleRef = useRef<MessageListHandle>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const typingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTypingSentRef = useRef(0);
  const autoScrollRef = useRef(true);
  const reconnectDelayRef = useRef(1000);
  const prevChannelRef = useRef<string | null>(null);

  /* ---- fetch channels + unread ---- */
  const fetchChannels = useCallback(async () => {
    try {
      const qs = scope?.projectId ? `?project_id=${encodeURIComponent(scope.projectId)}` : "";
      const [chRes, unRes] = await Promise.all([
        fetch(`/api/chat/channels${qs}`),
        fetch("/api/chat/unread"),
      ]);
      if (chRes.ok) {
        const data = await chRes.json();
        setChannels(data.channels ?? []);
      }
      if (unRes.ok) {
        const data = await unRes.json();
        setUnread(data.unread ?? {});
      }
    } catch {
      /* offline */
    } finally {
      setChannelsLoaded(true);
    }
  }, [scope?.projectId]);

  /* ---- fetch archived channels ---- */
  const fetchArchivedChannels = useCallback(async () => {
    try {
      const url = scope?.projectId
        ? `/api/chat/channels?archived=true&project_id=${encodeURIComponent(scope.projectId)}`
        : "/api/chat/channels?archived=true";
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setArchivedChannels(data.channels ?? []);
      }
    } catch {
      /* offline */
    }
  }, [scope?.projectId]);

  /* ---- fetch agent lists for author resolution ---- */
  const fetchAgentLists = useCallback(async () => {
    try {
      const [liveRes, archRes] = await Promise.all([
        fetch("/api/agents"),
        fetch("/api/agents/archived"),
      ]);
      if (liveRes.ok) {
        const ct = liveRes.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await liveRes.json();
          if (Array.isArray(data)) setLiveAgents(data as LiveAgent[]);
        }
      }
      if (archRes.ok) {
        const ct = archRes.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await archRes.json();
          if (Array.isArray(data)) setArchivedAgents(data as ArchivedAgentEntry[]);
        }
      }
    } catch {
      /* offline */
    }
  }, []);

  /* ---- fetch messages for a channel ---- */
  const fetchMessages = useCallback(async (channelId: string) => {
    try {
      const res = await fetch(`/api/chat/channels/${channelId}/messages?limit=50`);
      if (res.ok) {
        const data = await res.json();
        const list: Message[] = data.messages ?? [];
        setMessages(list);
        setFetchedChannel(channelId);
        autoScrollRef.current = true;
        const pending = pendingNewCountRef.current;
        pendingNewCountRef.current = 0;
        if (pending > 0 && list.length > 0) {
          const idx = list.length - pending;
          const atIdx = idx < 0 ? 0 : idx;
          setNewDividerAtId(list[atIdx]?.id ?? null);
        } else {
          setNewDividerAtId(null);
        }
      }
    } catch {
      /* offline */
    }
  }, []);

  /* ---- mark channel read ---- */
  const markRead = useCallback(async (channelId: string) => {
    try {
      await fetch(`/api/chat/channels/${channelId}/mark-read`, { method: "POST" });
      setUnread((u) => { const next = { ...u }; delete next[channelId]; return next; });
    } catch {
      /* ignore */
    }
  }, []);

  /* ---- WebSocket ---- */
  const connectWs = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= 1) return;
    setWsStatus("connecting");
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/chat`);

    ws.onopen = () => {
      setWsStatus("connected");
      reconnectDelayRef.current = 1000;
      // rejoin current channel
      if (prevChannelRef.current) {
        ws.send(JSON.stringify({ type: "join", channel_id: prevChannelRef.current }));
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Phase-2a: typing/thinking events for TypingFooter
        if (data.type === "typing" && data.kind === "human") {
          setTypingHumans((prev) => prev.includes(data.slug) ? prev : [...prev, data.slug]);
          setTimeout(() => setTypingHumans((prev) => prev.filter((s) => s !== data.slug)), 3500);
          return;
        }
        if (data.type === "thinking") {
          // #1741: a thinking frame from the waited-on agent is live activity.
          // Scope to that agent so a concurrent generation by another agent
          // cannot keep a stalled watch fresh.
          setResponseWatch((w) =>
            w && data.slug === w.agent ? { ...w, lastActivityAt: Date.now() } : w,
          );
          if (data.state === "start") {
            setTypingAgents((prev) => {
              const without = prev.filter((a) => a.slug !== data.slug);
              return [...without, { slug: data.slug, phase: data.phase ?? null, detail: data.detail ?? null }];
            });
          } else {
            setTypingAgents((prev) => prev.filter((a) => a.slug !== data.slug));
          }
          return;
        }

        switch (data.type) {
          case "message":
            setMessages((prev) => {
              if (prev.some((m) => m.id === data.id)) return prev;
              return [...prev, data as Message];
            });
            // Live thread updates: if this is a reply in the open thread, feed
            // it to the panel (de-duped by id).
            if (data.thread_id && data.thread_id === openThreadIdRef.current) {
              setThreadLiveReplies((prev) =>
                prev.some((m) => m.id === data.id) ? prev : [...prev, data as Message],
              );
            }
            // bump unread + browser-notify if not the selected channel and not
            // the user's own message.
            if (data.channel_id !== prevChannelRef.current) {
              setUnread((u) => ({ ...u, [data.channel_id]: (u[data.channel_id] ?? 0) + 1 }));
              if (data.author_id && data.author_id !== currentUserIdRef.current) {
                const chName = channelsRef.current.find((c) => c.id === data.channel_id)?.name ?? "a channel";
                notify(`${data.author_id} in #${chName}`, data.content ?? "", () => setSelectedChannel(data.channel_id));
              }
            }
            // #1741: the waited-on agent's reply frame in the watched channel
            // is activity. Scope to that agent (not any non-user author) so a
            // message from another speaker in a group channel cannot mask a
            // real stall, and record the reply id so subsequent deltas (which
            // carry no channel_id) can be matched to this stream.
            setResponseWatch((w) =>
              w && w.channelId === data.channel_id && data.author_id === w.agent
                ? { ...w, lastActivityAt: Date.now(), streamId: data.id }
                : w,
            );
            break;

          case "message_delta":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id
                  ? { ...m, content: m.content + (data.delta ?? ""), state: "streaming" }
                  : m,
              ),
            );
            // #1741: bump only for the reply we are waiting on (matched by the
            // stream id captured above), so a concurrent generation in another
            // open channel cannot keep this watch alive.
            setResponseWatch((w) =>
              w && w.streamId && data.message_id === w.streamId
                ? { ...w, lastActivityAt: Date.now() }
                : w,
            );
            break;

          case "message_state":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id ? { ...m, state: data.state } : m,
              ),
            );
            // #1741: a terminal state on the watched reply means it resolved.
            // Clear only for our stream (or before any stream id was captured,
            // as a defensive fallback), not for an unrelated channel's finish.
            if (data.state === "complete" || data.state === "error") {
              setResponseWatch((w) =>
                w && (!w.streamId || data.message_id === w.streamId) ? null : w,
              );
            }
            break;

          case "typing":
            // Legacy WS typing (agent only) — route into typingAgents for TypingFooter
            // (human typing is handled by the phase-2a branch above)
            if ((data.user_type ?? "user") !== "agent") break;
            setTypingAgents((prev) => {
              const without = prev.filter((a) => a.slug !== data.user_id);
              return [...without, { slug: data.user_id, phase: null, detail: null }];
            });
            setTimeout(() => {
              setTypingAgents((prev) => prev.filter((a) => a.slug !== data.user_id));
            }, 5000);
            break;

          case "reaction_update":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id ? { ...m, reactions: data.reactions } : m,
              ),
            );
            break;

          case "message_edit":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id
                  ? {
                      ...m,
                      ...(data.content !== undefined && { content: data.content }),
                      ...(data.edited_at !== undefined && { edited_at: data.edited_at }),
                      ...(data.metadata !== undefined && { metadata: data.metadata }),
                    }
                  : m,
              ),
            );
            break;

          case "message_delete":
            // Soft delete — keep the row so the UI can render the tombstone.
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id
                  ? { ...m, deleted_at: data.deleted_at ?? Date.now() / 1000 }
                  : m,
              ),
            );
            break;
        }
      } catch {
        /* bad json */
      }
    };

    ws.onclose = () => {
      setWsStatus("disconnected");
      wsRef.current = null;
      // reconnect with backoff
      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(delay * 2, 30000);
      setTimeout(connectWs, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  /* ---- emoji popover: escape and outside click ---- */
  useEffect(() => {
    if (!showEmoji) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setShowEmoji(null);
    }
    function onPointer(e: MouseEvent) {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (t.closest("[data-emoji-popover='1']")) return;
      if (t.closest(`[data-message-id="${showEmoji!.messageId}"]`)) return;
      setShowEmoji(null);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [showEmoji]);

  /* ---- init ---- */
  useEffect(() => {
    fetchChannels();
    fetchArchivedChannels();
    fetchAgentLists();
    connectWs();
    return () => {
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [fetchChannels, fetchArchivedChannels, fetchAgentLists, connectWs]);

  /* ---- keep unreadRef in sync with the unread state without re-running
   * the channel-selection effect (which would re-capture the pending count). ---- */
  useEffect(() => {
    unreadRef.current = unread;
  }, [unread]);

  /* ---- default-select A2A channel on first project visit ----
   * Also runs when the project switches: if the previously selected channel
   * is not in the new project's channel list, it's stale — fall back to
   * the remembered/A2A channel for the new project, or clear the selection.
   */
  useEffect(() => {
    if (!scope?.projectId) return;
    if (channels.length === 0) return;
    const selectedStillVisible =
      !!selectedChannel && channels.some((c) => c.id === selectedChannel);
    if (selectedStillVisible) return;
    const remembered = readLastChannel(scope.projectId);
    if (remembered && channels.some((c) => c.id === remembered)) {
      setSelectedChannel(remembered);
      return;
    }
    const a2aId = findA2aChannelId(channels);
    setSelectedChannel(a2aId ?? null);
  }, [scope?.projectId, channels, selectedChannel]);

  /* ---- persist last-selected channel per project ----
   * Split from the channel-join effect so we only write when we know the
   * current selection actually belongs to the current project — prevents
   * cross-project leakage when the user switches projects mid-flight.
   */
  useEffect(() => {
    if (!scope?.projectId) return;
    if (!selectedChannel) return;
    if (!channels.some((c) => c.id === selectedChannel)) return;
    writeLastChannel(scope.projectId, selectedChannel);
  }, [scope?.projectId, selectedChannel, channels]);

  /* ---- bus / project-channel selection are mutually exclusive ----
   * Modeled as render precedence: while busSelected is set the bus viewer
   * wins, otherwise the project channel shows. Picking a project channel
   * clears busSelected (project view takes over); picking a bus channel keeps
   * selectedChannel intact so returning from the bus restores it.
   */
  useEffect(() => {
    if (selectedChannel) setBusSelected(null);
  }, [selectedChannel]);

  const selectBusChannel = useCallback((channel: string) => {
    setBusSelected(channel);
  }, []);

  /* ---- fetch project list for sidebar grouping (standalone mode only) ---- */
  useEffect(() => {
    if (scope?.projectId) return;
    let cancelled = false;
    projectsApi.list("active").then((p) => { if (!cancelled) setProjects(p); }).catch(() => {});
    return () => { cancelled = true; };
  }, [scope?.projectId]);

  /* ---- fetch current user ---- */
  useEffect(() => {
    fetch("/auth/me")
      .then((r) => r.ok ? r.json() : null)
      .then((u) => {
        if (u?.user?.id) {
          setCurrentUserId(u.user.id);
          setCurrentUserDisplayName(u.user.full_name || u.user.username || u.user.id);
        }
      })
      .catch(() => {});
  }, []);

  /* ---- cross-app open-messages event ---- */
  useEffect(() => {
    // Guard against the component unmounting while an admin-prompt
    // fetch is in flight — without this, setState fires on an
    // unmounted component (React warns and React 18+ may bail out).
    let cancelled = false;
    const handler = async (e: Event) => {
      const detail = (e as CustomEvent<OpenMessagesDetail>).detail;
      if (!detail?.channelId) return;

      // Select the channel — try to match by id or by name (DM channels often use agent name)
      if (cancelled) return;
      setSelectedChannel(detail.channelId);

      // Fetch the admin prompt body if requested
      if (detail.prefillPromptName) {
        try {
          const res = await fetch(
            `/api/admin-prompts/${encodeURIComponent(detail.prefillPromptName)}`,
            { headers: { Accept: "application/json" } }
          );
          if (cancelled) return;
          if (res.ok) {
            const ct = res.headers.get("content-type") ?? "";
            if (ct.includes("application/json")) {
              const data: AdminPrompt = await res.json();
              if (cancelled) return;
              setInput(data.body ?? "");
              setPrefillBanner({
                promptName: detail.prefillPromptName,
                agentName: detail.prefillAgent,
              });
              // Focus composer after a short delay (channel selection renders first)
              setTimeout(() => {
                if (!cancelled) inputRef.current?.focus();
              }, 150);
            }
          }
        } catch {
          /* ignore — user can type manually */
        }
      }
    };

    window.addEventListener("taos:open-messages", handler);
    return () => {
      cancelled = true;
      window.removeEventListener("taos:open-messages", handler);
    };
  }, []);

  /* ---- channel selection ---- */
  useEffect(() => {
    // Persist the draft for the channel we are leaving, regardless of socket
    // state, so a switch while offline still saves the composer's contents.
    if (prevChannelRef.current && prevChannelRef.current !== selectedChannel) {
      saveDraft(prevChannelRef.current, input);
    }
    if (!selectedChannel) {
      // No new channel: clear refs and stop here.
      prevChannelRef.current = null;
      return;
    }
    // leave previous channel (websocket signaling only)
    if (prevChannelRef.current && wsRef.current?.readyState === 1) {
      wsRef.current.send(JSON.stringify({ type: "leave", channel_id: prevChannelRef.current }));
    }
    // load draft for the new channel
    if (prevChannelRef.current !== selectedChannel) {
      setInput(loadDraft(selectedChannel));
      if (inputRef.current) inputRef.current.style.height = "auto";
    }
    prevChannelRef.current = selectedChannel;
    setNewDividerAtId(null);
    // join new
    if (wsRef.current?.readyState === 1) {
      wsRef.current.send(JSON.stringify({ type: "join", channel_id: selectedChannel }));
    }
    // capture unread count before markRead clears it (read via ref so this
    // effect does not re-run when markRead mutates the unread map).
    pendingNewCountRef.current = unreadRef.current[selectedChannel] ?? 0;
    fetchMessages(selectedChannel);
    markRead(selectedChannel);
    setTypingHumans([]);
    setTypingAgents([]);
  }, [selectedChannel, fetchMessages, markRead]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---- deep-link scroll on ?msg=<id> — latch so it fires once per URL ---- */
  const deepLinkSeenRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedChannel || messages.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const msgId = params.get("msg");
    // Validate format: message ids are uuid4().hex[:12] — lowercase hex only.
    // Guards against selector-injection via a crafted URL.
    if (!msgId || !/^[a-zA-Z0-9_-]{1,64}$/.test(msgId)) return;
    const key = `${selectedChannel}:${msgId}`;
    if (deepLinkSeenRef.current === key) return;
    const el = document.querySelector(`[data-message-id="${msgId}"]`) as HTMLElement | null;
    if (el) {
      deepLinkSeenRef.current = key;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("data-highlight");
      setTimeout(() => el.classList.remove("data-highlight"), 2000);
    }
  }, [selectedChannel, messages.length]);

  /* ---- fetch pins when channel changes ---- */
  useEffect(() => {
    if (!selectedChannel) { setPinnedMessages([]); return; }
    listPins(selectedChannel)
      .then((pins) => setPinnedMessages(pins as PinnedMessage[]))
      .catch(() => setPinnedMessages([]));
  }, [selectedChannel]);

  /* ---- fetch slash commands on channel switch ---- */
  useEffect(() => {
    let alive = true;
    fetch("/api/frameworks/slash-commands")
      .then((r) => r.json())
      .then((d) => { if (alive) setSlashCommands(d || {}); })
      .catch(() => {});
    return () => { alive = false; };
  }, [selectedChannel]);

  /* ---- auto-scroll + new-message counter while scrolled away ---- */
  useEffect(() => {
    const delta = messages.length - prevMsgCountRef.current;
    prevMsgCountRef.current = messages.length;
    if (autoScrollRef.current) {
      messageListHandleRef.current?.scrollToBottom();
    } else if (delta > 0) {
      setNewCount((c) => c + delta);
    }
  }, [messages]);

  /* ---- reset scroll affordance on channel switch ---- */
  useEffect(() => {
    setAtBottom(true);
    setNewCount(0);
    prevMsgCountRef.current = 0;
  }, [selectedChannel]);

  /* ---- 60s tick to keep relative timestamps fresh ---- */
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (!el) return;
    const nowAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    autoScrollRef.current = nowAtBottom;
    // Only flip state (avoids re-render storms on every scroll tick).
    setAtBottom((prev) => (prev === nowAtBottom ? prev : nowAtBottom));
    if (nowAtBottom) setNewCount(0);
  };

  const scrollToLatest = () => {
    messageListHandleRef.current?.scrollToBottom();
    autoScrollRef.current = true;
    setAtBottom(true);
    setNewCount(0);
  };

  const toggleSection = (key: string) => {
    setCollapsedSections((s) => {
      const next = { ...s, [key]: !s[key] };
      try { localStorage.setItem("taos-chat-collapsed", JSON.stringify(next)); } catch { /* best-effort */ }
      return next;
    });
  };
  // When a section is collapsed, still surface channels that are unread or
  // currently selected (Slack behavior), so nothing important is hidden.
  const visibleInSection = (items: Channel[], key: string) =>
    collapsedSections[key]
      ? items.filter((ch) => (unread[ch.id] ?? 0) > 0 || ch.id === selectedChannel)
      : items;

  /* ---- typing emitter ---- */
  const emitTyping = useTypingEmitter(selectedChannel, "user");

  /* ---- mutex: settings vs thread panel ---- */
  const handleOpenSettings = () => {
    closeThread();
    setShowAllThreads(false);
    setShowSearch(false);
    setShowSettings(true);
  };
  const handleOpenThreadFor = (channelId: string, parentId: string) => {
    setShowSettings(false);
    setShowAllThreads(false);
    setShowSearch(false);
    openThreadFor(channelId, parentId);
  };

  // Cmd/Ctrl+K opens the quick channel switcher (suppressing the browser
  // default). Idempotent: re-pressing while open does not reset it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setShowSwitcher(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // #1741: drop any active watch when the user switches channels — a stall
  // banner from a previous conversation must not bleed into another.
  useEffect(() => {
    setResponseWatch(null);
  }, [selectedChannel]);

  // #1741: while a watch is armed, tick once a second so the render recomputes
  // elapsed-since-activity and the banner escalates. Only the presence of a
  // watch (not each bump) gates the interval, so healthy streaming does not
  // thrash it.
  useEffect(() => {
    if (!responseWatch) return;
    const id = setInterval(() => bumpStallClock((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [responseWatch !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  // #1741: arm the stall watch after a send if this channel has an agent that
  // is expected to reply — always in a DM with an agent, or when an agent
  // member is @mentioned elsewhere. Human-only channels never arm it.
  const armResponseWatch = (text: string) => {
    if (!currentChannel) return;
    const agent = pickWatchAgent(
      currentChannel,
      text,
      liveAgents.map((a) => a.name),
    );
    if (agent) {
      setResponseWatch({ channelId: currentChannel.id, agent, lastActivityAt: Date.now() });
    }
  };

  /* ---- send message ---- */
  const sendMessage = async () => {
    const text = input.trim();
    if (!text && pendingAttachments.length === 0) return;
    if (!selectedChannel) return;

    // Block send while uploads are in-flight
    if (pendingAttachments.some((a) => a.uploading)) {
      setSendError("waiting for uploads to finish…");
      return;
    }

    const readyAttachments = pendingAttachments
      .filter((a) => a.record && !a.error)
      .map((a) => a.record!);

    if (readyAttachments.length > 0) {
      // HTTP POST for messages with attachments (WS schema doesn't carry them)
      try {
        const r = await fetch("/api/chat/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel_id: selectedChannel,
            author_id: "user",
            author_type: "user",
            content: text,
            content_type: "text",
            attachments: readyAttachments,
          }),
        });
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          setSendError((body as { error?: string }).error || "couldn't send message");
          return;
        }
        setInput("");
        if (selectedChannel) saveDraft(selectedChannel, "");
        setNewDividerAtId(null);
        setPendingAttachments([]);
        if (inputRef.current) inputRef.current.style.height = "auto";
        autoScrollRef.current = true;
        armResponseWatch(text);
        return;
      } catch (e) {
        setSendError((e as Error).message || "send failed");
        return;
      }
    }

    if (!text) return;
    // If slash input, POST via REST. The server handles `/help` in-app and
    // guards bare slash in non-DMs. A 200 with `handled` means the message
    // was fully processed server-side — skip the WS send to avoid double-post.
    if (text.startsWith("/")) {
      try {
        const r = await fetch("/api/chat/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ channel_id: selectedChannel, content: text }),
        });
        if (r.status === 400) {
          const body = await r.json().catch(() => ({}));
          setSendError((body as { error?: string }).error || "couldn't send message");
          return;
        }
        if (r.ok) {
          const body = await r.json().catch(() => ({}));
          if ((body as { handled?: string }).handled) {
            setSendError(null);
            setInput("");
            if (selectedChannel) saveDraft(selectedChannel, "");
            setNewDividerAtId(null);
            autoScrollRef.current = true;
            if (inputRef.current) inputRef.current.style.height = "auto";
            return;
          }
        }
      } catch {
        /* network error — fall through to WS send */
      }
    }
    setSendError(null);
    // WS fallback for plain text messages. If WS is down, POST to /api/chat/messages
    // so the send still lands.
    if (wsRef.current && wsRef.current.readyState === 1) {
      wsRef.current.send(JSON.stringify({ type: "message", channel_id: selectedChannel, content: text }));
    } else {
      try {
        const r = await fetch("/api/chat/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel_id: selectedChannel,
            author_id: "user", author_type: "user",
            content: text, content_type: "text",
          }),
        });
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          setSendError((body as { error?: string }).error || "couldn't send message");
          return;
        }
      } catch (e) {
        setSendError((e as Error).message || "send failed");
        return;
      }
    }
    setInput("");
    if (selectedChannel) saveDraft(selectedChannel, "");
    setNewDividerAtId(null);
    autoScrollRef.current = true;
    if (inputRef.current) inputRef.current.style.height = "auto";
    armResponseWatch(text);
  };

  /* ---- typing indicator ---- */
  const handleInputChange = (val: string) => {
    setInput(val);
    if (selectedChannel) saveDraft(selectedChannel, val);
    // @mention detection: is the cursor inside an @token (no whitespace, the
    // @ at the start or after whitespace)? If so, enter mention mode.
    const pos = inputRef.current?.selectionStart ?? val.length;
    const m = val.slice(0, pos).match(/(?:^|\s)@([^\s@]*)$/);
    const part = m ? (m[1] ?? "") : "";
    setMention(m ? { partial: part, atIndex: pos - part.length - 1 } : null);
    // auto-resize textarea
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
      inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 120) + "px";
    }
    // send typing indicator throttled (every 3s)
    const now = Date.now();
    if (selectedChannel && wsRef.current?.readyState === 1 && now - lastTypingSentRef.current > 3000) {
      wsRef.current.send(JSON.stringify({ type: "typing", channel_id: selectedChannel }));
      lastTypingSentRef.current = now;
    }
    if (typingTimerRef.current) clearTimeout(typingTimerRef.current);
    typingTimerRef.current = setTimeout(() => { lastTypingSentRef.current = 0; }, 4000);
    // emit via hook for phase-2a backend
    emitTyping();
  };

  /* ---- file upload ---- */
  // Re-upload a File-based attachment (used by the retry affordance). Keeps the
  // success/failure state updates identical to a first attempt.
  const uploadFileAttachment = (id: string, file: File) => {
    setPendingAttachments((p) => p.map((x) => (x.id === id ? { ...x, uploading: true, error: undefined } : x)));
    uploadDiskFile(file, selectedChannel ?? undefined)
      .then((rec) => setPendingAttachments((p) => p.map((x) => (x.id === id ? { ...x, record: rec, uploading: false, error: undefined } : x))))
      .catch((err) => setPendingAttachments((p) => p.map((x) => (x.id === id ? { ...x, uploading: false, error: (err as Error).message } : x))));
  };

  const handleFileUpload = async () => {
    const selections = await openFilePicker({
      sources: ["disk", "workspace", "agent-workspace"],
      multi: true,
    });
    for (const sel of selections) {
      const id = Math.random().toString(36).slice(2);
      const filename = sel.source === "disk" ? sel.file.name : sel.path.split("/").pop() || "";
      const size = sel.source === "disk" ? sel.file.size : 0;
      setPendingAttachments((p) => [...p, { id, filename, size, uploading: true, file: sel.source === "disk" ? sel.file : undefined }]);
      try {
        const rec = sel.source === "disk"
          ? await uploadDiskFile(sel.file, selectedChannel ?? undefined)
          : await attachmentFromPath({
              path: sel.path,
              source: sel.source,
              slug: sel.source === "agent-workspace" ? sel.slug : undefined,
            });
        setPendingAttachments((p) =>
          p.map((x) => (x.id === id ? { ...x, record: rec, uploading: false } : x))
        );
      } catch (e) {
        setPendingAttachments((p) =>
          p.map((x) => (x.id === id ? { ...x, uploading: false, error: (e as Error).message } : x))
        );
      }
    }
  };

  /* ---- reaction toggle ---- */
  const toggleReaction = (messageId: string, emoji: string) => {
    if (wsRef.current?.readyState === 1) {
      wsRef.current.send(JSON.stringify({ type: "reaction", message_id: messageId, emoji }));
    }
    setShowEmoji(null);
  };

  /* ---- create channel ---- */
  const createChannel = async () => {
    if (!newChannel.name.trim()) return;
    try {
      const res = await fetch("/api/chat/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newChannel.name.trim(),
          type: newChannel.type,
          description: newChannel.description.trim() || undefined,
          ...(scope?.projectId ? { project_id: scope.projectId } : {}),
        }),
      });
      if (res.ok) {
        const ch = await res.json();
        await fetchChannels();
        setSelectedChannel(ch.id);
        setShowCreate(false);
        setNewChannel({ name: "", type: "topic", description: "" });
      }
    } catch {
      /* ignore */
    }
  };

  /* ---- archived channel actions ---- */
  const handleRestoreArchivedChannel = useCallback(async (channelId: string, channelName: string) => {
    const archivedAgent = archivedChannels.find((c) => c.id === channelId)?.settings?.archived_agent_id;
    if (archivedAgent) {
      // find the archived agent entry
      const agentEntry = archivedAgents.find((a) => a.id === archivedAgent);
      if (agentEntry) {
        if (!window.confirm(`Restore agent "${agentEntry.original?.display_name || agentEntry.original?.name || agentEntry.archived_slug}"?`)) return;
        try {
          const res = await fetch(`/api/agents/archived/${archivedAgent}/restore`, { method: "POST" });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            window.alert(`Restore failed: ${(err as { error?: string }).error ?? res.status}`);
            return;
          }
          await fetchChannels();
          await fetchArchivedChannels();
          await fetchAgentLists();
        } catch (e) {
          window.alert(`Network error: ${String(e)}`);
        }
      } else {
        window.alert("Agent entry missing — delete only.");
      }
    } else {
      window.alert(`Cannot restore channel "${channelName}": no associated agent found.`);
    }
  }, [archivedChannels, archivedAgents, fetchChannels, fetchArchivedChannels, fetchAgentLists]);

  const handleDeleteArchivedChannel = useCallback(async (channelId: string) => {
    if (!window.confirm("Permanently delete this chat? All messages are erased. This cannot be undone.")) return;
    try {
      const res = await fetch(`/api/chat/channels/${channelId}`, { method: "DELETE" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        window.alert(`Delete failed: ${(err as { error?: string }).error ?? res.status}`);
        return;
      }
      // Remove from local state + refetch
      setArchivedChannels((prev) => prev.filter((c) => c.id !== channelId));
      if (selectedChannel === channelId) setSelectedChannel(null);
    } catch (e) {
      window.alert(`Network error: ${String(e)}`);
    }
  }, [selectedChannel, fetchArchivedChannels]);

  /* ---- overflow menu handlers ---- */
  const handleEdit = (msgId: string) => {
    setEditingMessageId(msgId);
    setOverflowMenu(null);
  };

  const handleSaveEdit = async (msgId: string, content: string) => {
    try {
      await apiEditMessage(msgId, content);
      setEditingMessageId(null);
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const handleDelete = async (msgId: string) => {
    setOverflowMenu(null);
    if (!window.confirm("Delete this message?")) return;
    try {
      await apiDeleteMessage(msgId);
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const handleCopyLink = async (msgId: string) => {
    setOverflowMenu(null);
    if (!selectedChannel) return;
    const url = `${window.location.origin}/chat/${selectedChannel}?msg=${msgId}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch { /* ignore */ }
  };

  const handleCopyText = async (msgId: string) => {
    setOverflowMenu(null);
    const msg = messages.find((m) => m.id === msgId);
    if (!msg) return;
    try {
      await navigator.clipboard.writeText(msg.content);
    } catch { /* ignore */ }
  };

  const handlePin = async (msg: Message) => {
    setOverflowMenu(null);
    const isPinned = pinnedMessages.some((p) => p.id === msg.id);
    try {
      if (isPinned) await unpinMessage(msg.id);
      else await pinMessage(msg.id);
      if (selectedChannel) {
        const pins = await listPins(selectedChannel);
        setPinnedMessages(pins as PinnedMessage[]);
      }
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const handleMarkUnread = async (msgId: string) => {
    setOverflowMenu(null);
    if (!selectedChannel) return;
    try {
      await apiMarkUnread(selectedChannel, msgId);
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  const handlePinRequest = async (msgId: string) => {
    try {
      await pinMessage(msgId);
      if (selectedChannel) {
        const pins = await listPins(selectedChannel);
        setPinnedMessages(pins as PinnedMessage[]);
      }
    } catch (e) {
      setSendError((e as Error).message);
    }
  };

  /* ---- group channels by type ---- */
  // Standalone Messages: root channels (no project_id) go in the DM/Topics/Groups
  // sections; project channels nest under Projects. Project-scoped Messages shows
  // only that project's channels in the type sections (Projects nest is hidden).
  const inSidebarSection = (c: Channel) =>
    scope?.projectId ? c.project_id === scope.projectId : !c.project_id;
  const grouped = {
    dm: channels.filter((c) => c.type === "dm" && inSidebarSection(c)),
    topic: channels.filter((c) => c.type === "topic" && inSidebarSection(c)),
    group: channels.filter((c) => c.type === "group" && inSidebarSection(c)),
  };

  // Split DM channels into agent lifecycle buckets (Live / Suspended /
  // Archived) so deleted-agent DMs no longer mix in with live ones. Plain
  // user DMs and a2a channels stay under nonAgent (their original placement).
  const dmSections = bucketAgentChannels(grouped.dm, liveAgents, archivedAgents);

  const allChannels = [...channels, ...archivedChannels];
  const currentChannel = allChannels.find((c) => c.id === selectedChannel);
  const isCurrentArchived = currentChannel?.settings?.archived === true;
  // #1741: derive the stall banner. Silent for the first 20s of no activity
  // (healthy responses resolve well before then), a soft "taking longer" hint
  // after, and an amber "may be stalled" warning with a recovery pointer at 75s.
  const stallInfo = computeStallInfo(responseWatch, selectedChannel, Date.now());

  /* ---- slash menu derived state ---- */
  // In a DM (2 members: user + 1 agent), a leading "/" opens the agent's
  // slash menu.  In a group channel (3+ members), the user must prefix
  // with "@agentname /" so we know which agent's commands to show.
  const isDm = (currentChannel?.members?.length ?? 0) === 2;
  const showSlash = isDm ? input.startsWith("/") : /^@\S+\s+\//.test(input);
  // The agent scoped by "@agentname /" (group only; undefined in DM).
  const slashAgent = !isDm && showSlash ? input.match(/^@(\S+)\s+\//)?.[1] || undefined : undefined;
  const slashQuery = showSlash
    ? (isDm ? input.slice(1).split(/\s/, 1)[0] : input.slice(input.indexOf("/") + 1).split(/\s/, 1)[0]) || ""
    : "";

  /* ---- @mention autocomplete: candidates = channel members + "all" ---- */
  const mentionCandidates: string[] = (() => {
    if (!mention) return [];
    const q = mention.partial.toLowerCase();
    const pool = [...(currentChannel?.members ?? []).filter((m) => m !== "user"), "all"];
    const pref = pool.filter((m) => m.toLowerCase().startsWith(q));
    const sub = pool.filter((m) => !m.toLowerCase().startsWith(q) && m.toLowerCase().includes(q));
    return [...pref, ...sub].slice(0, 6);
  })();

  const insertMention = (slug: string | undefined) => {
    if (!mention || !slug) return;
    const el = inputRef.current;
    const pos = el?.selectionStart ?? input.length;
    const next = input.slice(0, mention.atIndex) + "@" + slug + " " + input.slice(pos);
    setInput(next);
    setMention(null);
    requestAnimationFrame(() => {
      if (el) {
        const caret = mention.atIndex + slug.length + 2; // past "@slug "
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    });
  };

  useEffect(() => { setMentionSel(0); }, [mention?.partial]);

  // Capture Arrow/Enter/Tab/Escape while the mention popover is open. Capture
  // phase + stopPropagation so the composer's send handler never sees them.
  useEffect(() => {
    if (!mention || mentionCandidates.length === 0) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setMention(null); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); setMentionSel((s) => Math.min(mentionCandidates.length - 1, s + 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); setMentionSel((s) => Math.max(0, s - 1)); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); insertMention(mentionCandidates[mentionSel]); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mention, mentionCandidates.join(","), mentionSel]);

  /* ---- project-grouped channels for sidebar (standalone mode) ---- */
  const projectGroups = (() => {
    const projectChannels = channels.filter((c) => c.project_id);
    if (!projectChannels.length) return [];
    const byId = new Map<string, { id: string; name: string; channels: Channel[] }>();
    for (const ch of projectChannels) {
      const pid = ch.project_id!;
      if (!byId.has(pid)) {
        const proj = projects.find((p) => p.id === pid);
        byId.set(pid, { id: pid, name: proj ? proj.name : pid, channels: [] });
      }
      byId.get(pid)!.channels.push(ch);
    }
    return Array.from(byId.values());
  })();

  /* ---------------------------------------------------------------- */
  /*  Sections definition (shared between mobile + desktop lists)     */
  /* ---------------------------------------------------------------- */

  // Agent DMs are grouped by lifecycle so live, suspended, and
  // archived/deleted agents are visually separated. Empty buckets are
  // omitted so the list stays compact. Plain user DMs and a2a channels
  // (nonAgent) keep their original "Direct Messages" placement.
  const SECTIONS = [
    { label: "Live", icon: <CircleDot size={13} />, items: dmSections.live },
    { label: "Suspended", icon: <PauseCircle size={13} />, items: dmSections.suspended },
    { label: "Archived Agents", icon: <Archive size={13} />, items: dmSections.archived },
    { label: "Direct Messages", icon: <AtSign size={13} />, items: dmSections.nonAgent },
    { label: "Topics", icon: <Hash size={13} />, items: grouped.topic },
    { label: "Groups", icon: <Users size={13} />, items: grouped.group },
  ].filter((s) => s.items.length > 0 || s.label === "Topics" || s.label === "Groups");

  const allEmpty =
    channelsLoaded &&
    SECTIONS.every((s) => s.items.length === 0) &&
    archivedChannels.length === 0 &&
    projectGroups.length === 0;

  const thinkingChannelIds: string[] = channels
    .filter((ch) => {
      const bound = (ch.settings as { taostalk_agent?: string } | undefined)?.taostalk_agent;
      return bound && typingAgents.some((a) => a.slug === bound);
    })
    .map((ch) => ch.id);

  /* ---------------------------------------------------------------- */
  /*  Channel list — iOS 26 grouped on mobile, flat sidebar on desktop */
  /* ---------------------------------------------------------------- */

  const channelListUI = (
    <ChannelSidebar
      isMobile={isMobile}
      wsStatus={wsStatus}
      allEmpty={allEmpty}
      sections={SECTIONS}
      collapsedSections={collapsedSections}
      onToggleSection={toggleSection}
      visibleInSection={visibleInSection}
      selectedChannel={selectedChannel}
      onSelectChannel={setSelectedChannel}
      unread={unread}
      nowMs={nowMs}
      liveAgents={liveAgents}
      archivedAgents={archivedAgents}
      archivedChannels={archivedChannels}
      archivedExpanded={archivedExpanded}
      onToggleArchived={() => setArchivedExpanded((v) => !v)}
      scope={scope}
      projectGroups={projectGroups}
      projectsExpanded={projectsExpanded}
      onToggleProjects={() => setProjectsExpanded((v) => !v)}
      projectChannelExpanded={projectChannelExpanded}
      onToggleProjectChannel={(projectId) =>
        setProjectChannelExpanded((prev) => ({ ...prev, [projectId]: !(prev[projectId] !== false) }))
      }
      onOpenAgentsApp={openAgentsApp}
      onRestoreArchivedChannel={handleRestoreArchivedChannel}
      onDeleteArchivedChannel={handleDeleteArchivedChannel}
      bus={bus}
      busSelected={busSelected}
      onSelectBusChannel={selectBusChannel}
      formatRelativeTime={relativeTime}
      thinkingChannelIds={thinkingChannelIds}
    />
  );

  /* ---------------------------------------------------------------- */
  /*  Message area                                                     */
  /* ---------------------------------------------------------------- */

  const messageAreaUI = busSelected ? (
    <A2aBusMessageView channel={busSelected} />
  ) : (
    <div className="relative flex-1 flex flex-col min-w-0 h-full">
      {selectedChannel && !atBottom && (
        <button
          type="button"
          onClick={scrollToLatest}
          aria-label="Jump to latest"
          className="absolute right-4 bottom-24 z-20 flex items-center gap-1.5 px-3 h-9 rounded-full bg-shell-surface-active border border-shell-border-strong text-shell-text/80 hover:text-shell-text shadow-lg hover:bg-shell-surface-hover backdrop-blur-xl transition-colors"
        >
          <ChevronDown size={16} aria-hidden="true" />
          {newCount > 0 && <span className="text-[11px] font-semibold">{newCount} new</span>}
        </button>
      )}
      {!selectedChannel ? (
        /* empty state: nothing selected yet */
        <div className="flex-1 flex items-center justify-center text-shell-text-tertiary">
          <div className="text-center px-6">
            <MessageCircle size={48} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm mb-3">Pick a channel or start a DM</p>
            <Button variant="outline" size="sm" onClick={() => setShowCreate(true)}>
              New channel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <MessageList
            ref={messageListHandleRef}
            messages={messages}
            fetchedChannel={fetchedChannel}
            channel={currentChannel}
            selectedChannel={selectedChannel}
            isMobile={isMobile}
            keyboardInset={keyboardInset}
            nowMs={nowMs}
            liveAgents={liveAgents}
            archivedAgents={archivedAgents}
            currentUserId={currentUserId}
            currentUserDisplayName={currentUserDisplayName}
            pinnedMessages={pinnedMessages}
            pinnedPopoverOpen={pinnedPopoverOpen}
            onTogglePinnedPopover={() => setPinnedPopoverOpen((o) => !o)}
            editingMessageId={editingMessageId}
            onCancelEdit={() => setEditingMessageId(null)}
            onSaveEdit={handleSaveEdit}
            onToggleReaction={toggleReaction}
            showEmoji={showEmoji}
            onShowEmoji={setShowEmoji}
            hoveredMessageId={hoveredMessageId}
            onHoverMessage={setHoveredMessageId}
            onReplyInThread={handleOpenThreadFor}
            onOverflow={(e, messageId) => { e.preventDefault(); setOverflowMenu({ messageId, x: e.clientX, y: e.clientY }); }}
            onOpenThread={handleOpenThreadFor}
            onApprovePinRequest={handlePinRequest}
            onViewCanvas={setViewingCanvas}
            newDividerAtId={newDividerAtId}
            atBottom={atBottom}
            newCount={newCount}
            onScrollToLatest={scrollToLatest}
            onScroll={handleScroll}
            dropTarget={{
              isOver: shellFileDropTarget.isOver,
              isValidTarget: shellFileDropTarget.isValidTarget,
              handlers: {
                onDragEnter: shellFileDropTarget.dropHandlers.onDragEnter,
                onDragOver: (e: React.DragEvent) => {
                  shellFileDropTarget.dropHandlers.onDragOver(e);
                  if (!e.defaultPrevented) e.preventDefault();
                },
                onDragLeave: shellFileDropTarget.dropHandlers.onDragLeave,
                onDrop: (e: React.DragEvent) => {
                  if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                    e.preventDefault();
                    for (const f of Array.from(e.dataTransfer.files)) {
                      const id = Math.random().toString(36).slice(2);
                      setPendingAttachments((p) => [...p, { id, filename: f.name, size: f.size, uploading: true, file: f }]);
                      uploadDiskFile(f, selectedChannel ?? undefined)
                        .then((rec) => setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, record: rec, uploading: false } : x)))
                        .catch((err) => setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, uploading: false, error: (err as Error).message } : x)));
                    }
                    return;
                  }
                  shellFileDropTarget.dropHandlers.onDrop(e);
                },
              },
            }}
            showAllThreads={showAllThreads}
            onToggleAllThreads={() => {
              if (showAllThreads) { setShowAllThreads(false); }
              else { closeThread(); setShowSettings(false); setShowSearch(false); setShowAllThreads(true); }
            }}
            showSearch={showSearch}
            onToggleSearch={() => {
              if (showSearch) { setShowSearch(false); }
              else { closeThread(); setShowSettings(false); setShowAllThreads(false); setShowSearch(true); }
            }}
            onOpenSettings={handleOpenSettings}
            typingHumans={typingHumans}
            typingAgents={typingAgents}
          />

          {/* #1741: stall banner — surfaces only when a response is abnormally
              slow or has gone quiet, so a stalled generation no longer looks
              like a frozen window. */}
{stallInfo && (
             <div
               role="status"
               className={`mx-4 mb-2 px-3 py-2 rounded-lg border text-[12px] flex items-center gap-2 shrink-0 ${
                 stallInfo.stalled
                   ? "bg-amber-500/10 border-amber-500/25 text-amber-300/90"
                   : "bg-shell-surface border-shell-border text-shell-text-secondary"
               }`}
             >
              {stallInfo.stalled ? (
                <AlertTriangle size={13} aria-hidden="true" className="shrink-0" />
              ) : (
                <Loader2 size={13} aria-hidden="true" className="shrink-0 animate-spin" />
              )}
              {stallInfo.stalled ? (
                <span className="flex-1">
                  No response from {stallInfo.agent} for {stallInfo.seconds}s. The model may be
                  stalled.{" "}
                  <button
                    type="button"
                    onClick={() => {
                      const a = getApp("dashboard");
                      if (a) openWindow("dashboard", a.defaultSize);
                    }}
                    className="underline underline-offset-2 hover:text-amber-200"
                  >
                    Open Activity to restart the AI services
                  </button>
                  .
                </span>
              ) : (
                <span className="flex-1">
                  {stallInfo.agent} is taking longer than usual… ({stallInfo.seconds}s)
                </span>
              )}
            </div>
          )}

          {/* archived banner */}
          {isCurrentArchived && (
            <div className="mx-4 mb-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[12px] text-amber-400/80 flex items-center gap-2 shrink-0" role="status">
              <Archive size={13} aria-hidden="true" />
              This chat is archived. The agent is no longer active.
            </div>
          )}

          {/* prefill banner */}
          {prefillBanner && (
            <div
              className="mx-4 mb-1 px-3 py-2 rounded-lg bg-accent-soft border border-accent-line text-[12px] text-accent-strong flex items-center gap-2 shrink-0"
              role="status"
              aria-label={`Composer prefilled from prompt: ${prefillBanner.promptName}`}
            >
              <span className="flex-1 truncate">
                Prefilled from: {prefillBanner.promptName}
                {prefillBanner.agentName ? ` for ${prefillBanner.agentName}` : ""} — edit and send
              </span>
              <button
                onClick={() => {
                  setPrefillBanner(null);
                  setInput("");
                }}
className="shrink-0 p-0.5 rounded hover:bg-shell-surface-active transition-colors"
                 aria-label="Dismiss prefill"
              >
                <X size={12} aria-hidden="true" />
              </button>
            </div>
          )}

          {/* send error */}
          {sendError && (
            <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded px-3 py-1 mx-4">
              {sendError}
            </div>
          )}

          {/* A2A channel note */}
          {currentChannel?.settings?.kind === "a2a" && messages.length === 0 && (
            <div
              role="note"
              style={{
                padding: "10px 14px",
                fontSize: 12,
                color: "var(--color-shell-text-secondary)",
                background: "var(--color-accent-soft)",
                border: "1px solid var(--color-accent-line)",
                borderRadius: 12,
                margin: "8px 12px",
              }}
            >
              Agents coordinate here. Mention <code>@&lt;slug&gt;</code> to hand off a task to another agent.
            </div>
          )}

          <MessageInput
            value={input}
            onChange={(v) => !isCurrentArchived && handleInputChange(v)}
            onSend={sendMessage}
            channel={currentChannel}
            isArchived={isCurrentArchived}
            isMobile={isMobile}
            keyboardInset={keyboardInset}
            slashCommands={slashCommands}
            showSlash={showSlash}
            slashQuery={slashQuery}
            slashAgent={slashAgent}
            mention={mention}
            mentionCandidates={mentionCandidates}
            mentionSel={mentionSel}
            onMentionSelChange={setMentionSel}
            onInsertMention={insertMention}
            onDismissMention={() => setMention(null)}
            pendingAttachments={pendingAttachments}
            onRemoveAttachment={(id) => setPendingAttachments((p) => p.filter((x) => x.id !== id))}
            onRetryAttachment={(id) => {
              const entry = pendingAttachments.find((x) => x.id === id);
              if (!entry) return;
              if (!entry.file) {
                setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, error: "Can't retry, remove and re-add" } : x));
                return;
              }
              if ((entry.retries ?? 0) >= 3) return;
              setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, retries: (x.retries ?? 0) + 1 } : x));
              uploadFileAttachment(id, entry.file);
            }}
            onFileUpload={handleFileUpload}
            onSlashPick={(slug, cmd) => setInput(`@${slug} /${cmd} `)}
            onSlashClose={() => {}}
            onPaste={(e) => {
              if (!e.clipboardData) return;
              const files = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
              if (files.length === 0) return;
              e.preventDefault();
              for (const f of files) {
                const id = Math.random().toString(36).slice(2);
                setPendingAttachments((p) => [...p, { id, filename: f.name || "pasted.png", size: f.size, uploading: true, file: f }]);
                uploadDiskFile(f, selectedChannel ?? undefined)
                  .then((rec) => setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, record: rec, uploading: false } : x)))
                  .catch((err) => setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, uploading: false, error: (err as Error).message } : x)));
              }
            }}
          />
        </>
      )}
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Toolbar — hide on mobile when in chat                           */
  /* ---------------------------------------------------------------- */

  const showToolbar = !isMobile || selectedChannel === null;

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  return (
    <div className="relative flex flex-col h-full bg-shell-base text-shell-text overflow-hidden">
      {/* Toolbar — hidden on mobile when a channel is selected */}
      {showToolbar && (
        <div className="relative flex items-center px-3 py-2.5 border-b border-shell-border shrink-0">
          {title ? (
            <>
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <span className="text-sm font-semibold text-shell-text">{title}</span>
              </div>
              <div className="ml-auto">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setShowCreate(true)}
                  className="h-7 w-7"
                  aria-label="New channel"
                >
                  <Plus size={15} />
                </Button>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2 text-sm font-medium text-shell-text">
                <MessageCircle size={15} />
                {!isMobile && "Messages"}
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setShowCreate(true)}
                className="h-7 w-7 ml-auto"
                aria-label="New channel"
              >
                <Plus size={15} />
              </Button>
            </>
          )}
        </div>
      )}

      {/* Master-detail — MobileSplitView handles mobile single-pane + desktop split */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <MobileSplitView
          selectedId={selectedChannel ?? busSelected}
          onBack={() => { setSelectedChannel(null); setBusSelected(null); }}
          listTitle="Messages"
          // On mobile the in-pane channel header already shows the channel name
          // (and its toolbar), so suppressing the nav title removes the doubled
          // name and the dead space it took up. Desktop keeps it.
          detailTitle={isMobile ? undefined : (busSelected ?? currentChannel?.name)}
          listWidth={240}
          list={channelListUI}
          detail={messageAreaUI}
        />
      </div>

      {/* ---- Message Overflow Menu ---- */}
      {overflowMenu && (() => {
        const msg = messages.find((m) => m.id === overflowMenu.messageId);
        if (!msg) return null;
        const menu = (
          <MessageOverflowMenu
            isOwn={msg.author_id === currentUserId}
            isHuman={true} /* desktop UI viewer is always human */
            isPinned={pinnedMessages.some((p) => p.id === msg.id)}
            onEdit={() => handleEdit(msg.id)}
            onDelete={() => handleDelete(msg.id)}
            onCopyLink={() => handleCopyLink(msg.id)}
            onCopyText={() => handleCopyText(msg.id)}
            onPin={() => handlePin(msg)}
            onMarkUnread={() => handleMarkUnread(msg.id)}
            onClose={() => setOverflowMenu(null)}
          />
        );
        if (isMobile) {
          return (
            <BottomSheet open={true} onClose={() => setOverflowMenu(null)}>
              {menu}
            </BottomSheet>
          );
        }
        return (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setOverflowMenu(null)} />
            <div className="fixed z-50" style={{ top: overflowMenu.y, left: overflowMenu.x }}>
              {menu}
            </div>
          </>
        );
      })()}

      {/* ---- Channel Settings Panel ---- */}
      {showSettings && currentChannel && (
        <ChannelSettingsPanel
          channel={{
            id: currentChannel.id,
            name: currentChannel.name,
            type: currentChannel.type,
            topic: currentChannel.topic ?? currentChannel.description ?? "",
            members: currentChannel.members ?? [],
            settings: currentChannel.settings ?? {},
          }}
          knownAgents={liveAgents.map((a) => ({ name: a.name }))}
          onClose={() => setShowSettings(false)}
          onChanged={() => { void fetchChannels(); }}
        />
      )}

      {/* ---- Thread Panel ---- */}
      {openThread && (
        <ThreadPanel
          channelId={openThread.channelId}
          parentId={openThread.parentId}
          onClose={closeThread}
          isFullscreen={isMobile}
          liveReplies={threadLiveReplies}
          authorCtx={{ currentUserId, currentUserDisplayName }}
          onSend={async (content, attachments) => {
            const r = await fetch("/api/chat/messages", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                channel_id: openThread.channelId,
                author_id: "user",
                author_type: "user",
                content,
                content_type: "text",
                thread_id: openThread.parentId,
                attachments,
              }),
            });
            if (!r.ok) {
              const body = await r.json().catch(() => ({}));
              throw new Error((body as { error?: string }).error || `HTTP ${r.status}`);
            }
          }}
        />
      )}

      {/* ---- All Threads Panel ---- */}
      {showAllThreads && selectedChannel && !openThread && !showSettings && !showSearch && (
        <AllThreadsList
          channelId={selectedChannel}
          onClose={() => setShowAllThreads(false)}
          onJumpToThread={(parentId) => {
            setShowAllThreads(false);
            openThreadFor(selectedChannel, parentId);
          }}
          authorCtx={{ currentUserId, currentUserDisplayName }}
        />
      )}

      {/* ---- Search Panel ---- */}
      {showSearch && !openThread && !showSettings && !showAllThreads && (
        <SearchPanel
          onJump={(channelId, messageId) => {
            setShowSearch(false);
            if (channelId !== selectedChannel) {
              // Switching channel triggers fetchMessages; the scroll happens
              // once the new messages render (the rAF retry below waits for it).
              setSelectedChannel(channelId);
            }
            // Poll across a few frames so a slow channel switch/render still
            // lands instead of relying on a single fixed delay. If the target
            // is not in the first 50 loaded messages it never appears, so the
            // jump silently no-ops (search hits are not paginated here).
            let attempts = 0;
            const tryScroll = () => {
              const el = document.querySelector(`[data-message-id="${messageId}"]`) as HTMLElement | null;
              if (el) {
                el.scrollIntoView({ behavior: "smooth", block: "center" });
                el.classList.add("data-highlight");
                setTimeout(() => el.classList.remove("data-highlight"), 2000);
                return;
              }
              if (attempts++ < 40) requestAnimationFrame(tryScroll);
            };
            requestAnimationFrame(tryScroll);
          }}
          onClose={() => setShowSearch(false)}
          channels={allChannels.map((c) => ({ id: c.id, name: c.name }))}
          authorCtx={{ currentUserId, currentUserDisplayName }}
        />
      )}

      {/* ---- Quick channel switcher (Cmd/Ctrl+K) ---- */}
      {showSwitcher && (
        <ChannelSwitcher
          channels={channels.map((c) => ({ id: c.id, name: c.name }))}
          onSelect={(id) => setSelectedChannel(id)}
          onClose={() => setShowSwitcher(false)}
        />
      )}

      {/* ---- Agent Context Menu ---- */}
      {contextMenu && (
        <AgentContextMenu
          slug={contextMenu.slug}
          channelId={selectedChannel ?? undefined}
          channelType={currentChannel?.type}
          isMuted={currentChannel?.settings?.muted?.includes(contextMenu.slug) ?? false}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          onDm={async (slug) => {
            const existing = channels.find((ch) =>
              ch.type === "dm"
              && (ch.members || []).length === 2
              && (ch.members || []).includes("user")
              && (ch.members || []).includes(slug)
            );
            if (existing) {
              setSelectedChannel(existing.id);
            } else {
              const r = await fetch("/api/chat/channels", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  name: slug, type: "dm",
                  members: ["user", slug],
                  description: "", topic: "",
                }),
              });
              if (r.ok) {
                const created = await r.json();
                await fetchChannels();
                setSelectedChannel(created.id);
              }
            }
            setContextMenu(null);
          }}
          onViewInfo={(slug) => {
            const agent = liveAgents.find((a) => a.name === slug);
            if (agent) {
              setAgentInfoPopover({
                slug,
                framework: agent.framework || "unknown",
                model: agent.model || "unknown",
                status: agent.status || "unknown",
                x: contextMenu.x,
                y: contextMenu.y,
              });
            }
            setContextMenu(null);
          }}
          onJumpToSettings={(slug) => {
            window.dispatchEvent(new CustomEvent("taos:open-agent", { detail: { slug } }));
            setContextMenu(null);
          }}
        />
      )}

      {/* ---- Agent Info Popover ---- */}
      {agentInfoPopover && (
        <div
          role="dialog"
          aria-label={`Agent info for @${agentInfoPopover.slug}`}
          className="fixed z-50 bg-shell-surface border border-shell-border rounded-lg shadow-xl p-3 text-xs min-w-[200px]"
          style={{ top: agentInfoPopover.y, left: agentInfoPopover.x }}
          onMouseLeave={() => setAgentInfoPopover(null)}
        >
          <div className="font-semibold text-sm mb-1">@{agentInfoPopover.slug}</div>
          <div className="opacity-70">Framework: {agentInfoPopover.framework}</div>
          <div className="opacity-70">Model: {agentInfoPopover.model}</div>
          <div className="opacity-70">Status: {agentInfoPopover.status}</div>
        </div>
      )}

      {/* ---- Canvas Viewer ---- */}
      {viewingCanvas && (
        <div
          className="fixed inset-0 z-[10002] flex items-center justify-center bg-shell-scrim backdrop-blur-sm"
          onClick={() => setViewingCanvas(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Canvas viewer"
        >
          <div
            className="w-[90vw] h-[85vh] max-w-5xl rounded-xl border border-shell-border overflow-hidden bg-shell-bg flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-shell-border shrink-0">
              <div className="flex items-center gap-2 text-sm text-shell-text">
                <PanelRight size={14} />
                <span>{viewingCanvas.title ?? "Canvas"}</span>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setViewingCanvas(null)}
                className="h-7 w-7"
                aria-label="Close canvas viewer"
              >
                <X size={14} />
              </Button>
            </div>
            <iframe
              src={viewingCanvas.url}
              className="flex-1 w-full border-none bg-white" // palette-ok: canvas iframe document background is legitimately white
              title="Canvas"
            />
          </div>
        </div>
      )}

      {/* ---- Create Channel — bottom sheet on mobile, centred modal on desktop ---- */}
      {showCreate && (
        isMobile ? (
          <div
            className="fixed inset-0 z-50"
            onClick={() => setShowCreate(false)}
            role="dialog"
            aria-modal="true"
            aria-label="New channel"
          >
            <div
              className="absolute bottom-0 left-0 right-0 bg-shell-bg border-t border-shell-border rounded-t-2xl p-4 space-y-3"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-semibold">New Channel</span>
                <Button variant="ghost" size="icon" onClick={() => setShowCreate(false)} className="h-7 w-7" aria-label="Close">
                  <X size={15} />
                </Button>
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-channel-name-mobile" className="block uppercase tracking-wider">Name</Label>
                <Input
                  id="new-channel-name-mobile"
                  value={newChannel.name}
                  onChange={(e) => setNewChannel((s) => ({ ...s, name: e.target.value }))}
                  placeholder="general"
                  aria-label="Channel name"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-channel-type-mobile" className="block uppercase tracking-wider">Type</Label>
                <select
                  id="new-channel-type-mobile"
                  value={newChannel.type}
                  onChange={(e) => setNewChannel((s) => ({ ...s, type: e.target.value as "topic" | "group" }))}
                  className="w-full bg-shell-subtle border border-shell-border rounded-lg px-3 py-2 text-sm text-shell-text outline-none focus:border-accent-line"
                  aria-label="Channel type"
                >
                  <option value="topic">Topic</option>
                  <option value="group">Group</option>
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-channel-description-mobile" className="block uppercase tracking-wider">Description</Label>
                <Input
                  id="new-channel-description-mobile"
                  value={newChannel.description}
                  onChange={(e) => setNewChannel((s) => ({ ...s, description: e.target.value }))}
                  placeholder="What's this channel about?"
                  aria-label="Channel description"
                />
              </div>
              <Button onClick={createChannel} disabled={!newChannel.name.trim()} className="w-full">
                Create Channel
              </Button>
            </div>
          </div>
        ) : (
          <div className="absolute inset-0 bg-shell-scrim flex items-center justify-center z-50 p-4">
            <Card className="w-full max-w-[380px] max-h-full flex flex-col shadow-2xl bg-shell-bg">
              <CardHeader className="flex flex-row items-center justify-between gap-2 p-0 px-4 py-3 border-b border-shell-border">
                <CardTitle className="text-sm font-medium">New Channel</CardTitle>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setShowCreate(false)}
                  className="h-7 w-7"
                  aria-label="Close"
                >
                  <X size={15} />
                </Button>
              </CardHeader>
              <CardContent className="p-4 pt-4 space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="new-channel-name" className="block uppercase tracking-wider">Name</Label>
                  <Input
                    id="new-channel-name"
                    value={newChannel.name}
                    onChange={(e) => setNewChannel((s) => ({ ...s, name: e.target.value }))}
                    placeholder="general"
                    aria-label="Channel name"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="new-channel-type" className="block uppercase tracking-wider">Type</Label>
                  <select
                    id="new-channel-type"
                    value={newChannel.type}
                    onChange={(e) => setNewChannel((s) => ({ ...s, type: e.target.value as "topic" | "group" }))}
                    className="w-full bg-shell-subtle border border-shell-border rounded-lg px-3 py-2 text-sm text-shell-text outline-none focus:border-accent-line"
                    aria-label="Channel type"
                  >
                    <option value="topic">Topic</option>
                    <option value="group">Group</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="new-channel-description" className="block uppercase tracking-wider">Description</Label>
                  <Input
                    id="new-channel-description"
                    value={newChannel.description}
                    onChange={(e) => setNewChannel((s) => ({ ...s, description: e.target.value }))}
                    placeholder="What's this channel about?"
                    aria-label="Channel description"
                  />
                </div>
                <Button
                  onClick={createChannel}
                  disabled={!newChannel.name.trim()}
                  className="w-full"
                >
                  Create Channel
                </Button>
              </CardContent>
            </Card>
          </div>
        )
      )}
    </div>
  );
}
