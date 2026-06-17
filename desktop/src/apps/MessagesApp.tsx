import React, { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  MessageCircle,
  Hash,
  Users,
  Plus,
  Send,
  Paperclip,
  Bot,
  X,
  AtSign,
  Wifi,
  WifiOff,
  ChevronRight,
  ChevronDown,
  PanelRight,
  Archive,
  Trash2,
  RotateCcw,
  MessagesSquare,
  Search,
} from "lucide-react";
import {
  Button,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Input,
  Textarea,
  Label,
} from "@/components/ui";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { useVisualViewport } from "@/hooks/use-visual-viewport";
import { useDropTarget } from "@/shell/dnd/use-drop-target";
import { startDrag, endDrag } from "@/shell/dnd/dnd-bus";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { MessageAvatar } from "./chat/MessageAvatar";
import { ChannelSettingsPanel } from "./chat/ChannelSettingsPanel";
import { AgentContextMenu } from "./chat/AgentContextMenu";
import { SlashMenu, type SlashCommandsBySlug } from "./chat/SlashMenu";
import { TypingFooter, type AgentTyping } from "./chat/TypingFooter";
import { useTypingEmitter } from "@/lib/use-typing-emitter";
import { MessageHoverActions } from "./chat/MessageHoverActions";
import { ThreadIndicator } from "./chat/ThreadIndicator";
import { ThreadPanel } from "./chat/ThreadPanel";
import { AttachmentsBar, type PendingAttachment } from "./chat/AttachmentsBar";
import { AttachmentGallery } from "./chat/AttachmentGallery";
import { uploadDiskFile, attachmentFromPath, type AttachmentRecord } from "@/lib/chat-attachments-api";
import { useThreadPanel } from "@/lib/use-thread-panel";
import { openFilePicker } from "@/shell/file-picker-api";
import { MessageOverflowMenu } from "./chat/MessageOverflowMenu";
import { BottomSheet } from "@/shell/BottomSheet";
import { MessageEditor } from "./chat/MessageEditor";
import { MessageTombstone } from "./chat/MessageTombstone";
import { PinBadge } from "./chat/PinBadge";
import { PinnedMessagesPopover, type PinnedMessage } from "./chat/PinnedMessagesPopover";
import { AllThreadsList } from "./chat/AllThreadsList";
import { ChannelSwitcher } from "./chat/ChannelSwitcher";
import { useChatNotifications } from "./chat/useChatNotifications";
import { PinRequestAffordance } from "./chat/PinRequestAffordance";
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
import { displayAuthor } from "./chat/format-author";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { CodeBlock } from "@/components/CodeBlock";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Picker, { Theme } from "emoji-picker-react";
import { SearchPanel } from "./chat/SearchPanel";
import {
  A2aBusSection,
  A2aBusMessageView,
  useBusChannels,
} from "./chat/A2aBusPanel";

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

interface Message {
  id: string;
  channel_id: string;
  author_id: string;
  author_type: "user" | "agent";
  content: string;
  /** Parent message id when this message is a thread reply. */
  thread_id?: string;
  content_type?: "text" | "canvas" | string;
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
function toMs(ts: number | string): number {
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  if (ts === "" || ts == null) return Date.now();
  const n = Number(ts);
  if (!Number.isNaN(n)) return n < 1e12 ? n * 1000 : n;
  return new Date(ts).getTime();
}

function relativeTime(ts: number | string, nowMs: number = Date.now()): string {
  const ms = toMs(ts);
  const mins = Math.floor((nowMs - ms) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  // Older than an hour: show the clock time. The day context comes from the
  // date separators rendered between message groups.
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function renderContent(text: string) {
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
              <code className="bg-white/10 px-1.5 py-0.5 rounded text-[13px] font-mono" {...props}>
                {children}
              </code>
            );
          },
          ul: ({ node, ...props }) => <ul className="list-disc pl-5 mb-1" {...props} />,
          ol: ({ node, ...props }) => <ol className="list-decimal pl-5 mb-1" {...props} />,
          blockquote: ({ node, ...props }) => (
            <blockquote className="border-l-2 border-white/20 pl-3 text-white/70" {...props} />
          ),
          pre: ({ node, ...props }) => (
            <pre className="my-2 overflow-x-auto max-w-full bg-black/30 border border-white/10 rounded p-2 text-[13px]" {...props} />
          ),
          table: ({ node, ...props }) => (
            <div className="my-2 overflow-x-auto">
              <table className="min-w-full text-left text-[13px]" {...props} />
            </div>
          ),
          th: ({ node, ...props }) => (
            <th className="border-b border-white/10 px-2 py-1 font-semibold" {...props} />
          ),
          td: ({ node, ...props }) => (
            <td className="border-b border-white/5 px-2 py-1 align-top" {...props} />
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

const EMOJI_PICKER = ["👍", "❤️", "😂", "🎉", "🤔", "👀", "🚀", "✅"];

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
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);
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
            break;

          case "message_delta":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id
                  ? { ...m, content: m.content + (data.delta ?? ""), state: "streaming" }
                  : m,
              ),
            );
            break;

          case "message_state":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === data.message_id ? { ...m, state: data.state } : m,
              ),
            );
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
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
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

  const handleScroll = () => {
    const el = messageListRef.current;
    if (!el) return;
    const nowAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    autoScrollRef.current = nowAtBottom;
    // Only flip state (avoids re-render storms on every scroll tick).
    setAtBottom((prev) => (prev === nowAtBottom ? prev : nowAtBottom));
    if (nowAtBottom) setNewCount(0);
  };

  const scrollToLatest = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
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

  /* ---- typing emitter + slash menu derived state ---- */
  const emitTyping = useTypingEmitter(selectedChannel, "user");
  const showSlash = input.startsWith("/");
  const slashQuery = showSlash ? input.slice(1).split(/\s/, 1)[0] || "" : "";

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

  /* ---- key handler ---- */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // The mention popover (when open) owns Enter/Tab via a capture listener
    // that stops propagation, so this send handler never sees those keys.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
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
        setChannels((prev) => [...prev, ch]);
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
  const isRoot = (c: Channel) => !c.project_id;
  const grouped = {
    dm: channels.filter((c) => c.type === "dm" && isRoot(c)),
    topic: channels.filter((c) => c.type === "topic" && isRoot(c)),
    group: channels.filter((c) => c.type === "group" && isRoot(c)),
  };

  const allChannels = [...channels, ...archivedChannels];
  const currentChannel = allChannels.find((c) => c.id === selectedChannel);
  const isCurrentArchived = currentChannel?.settings?.archived === true;

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

  const SECTIONS = [
    { label: "Direct Messages", icon: <AtSign size={13} />, items: grouped.dm },
    { label: "Topics", icon: <Hash size={13} />, items: grouped.topic },
    { label: "Groups", icon: <Users size={13} />, items: grouped.group },
  ];

  const allEmpty =
    channelsLoaded &&
    SECTIONS.every((s) => s.items.length === 0) &&
    archivedChannels.length === 0 &&
    projectGroups.length === 0;

  /* ---------------------------------------------------------------- */
  /*  Channel list — iOS 26 grouped on mobile, flat sidebar on desktop */
  /* ---------------------------------------------------------------- */

  const channelListUI = isMobile ? (
    /* Mobile: iOS 26 grouped list */
    <div style={{ padding: "8px 0 16px" }}>
      {/* connection status */}
      <div style={{ padding: "0 20px 8px", fontSize: 11, display: "flex", alignItems: "center", gap: 6 }}>
        {wsStatus === "connected" ? (
          <><Wifi size={11} style={{ color: "#34d399" }} /><span style={{ color: "rgba(52,211,153,0.8)" }}>Connected</span></>
        ) : wsStatus === "connecting" ? (
          <><Wifi size={11} style={{ color: "#fbbf24" }} /><span style={{ color: "rgba(251,191,36,0.8)" }}>Connecting…</span></>
        ) : (
          <><WifiOff size={11} style={{ color: "#f87171" }} /><span style={{ color: "rgba(248,113,113,0.8)" }}>Offline</span></>
        )}
      </div>

      {allEmpty ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 24px", textAlign: "center", gap: 12 }}>
          <MessageCircle size={36} style={{ color: "var(--color-shell-text-tertiary)" }} aria-hidden="true" />
          <p style={{ fontSize: 15, fontWeight: 600, color: "var(--color-shell-text)", margin: 0 }}>No conversations yet</p>
          <p style={{ fontSize: 13, color: "var(--color-shell-text-secondary)", margin: 0 }}>Deploy an agent to start chatting</p>
          <button
            type="button"
            onClick={openAgentsApp}
            style={{ marginTop: 4, fontSize: 13, padding: "8px 16px", borderRadius: 10, background: "var(--color-accent-soft)", border: "1px solid var(--color-accent-line)", color: "var(--color-accent-strong)", cursor: "pointer" }}
          >
            Open Agents
          </button>
        </div>
      ) : SECTIONS.map((section) => (
        <div key={section.label} style={{ marginBottom: 20 }}>
          <button
            type="button"
            onClick={() => toggleSection(section.label)}
            aria-expanded={!collapsedSections[section.label]}
            style={{ fontSize: 12, textTransform: "uppercase" as const, letterSpacing: 0.5, color: "var(--color-shell-text-secondary)", padding: "0 20px 6px", fontWeight: 600, display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", cursor: "pointer", width: "100%" }}
          >
            <ChevronRight size={13} aria-hidden="true" style={{ transition: "transform 0.15s", transform: collapsedSections[section.label] ? "none" : "rotate(90deg)" }} />
            {section.icon} {section.label}
          </button>
          {visibleInSection(section.items, section.label).length === 0 ? (
            collapsedSections[section.label] ? null : (
              <div style={{ padding: "0 20px", fontSize: 12, color: "var(--color-shell-text-tertiary)", fontStyle: "italic" }}>None yet</div>
            )
          ) : (
            <div
              style={{
                margin: "0 12px",
                borderRadius: 16,
                background: "var(--color-shell-surface)",
                border: "1px solid var(--color-shell-border)",
                overflow: "hidden",
              }}
            >
              {visibleInSection(section.items, section.label).map((ch, idx, arr) => {
                const isA2A = ch.settings?.kind === "a2a";
                // Only direct messages get an agent avatar; topics/groups/a2a get
                // a glyph tile (a topic/group can include agent members too).
                const agentMember = ch.type === "dm" ? (ch.members ?? []).find((m) => m !== "user") : undefined;
                const count = unread[ch.id] ?? 0;
                return (
                <button
                  key={ch.id}
                  type="button"
                  onClick={() => setSelectedChannel(ch.id)}
                  aria-label={`Channel ${ch.name}`}
                  title={isA2A ? "Agent coordination — mention @<slug> to hand off." : undefined}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    width: "100%",
                    padding: "11px 14px",
                    background: selectedChannel === ch.id ? "var(--color-shell-surface-active)" : "none",
                    border: "none",
                    borderBottom: idx === arr.length - 1 ? "none" : "1px solid var(--color-shell-border)",
                    cursor: "pointer",
                    color: "inherit",
                    textAlign: "left",
                  }}
                >
                  {agentMember ? (
                    <MessageAvatar size={38} authorId={agentMember} displayName={agentMember} kind="agent" />
                  ) : isA2A ? (
                    <div style={{ width: 38, height: 38, borderRadius: 11, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--color-accent-soft)", border: "1px solid var(--color-accent-line)", color: "var(--color-accent-strong)", flexShrink: 0 }}>
                      <Bot size={18} aria-hidden />
                    </div>
                  ) : (
                    <div style={{ width: 38, height: 38, borderRadius: 11, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--color-shell-surface-active)", color: "var(--color-shell-text-secondary)", flexShrink: 0 }}>
                      {ch.type === "group" ? <Users size={18} aria-hidden /> : <Hash size={18} aria-hidden />}
                    </div>
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                      <span style={{ flex: 1, fontSize: 15, fontWeight: count > 0 ? 700 : 600, color: "var(--color-shell-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {ch.name}
                      </span>
                      {ch.last_message_at && (
                        <span style={{ fontSize: 11, color: "var(--color-shell-text-tertiary)", flexShrink: 0 }}>
                          {relativeTime(ch.last_message_at, nowMs)}
                        </span>
                      )}
                    </div>
                    {ch.lastPreview && (
                      <div style={{ fontSize: 13, color: "var(--color-shell-text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 1 }}>
                        {ch.lastPreview}
                      </div>
                    )}
                  </div>
                  {count > 0 && (
                    <span style={{ background: "var(--color-unread)", color: "#fff", fontSize: 10, fontWeight: 700, borderRadius: 9999, minWidth: 18, height: 18, display: "flex", alignItems: "center", justifyContent: "center", padding: "0 5px", flexShrink: 0 }}>
                      {count}
                    </span>
                  )}
                </button>
                );
              })}
            </div>
          )}
        </div>
      ))}

      {/* Projects section — mobile (standalone mode only) */}
      {!scope?.projectId && projectGroups.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <button
            type="button"
            onClick={() => setProjectsExpanded((v) => !v)}
            aria-expanded={projectsExpanded}
            aria-controls="projects-section-mobile"
            style={{ fontSize: 12, textTransform: "uppercase" as const, letterSpacing: 0.5, color: "var(--color-shell-text-secondary)", padding: "0 20px 6px", fontWeight: 600, display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", cursor: "pointer", width: "100%" }}
          >
            <ChevronRight size={12} style={{ transition: "transform 0.15s", transform: projectsExpanded ? "rotate(90deg)" : "none", color: "var(--color-shell-text-tertiary)" }} aria-hidden="true" />
            Projects ({projectGroups.length})
          </button>
          <div id="projects-section-mobile" style={{ display: projectsExpanded ? "block" : "none" }}>
            {projectGroups.map((g) => {
              const isOpen = projectChannelExpanded[g.id] !== false;
              return (
                <div key={g.id} style={{ marginBottom: 12 }}>
                  <button
                    type="button"
                    onClick={() => setProjectChannelExpanded((prev) => ({ ...prev, [g.id]: !isOpen }))}
                    aria-expanded={isOpen}
                    aria-controls={`project-section-mobile-${g.id}`}
                    style={{ fontSize: 11, color: "var(--color-shell-text-secondary)", padding: "0 20px 4px", fontWeight: 600, display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", cursor: "pointer", width: "100%" }}
                  >
                    <ChevronRight size={10} style={{ transition: "transform 0.15s", transform: isOpen ? "rotate(90deg)" : "none", color: "var(--color-shell-text-tertiary)" }} aria-hidden="true" />
                    {g.name}
                  </button>
                  <div id={`project-section-mobile-${g.id}`} style={{ display: isOpen ? "block" : "none" }}>
                    <div style={{ margin: "0 12px", borderRadius: 16, background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)", overflow: "hidden" }}>
                      {g.channels.map((ch, idx, arr) => (
                        <button
                          key={ch.id}
                          type="button"
                          onClick={() => setSelectedChannel(ch.id)}
                          aria-label={`Channel ${ch.name}`}
                          title={ch.settings?.kind === "a2a" ? "Agent coordination — mention @<slug> to hand off." : undefined}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 10,
                            width: "100%",
                            padding: "14px 16px",
                            background: selectedChannel === ch.id ? "var(--color-shell-surface-active)" : "none",
                            border: "none",
                            borderBottom: idx === arr.length - 1 ? "none" : "1px solid var(--color-shell-border)",
                            cursor: "pointer",
                            color: "inherit",
                            textAlign: "left",
                          }}
                        >
                          {ch.settings?.kind === "a2a" && (
                            <Bot
                              size={14}
                              aria-hidden
                              style={{ color: "var(--color-shell-text-secondary)", flexShrink: 0 }}
                            />
                          )}
                          <span style={{ flex: 1, fontSize: 15, fontWeight: 400, color: "var(--color-shell-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {ch.name}
                          </span>
                          {(unread[ch.id] ?? 0) > 0 && (
                            <span style={{ background: "var(--color-unread)", color: "#fff", fontSize: 10, fontWeight: 700, borderRadius: 9999, minWidth: 18, height: 18, display: "flex", alignItems: "center", justifyContent: "center", padding: "0 4px" }}>
                              {unread[ch.id]}
                            </span>
                          )}
                          <ChevronRight size={16} style={{ color: "var(--color-shell-text-tertiary)", flexShrink: 0 }} />
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Archived channels section — mobile */}
      {archivedChannels.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <button
            type="button"
            onClick={() => setArchivedExpanded((v) => !v)}
            aria-expanded={archivedExpanded}
            aria-controls="archived-channels-mobile"
            style={{ fontSize: 12, textTransform: "uppercase" as const, letterSpacing: 0.5, color: "var(--color-shell-text-tertiary)", padding: "0 20px 6px", fontWeight: 600, display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", cursor: "pointer", width: "100%" }}
          >
            <ChevronRight size={12} style={{ transition: "transform 0.15s", transform: archivedExpanded ? "rotate(90deg)" : "none", color: "var(--color-shell-text-tertiary)" }} aria-hidden="true" />
            <Archive size={12} aria-hidden="true" />
            Archived ({archivedChannels.length})
          </button>
          <div id="archived-channels-mobile" style={{ display: archivedExpanded ? "block" : "none" }}>
            <div style={{ margin: "0 12px", borderRadius: 16, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", overflow: "hidden" }}>
              {archivedChannels.map((ch, idx, arr) => {
                const agentId = ch.settings?.archived_agent_id;
                const hasAgent = agentId ? archivedAgents.some((a) => a.id === agentId) : false;
                return (
                  <div
                    key={ch.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      borderBottom: idx === arr.length - 1 ? "none" : "1px solid rgba(255,255,255,0.04)",
                      opacity: 0.6,
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedChannel(ch.id)}
                      aria-label={`Archived channel ${ch.name}`}
                      style={{ flex: 1, display: "flex", alignItems: "center", gap: 8, padding: "12px 8px 12px 16px", background: selectedChannel === ch.id ? "var(--color-shell-surface-active)" : "none", border: "none", cursor: "pointer", color: "inherit", textAlign: "left" as const, minWidth: 0 }}
                    >
                      <Archive size={11} aria-hidden="true" style={{ color: "var(--color-shell-text-tertiary)", flexShrink: 0 }} />
                      <span style={{ flex: 1, fontSize: 14, color: "var(--color-shell-text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ch.name}</span>
                    </button>
                    <div style={{ display: "flex", gap: 2, paddingRight: 8 }}>
                      <button
                        type="button"
                        onClick={() => handleRestoreArchivedChannel(ch.id, ch.name)}
                        disabled={!hasAgent}
                        aria-label={`Restore archived channel ${ch.name}`}
                        title={hasAgent ? "Restore agent" : "Agent entry missing — delete only"}
                        style={{ background: "none", border: "none", cursor: hasAgent ? "pointer" : "not-allowed", color: hasAgent ? "rgba(52,211,153,0.7)" : "rgba(255,255,255,0.2)", padding: "6px" }}
                      >
                        <RotateCcw size={13} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteArchivedChannel(ch.id)}
                        aria-label={`Permanently delete archived channel ${ch.name}`}
                        title="Delete permanently"
                        style={{ background: "none", border: "none", cursor: "pointer", color: "rgba(248,113,113,0.7)", padding: "6px" }}
                      >
                        <Trash2 size={13} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* External taOSmd coordination bus (read-only) */}
      <A2aBusSection
        channels={bus.channels}
        available={bus.available}
        loaded={bus.loaded}
        selected={busSelected}
        onSelect={selectBusChannel}
      />
    </div>
  ) : (
    /* Desktop: compact sidebar */
    <div className="w-full flex flex-col h-full">
      {/* connection status */}
      <div className="px-3 py-1.5 text-[11px] flex items-center gap-1.5">
        {wsStatus === "connected" ? (
          <><Wifi size={11} className="text-emerald-400" /><span className="text-emerald-400/80">Connected</span></>
        ) : wsStatus === "connecting" ? (
          <><Wifi size={11} className="text-amber-400 animate-pulse" /><span className="text-amber-400/80">Connecting...</span></>
        ) : (
          <><WifiOff size={11} className="text-red-400" /><span className="text-red-400/80">Offline</span></>
        )}
      </div>

      {/* channel list */}
      <div className="flex-1 overflow-y-auto py-1">
        {allEmpty ? (
          <div className="flex flex-col items-center justify-center h-full px-4 py-10 text-center gap-2.5">
            <MessageCircle size={28} className="text-white/15" aria-hidden="true" />
            <p className="text-[13px] font-medium text-white/60">No conversations yet</p>
            <p className="text-[11px] text-white/30">Deploy an agent to start chatting</p>
            <Button
              variant="outline"
              size="sm"
              onClick={openAgentsApp}
              className="mt-1 text-xs"
            >
              Open Agents
            </Button>
          </div>
        ) : SECTIONS.map((section) => (
          <div key={section.label}>
            <button
              type="button"
              onClick={() => toggleSection(section.label)}
              aria-expanded={!collapsedSections[section.label]}
              className="w-full px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-white/30 hover:text-white/50 flex items-center gap-1.5 transition-colors"
            >
              <ChevronRight
                size={11}
                aria-hidden="true"
                className={`transition-transform ${collapsedSections[section.label] ? "" : "rotate-90"}`}
              />
              {section.icon} {section.label}
            </button>
            {!collapsedSections[section.label] && section.items.length === 0 && (
              <div className="px-3 py-1 text-[11px] text-white/20 italic">None yet</div>
            )}
            <div className="px-2 flex flex-col gap-px">
              {visibleInSection(section.items, section.label).map((ch) => {
                const isA2A = ch.settings?.kind === "a2a";
                const agentMember = ch.type === "dm" ? (ch.members ?? []).find((m) => m !== "user") : undefined;
                const count = unread[ch.id] ?? 0;
                return (
                  <button
                    key={ch.id}
                    type="button"
                    onClick={() => setSelectedChannel(ch.id)}
                    aria-pressed={selectedChannel === ch.id}
                    className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-[10px] text-left transition-colors ${
                      selectedChannel === ch.id ? "bg-shell-surface-active" : "hover:bg-shell-surface-hover"
                    }`}
                    aria-label={`Channel ${ch.name}`}
                    title={isA2A ? "Agent coordination — mention @<slug> to hand off." : undefined}
                  >
                    {agentMember ? (
                      <MessageAvatar size={30} authorId={agentMember} displayName={agentMember} kind="agent" />
                    ) : isA2A ? (
                      <span className="shrink-0 grid place-items-center w-[30px] h-[30px] rounded-[9px] bg-accent-soft border border-accent-line text-accent-strong">
                        <Bot size={15} aria-hidden />
                      </span>
                    ) : (
                      <span className="shrink-0 grid place-items-center w-[30px] h-[30px] rounded-[9px] bg-shell-surface-active text-shell-text-secondary">
                        {ch.type === "group" ? <Users size={15} aria-hidden /> : <Hash size={15} aria-hidden />}
                      </span>
                    )}
                    <span className={`truncate flex-1 text-[14px] tracking-tight ${count > 0 ? "font-bold text-shell-text" : "font-semibold text-shell-text"}`}>
                      {ch.name}
                    </span>
                    {count > 0 && (
                      <span className="shrink-0 bg-unread text-white text-[10px] font-bold rounded-full min-w-[18px] h-[18px] flex items-center justify-center px-1 tabular-nums">
                        {count}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        {/* Projects section — desktop (standalone mode only) */}
        {!scope?.projectId && projectGroups.length > 0 && (
          <details className="px-3 mt-2">
            <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-white/30 py-1">
              Projects
            </summary>
            {projectGroups.map((g) => (
              <details key={g.id} className="ml-2 mt-1">
                <summary className="cursor-pointer text-xs text-white/60 py-1">{g.name}</summary>
                <div className="ml-2 mt-0.5">
                  {g.channels.map((ch) => (
                    <button
                      key={ch.id}
                      type="button"
                      onClick={() => setSelectedChannel(ch.id)}
                      aria-pressed={selectedChannel === ch.id}
                      aria-label={`Channel ${ch.name}`}
                      title={ch.settings?.kind === "a2a" ? "Agent coordination — mention @<slug> to hand off." : undefined}
                      className={`w-full text-left text-xs py-1 px-2 rounded flex items-center gap-1.5 ${
                        selectedChannel === ch.id ? "bg-white/10" : "hover:bg-white/5"
                      }`}
                    >
                      {ch.settings?.kind === "a2a" && (
                        <Bot
                          size={12}
                          aria-hidden
                          style={{ color: "rgba(255,255,255,0.6)", flexShrink: 0 }}
                        />
                      )}
                      {ch.name}
                    </button>
                  ))}
                </div>
              </details>
            ))}
          </details>
        )}

        {/* Archived channels section — desktop */}
        {archivedChannels.length > 0 && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setArchivedExpanded((v) => !v)}
              className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-white/25 hover:text-white/40 transition-colors w-full text-left"
              aria-expanded={archivedExpanded}
              aria-controls="archived-channels-desktop"
            >
              <ChevronRight size={11} className={`transition-transform ${archivedExpanded ? "rotate-90" : ""}`} aria-hidden="true" />
              <Archive size={11} aria-hidden="true" />
              Archived ({archivedChannels.length})
            </button>
            <div id="archived-channels-desktop" className={archivedExpanded ? "" : "hidden"}>
              {archivedChannels.map((ch) => {
                const agentId = ch.settings?.archived_agent_id;
                const hasAgent = agentId ? archivedAgents.some((a) => a.id === agentId) : false;
                return (
                  <div
                    key={ch.id}
                    className="group relative flex items-center opacity-60 hover:opacity-80 transition-opacity"
                  >
                    <Button
                      variant={selectedChannel === ch.id ? "secondary" : "ghost"}
                      onClick={() => setSelectedChannel(ch.id)}
                      className="flex-1 justify-start h-auto py-1.5 pl-3 pr-1 text-[13px] rounded-none font-normal min-w-0"
                      aria-label={`Archived channel ${ch.name}`}
                    >
                      <Archive size={11} className="shrink-0 mr-1.5 text-white/40" aria-hidden="true" />
                      <span className="truncate flex-1 text-left">{ch.name}</span>
                    </Button>
                    {/* Per-row actions — only visible on hover */}
                    <div className="hidden group-hover:flex items-center shrink-0 pr-1">
                      <button
                        type="button"
                        onClick={() => handleRestoreArchivedChannel(ch.id, ch.name)}
                        disabled={!hasAgent}
                        aria-label={`Restore archived channel ${ch.name}`}
                        title={hasAgent ? "Restore agent" : "Agent entry missing — delete only"}
                        className={`p-1 rounded transition-colors ${hasAgent ? "text-white/30 hover:text-emerald-400 hover:bg-emerald-500/10 cursor-pointer" : "text-white/15 cursor-not-allowed"}`}
                      >
                        <RotateCcw size={12} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteArchivedChannel(ch.id)}
                        aria-label={`Permanently delete archived channel ${ch.name}`}
                        title="Delete permanently"
                        className="p-1 rounded text-white/30 hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer"
                      >
                        <Trash2 size={12} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* External taOSmd coordination bus (read-only) */}
        <A2aBusSection
          channels={bus.channels}
          available={bus.available}
          loaded={bus.loaded}
          selected={busSelected}
          onSelect={selectBusChannel}
        />
      </div>
    </div>
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
        <div className="flex-1 flex items-center justify-center text-white/20">
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
          {/* channel header — MobileSplitView owns back nav on mobile */}
          <div className="px-4 py-2.5 border-b border-white/[0.06] flex items-center gap-3 shrink-0">
            {currentChannel?.type === "topic" ? <Hash size={16} className="text-white/40" /> :
             currentChannel?.type === "group" ? <Users size={16} className="text-white/40" /> :
             <AtSign size={16} className="text-white/40" />}
            {(() => {
              // For DM channels, prefix the header with the paired agent's
              // emoji (or framework default) so the user can see at a glance
              // who they are chatting with.
              if (currentChannel?.type !== "dm") return null;
              const agentName = (currentChannel.members ?? []).find((m) => m !== "user");
              if (!agentName) return null;
              const agent = liveAgents.find((a) => a.name === agentName);
              if (!agent) return null;
              return (
                <span
                  className="text-base leading-none shrink-0"
                  aria-hidden="true"
                >
                  {resolveAgentEmoji(agent.emoji, agent.framework)}
                </span>
              );
            })()}
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium truncate flex items-center gap-1">
                {currentChannel?.name ?? "Unknown"}
                {currentChannel && currentChannel.type !== "dm" && (
                  <button
                    aria-label="Channel settings"
                    onClick={handleOpenSettings}
                    className="ml-1 opacity-60 hover:opacity-100"
                  >ⓘ</button>
                )}
                <a
                  aria-label="Open chat guide"
                  href="https://github.com/jaylfc/tinyagentos/blob/master/docs/chat-guide.md"
                  target="_blank"
                  rel="noreferrer"
                  className="ml-1 opacity-60 hover:opacity-100 text-[12px]"
                >?</a>
                <div className="relative">
                  <PinBadge
                    count={pinnedMessages.length}
                    onClick={() => setPinnedPopoverOpen((open) => !open)}
                  />
                  {pinnedPopoverOpen && (
                    <PinnedMessagesPopover
                      pins={pinnedMessages}
                      authorCtx={{ currentUserId, currentUserDisplayName }}
                      onJumpTo={(id) => {
                        setPinnedPopoverOpen(false);
                        const el = document.querySelector(`[data-message-id="${id}"]`) as HTMLElement | null;
                        if (el) {
                          el.scrollIntoView({ behavior: "smooth", block: "center" });
                          el.classList.add("data-highlight");
                          setTimeout(() => el.classList.remove("data-highlight"), 2000);
                        } else {
                          // Only ~50 messages load; a pin older than that is not in the DOM.
                          setSendError("Message is older than the loaded history");
                        }
                      }}
                      onClose={() => setPinnedPopoverOpen(false)}
                    />
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (showAllThreads) {
                      setShowAllThreads(false);
                    } else {
                      closeThread();
                      setShowSettings(false);
                      setShowSearch(false);
                      setShowAllThreads(true);
                    }
                  }}
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
                  onClick={() => {
                    if (showSearch) {
                      setShowSearch(false);
                    } else {
                      closeThread();
                      setShowSettings(false);
                      setShowAllThreads(false);
                      setShowSearch(true);
                    }
                  }}
                  className="ml-2 p-1 rounded hover:bg-white/10 text-white/60 hover:text-white"
                  aria-label={showSearch ? "Hide search" : "Search messages"}
                  aria-expanded={showSearch}
                  aria-controls="search-panel"
                  title="Search"
                >
                  <Search size={14} aria-hidden="true" />
                </button>
              </div>
              {currentChannel?.description && (
                <div className="text-[11px] text-white/35 truncate">{currentChannel.description}</div>
              )}
            </div>
            {currentChannel?.members && (
              <div className="text-[11px] text-white/30 flex items-center gap-1">
                <Users size={12} /> {currentChannel.members.length}
              </div>
            )}
          </div>

          {/* message list — explicitly selectable. Most app shells set
              `select-none` for the native-OS feel; Messages is the exception
              where users expect to copy conversation text, so opt back in. */}
          <div
            ref={messageListRef}
            onScroll={handleScroll}
            className={`flex-1 overflow-y-auto px-4 py-3 space-y-0.5 select-text message-list-drop-target ${
              shellFileDropTarget.isOver
                ? "ring-2 ring-sky-400/60 ring-inset bg-sky-500/5"
                : shellFileDropTarget.isValidTarget
                ? "ring-2 ring-sky-400/30 ring-inset"
                : ""
            }`}
            style={isMobile && keyboardInset > 0 ? { paddingBottom: `${keyboardInset + 60}px` } : undefined}
            onDragEnter={shellFileDropTarget.dropHandlers.onDragEnter}
            onDragOver={(e) => {
              shellFileDropTarget.dropHandlers.onDragOver(e);
              if (!e.defaultPrevented) e.preventDefault();
            }}
            onDragLeave={shellFileDropTarget.dropHandlers.onDragLeave}
            onDrop={(e) => {
              // OS-level file drops (finder/explorer) take precedence.
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
            }}
          >
            {messages.length === 0 && fetchedChannel === selectedChannel && (
              <div className="flex flex-col items-center justify-center h-full text-white/25 text-center px-6">
                <MessageCircle size={40} className="mb-3 opacity-30" />
                <p className="text-sm">
                  No messages yet. Say hello to{" "}
                  {currentChannel?.type === "dm"
                    ? `@${(currentChannel.members ?? []).find((m) => m !== "user") ?? "them"}`
                    : currentChannel?.name
                      ? `#${currentChannel.name}`
                      : "this channel"}
                  .
                </p>
              </div>
            )}
            {messages.map((msg, i) => {
              const isAgent = msg.author_type === "agent";
              const prev = i > 0 ? messages[i - 1] : undefined;
              const showAuthor = !prev || prev.author_id !== msg.author_id;
              const prevDay = prev ? new Date(toMs(prev.created_at)).toDateString() : null;
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
                    <span className="text-[11px] text-white/40 font-medium">{dayLabel(msg.created_at)}</span>
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
                    <span className="text-[11px] text-red-400 font-semibold">New</span>
                    <div className="flex-1 h-px bg-red-400/40" />
                  </div>
                )}
                <div
                  data-message-id={msg.id}
                  className={`group relative flex gap-2.5 px-3 py-0.5 rounded-md transition-colors hover:bg-shell-surface ${showAuthor ? (isMobile ? "mt-2" : "mt-3") : ""}`}
                  onMouseEnter={() => setHoveredMessageId(msg.id)}
                  onMouseLeave={() => setHoveredMessageId((id) => id === msg.id ? null : id)}
                >
                  {/* avatar gutter */}
                  <div
                    className="flex-shrink-0 flex justify-end pt-0.5"
                    style={{ width: isMobile ? 34 : 38 }}
                    onContextMenu={(e) => {
                      if (msg.author_type !== "agent") return;
                      e.preventDefault();
                      setContextMenu({ slug: msg.author_id, x: e.clientX, y: e.clientY });
                    }}
                  >
                    {showAuthor ? (
                      (() => {
                        const agent = isAgent ? liveAgents.find((a) => a.name === msg.author_id) : undefined;
                        return (
                          <MessageAvatar
                            size={isMobile ? 34 : 38}
                            authorId={msg.author_id}
                            displayName={displayAuthor(msg, { currentUserId, currentUserDisplayName })}
                            kind={isAgent ? "agent" : "user"}
                            dead={isDeadAgent}
                            emoji={agent ? resolveAgentEmoji(agent.emoji, agent.framework) : isAgent ? resolveAgentEmoji(undefined, undefined) : undefined}
                          />
                        );
                      })()
                    ) : (
                      <span
                        className="text-[10px] leading-none text-shell-text-tertiary opacity-0 group-hover:opacity-100 transition-opacity self-center select-none"
                        aria-hidden="true"
                        title={new Date(toMs(msg.created_at)).toLocaleString()}
                      >
                        {new Date(toMs(msg.created_at)).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    )}
                  </div>
                  {/* content column */}
                  <div className="flex-1 min-w-0">
                  {showAuthor && (
                    <div
                      className="flex items-center gap-2 mb-0.5"
                      onContextMenu={(e) => {
                        if (msg.author_type !== "agent") return;
                        e.preventDefault();
                        setContextMenu({ slug: msg.author_id, x: e.clientX, y: e.clientY });
                      }}
                    >
                      <span
                        className={`${isMobile ? "text-[14px]" : "text-[15px]"} font-bold tracking-tight ${
                          isDeadAgent
                            ? "line-through text-shell-text-tertiary"
                            : "text-shell-text"
                        }`}
                        style={isDeadAgent ? { opacity: 0.55 } : undefined}
                        title={authorTooltip}
                      >
                        {displayAuthor(msg, { currentUserId, currentUserDisplayName })}
                      </span>
                      {isAgent && !isDeadAgent && (
                        <span className="text-[10px] uppercase tracking-wide bg-accent-soft text-accent-strong border border-accent-line px-1.5 py-0.5 rounded font-semibold flex items-center gap-0.5">
                          <Bot size={10} aria-hidden="true" /> Agent
                        </span>
                      )}
                      {isDeadAgent && (
                        <span className="text-[10px] uppercase tracking-wide bg-shell-surface-active text-shell-text-tertiary px-1.5 py-0.5 rounded font-semibold flex items-center gap-0.5">
                          <Bot size={10} aria-hidden="true" />
                          {authorState === "archived" ? "inactive" : "removed"}
                        </span>
                      )}
                      <span
                        className="text-[11px] text-shell-text-tertiary"
                        title={new Date(toMs(msg.created_at)).toLocaleString()}
                      >{relativeTime(msg.created_at, nowMs)}</span>
                      {msg.edited_at && <span className="text-[10px] text-shell-text-tertiary">(edited)</span>}
                    </div>
                  )}
                  {msg.deleted_at ? (
                    <MessageTombstone />
                  ) : editingMessageId === msg.id ? (
                    <MessageEditor
                      initial={msg.content}
                      onSave={(content) => handleSaveEdit(msg.id, content)}
                      onCancel={() => setEditingMessageId(null)}
                    />
                  ) : (
                    <div className={`${isMobile ? "text-[14px]" : "text-[15px]"} leading-[1.46] whitespace-pre-wrap break-words ${isDeadAgent ? "text-shell-text-secondary" : "text-shell-text"}`}>
                      {renderContent(msg.content)}
                      {msg.state === "pending" && (
                        <span className="ml-1 text-shell-text-tertiary">...</span>
                      )}
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
                    </div>
                  )}
                  {msg.metadata?.pin_requested && msg.author_type === "agent" && (
                    <PinRequestAffordance
                      authorId={msg.author_id}
                      onApprove={() => handlePinRequest(msg.id)}
                    />
                  )}

                  {/* canvas attachment */}
                  {msg.content_type === "canvas" && (msg.metadata?.canvas_url || msg.metadata?.canvas_id) && (
                    <div className="mt-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          const url = msg.metadata?.canvas_url ?? `/canvas/${msg.metadata?.canvas_id}`;
                          setViewingCanvas({ url, title: msg.metadata?.canvas_title as string | undefined });
                        }}
                        className="h-7 px-2.5 text-[12px] gap-1.5 bg-shell-surface border-shell-border-strong hover:bg-shell-surface-hover"
                        aria-label="View canvas"
                      >
                        <PanelRight size={13} />
                        View Canvas{msg.metadata?.canvas_title ? `: ${msg.metadata.canvas_title}` : ""}
                      </Button>
                    </div>
                  )}

                  {/* reactions */}
                  {msg.reactions && Object.keys(msg.reactions).length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {Object.entries(msg.reactions).map(([emoji, users]) => {
                        const mine = currentUserId != null && users.includes(currentUserId);
                        return (
                          <button
                            key={emoji}
                            onClick={() => toggleReaction(msg.id, emoji)}
                            aria-pressed={mine}
                            className={`text-[12px] rounded-full px-2 py-0.5 flex items-center gap-1 border transition-colors ${
                              mine
                                ? "bg-accent-soft border-accent-line text-accent-strong"
                                : "bg-shell-surface border-shell-border hover:bg-shell-surface-hover text-shell-text-secondary"
                            }`}
                          >
                            <span>{emoji}</span>
                            <span className={mine ? "text-accent-strong font-medium" : "text-shell-text-tertiary"}>{users.length}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {/* hover actions — always visible on mobile (no hover available), hover-gated on desktop */}
                  {(isMobile || hoveredMessageId === msg.id) && (() => {
                    const excerpt = (msg.content || "").slice(0, 80);
                    const msgChannelId = msg.channel_id ?? selectedChannel ?? "";
                    return (
                      <div className="absolute -top-3 right-2 z-10">
                        <MessageHoverActions
                          onReact={() => {
                            if (showEmoji && showEmoji.messageId === msg.id) {
                              setShowEmoji(null);
                              return;
                            }
                            const row = document.querySelector(`[data-message-id="${msg.id}"]`) as HTMLElement | null;
                            const rect = row?.getBoundingClientRect();
                            if (!rect) return;
                            setShowEmoji({ messageId: msg.id, rect });
                          }}
                          onReplyInThread={() => handleOpenThreadFor(msg.channel_id ?? selectedChannel ?? "", msg.id)}
                          onOverflow={(e) => {
                            e.preventDefault();
                            setOverflowMenu({ messageId: msg.id, x: e.clientX, y: e.clientY });
                          }}
                          dragHandle={msgChannelId ? (
                            <span
                              draggable
                              onDragStart={(e) => {
                                e.stopPropagation();
                                e.dataTransfer.effectAllowed = "copy";
                                try {
                                  e.dataTransfer.setData("text/plain", `@${msg.author_id}: ${excerpt}`);
                                  e.dataTransfer.setData("text/uri-list", `${window.location.origin}/chat/${msgChannelId}?msg=${msg.id}`);
                                } catch { /* best-effort */ }
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
                            >&#8942;&#8942;</span>
                          ) : undefined}
                        />
                      </div>
                    );
                  })()}
                  <AttachmentGallery attachments={(msg.attachments as AttachmentRecord[] | undefined) || []} />
                  {typeof msg.reply_count === "number" && msg.reply_count > 0 && (
                    <ThreadIndicator
                      replyCount={msg.reply_count}
                      lastReplyAt={msg.last_reply_at ?? null}
                      onOpen={() => handleOpenThreadFor(msg.channel_id ?? selectedChannel ?? "", msg.id)}
                    />
                  )}

                  {/* emoji picker — rendered in a portal to avoid clipping by the scrollable list */}
                  {showEmoji && showEmoji.messageId === msg.id && createPortal(
                    (() => {
                      const POPOVER_W = 300;
                      const POPOVER_H = 360;
                      const vw = window.innerWidth;
                      const vh = window.innerHeight;
                      const r = showEmoji.rect;
                      // Upper bounds are clamped to >=8 so a viewport smaller
                      // than the popover (with margins) cannot produce a
                      // negative limit and let Math.min return a value < 8.
                      const top = Math.max(8, Math.min(r.top, Math.max(8, vh - POPOVER_H - 8)));
                      const left = Math.max(8, Math.min(r.right - POPOVER_W, Math.max(8, vw - POPOVER_W - 8)));
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
                            onClick={() => toggleReaction(msg.id, em)}
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
                            toggleReaction(msg.id, d.emoji);
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
          <TypingFooter humans={typingHumans} agents={typingAgents} selfId="user" />

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
                className="shrink-0 p-0.5 rounded hover:bg-white/10 transition-colors"
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

          {/* pending attachments bar */}
          <AttachmentsBar
            items={pendingAttachments}
            onRemove={(id) => setPendingAttachments((p) => p.filter((x) => x.id !== id))}
            onRetry={(id) => {
              const entry = pendingAttachments.find((x) => x.id === id);
              if (!entry) return;
              if (!entry.file) {
                // Path-based attachment (no File kept): can only re-add.
                setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, error: "Can't retry, remove and re-add" } : x));
                return;
              }
              if ((entry.retries ?? 0) >= 3) return;
              setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, retries: (x.retries ?? 0) + 1 } : x));
              uploadFileAttachment(id, entry.file);
            }}
          />

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
                  members={currentChannel?.members || []}
                  onPick={(slug, cmd) => {
                    setInput(`@${slug} /${cmd} `);
                  }}
                  onClose={() => { /* leave input as-is; user can Esc or delete */ }}
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
                      onMouseEnter={() => setMentionSel(i)}
                      onMouseDown={(e) => { e.preventDefault(); insertMention(slug); }}
                      className={`w-full text-left px-3 py-1.5 flex items-center gap-2 ${i === mentionSel ? "bg-white/10" : "hover:bg-white/5"}`}
                    >
                      <AtSign size={13} className="text-white/40" aria-hidden="true" />
                      <span className="font-mono text-[13px]">@{slug}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className={`flex items-end gap-2 rounded-2xl border px-2 py-1.5 ${isCurrentArchived ? "bg-white/[0.02] border-white/[0.04] opacity-50" : "bg-shell-surface border-shell-border-strong"}`}>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleFileUpload}
                  className="h-8 w-8 shrink-0 mb-0.5"
                  aria-label="Upload file"
                  disabled={isCurrentArchived}
                >
                  <Paperclip size={16} />
                </Button>
                <Textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => !isCurrentArchived && handleInputChange(e.target.value)}
                  onKeyDown={(e) => !isCurrentArchived && handleKeyDown(e)}
                  onBlur={() => setMention(null)}
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
                  placeholder={isCurrentArchived ? "This chat is archived" : `Message #${currentChannel?.name ?? ""}...`}
                  rows={1}
                  disabled={isCurrentArchived}
                  className="flex-1 bg-transparent border-0 px-1 py-1.5 min-h-0 text-[13px] focus-visible:ring-0 focus-visible:border-0 max-h-[120px] disabled:cursor-not-allowed"
                  aria-label="Message input"
                />
                <Button
                  size="icon"
                  onClick={sendMessage}
                  disabled={(!input.trim() && pendingAttachments.length === 0) || isCurrentArchived || pendingAttachments.some(a => a.uploading)}
                  className="h-8 w-8 shrink-0 mb-0.5"
                  aria-label="Send message"
                >
                  <Send size={15} />
                </Button>
              </div>
            </div>
          </div>
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
    <div className="relative flex flex-col h-full bg-shell-base text-white overflow-hidden">
      {/* Toolbar — hidden on mobile when a channel is selected */}
      {showToolbar && (
        <div className="relative flex items-center px-3 py-2.5 border-b border-white/[0.06] shrink-0">
          {title ? (
            <>
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <span className="text-sm font-semibold text-white/90">{title}</span>
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
              <div className="flex items-center gap-2 text-sm font-medium text-white/80">
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
          detailTitle={busSelected ?? currentChannel?.name}
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
            topic: currentChannel.topic ?? "",
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
          className="fixed z-50 bg-shell-surface border border-white/10 rounded-lg shadow-xl p-3 text-xs min-w-[200px]"
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
          className="fixed inset-0 z-[10002] flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setViewingCanvas(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Canvas viewer"
        >
          <div
            className="w-[90vw] h-[85vh] max-w-5xl rounded-xl border border-white/10 overflow-hidden bg-shell-bg flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 shrink-0">
              <div className="flex items-center gap-2 text-sm text-white/80">
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
              className="flex-1 w-full border-none bg-white"
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
              className="absolute bottom-0 left-0 right-0 bg-shell-bg border-t border-white/[0.08] rounded-t-2xl p-4 space-y-3"
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
                  className="w-full bg-white/[0.06] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-accent-line"
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
          <div className="absolute inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <Card className="w-full max-w-[380px] max-h-full flex flex-col shadow-2xl bg-shell-bg">
              <CardHeader className="flex flex-row items-center justify-between gap-2 p-0 px-4 py-3 border-b border-white/[0.06]">
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
                    className="w-full bg-white/[0.06] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-accent-line"
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
