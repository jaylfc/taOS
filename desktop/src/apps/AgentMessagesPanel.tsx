import { useState, useEffect, useCallback } from "react";
import { Send, Loader2, ArrowRightLeft, Copy, Check } from "lucide-react";
import { Button, Card, Input, Textarea, Label } from "@/components/ui";

interface AgentMessageRaw {
  id: number | string;
  from?: string;
  to?: string;
  from_agent?: string;
  to_agent?: string;
  message: string;
  tool_calls?: unknown[];
  tool_results?: unknown[];
  reasoning?: string;
  depth: number;
  timestamp?: number;
  created_at?: number;
}

interface AgentMessage {
  id: string;
  from_agent: string;
  to_agent: string;
  message: string;
  tool_calls?: unknown[];
  tool_results?: unknown[];
  reasoning?: string;
  depth: number;
  created_at: number;
}

interface Props {
  agentName: string;
}

function normalizeMessage(raw: AgentMessageRaw): AgentMessage {
  return {
    id: String(raw.id),
    from_agent: raw.from_agent ?? raw.from ?? "",
    to_agent: raw.to_agent ?? raw.to ?? "",
    message: raw.message,
    tool_calls: raw.tool_calls ?? [],
    tool_results: raw.tool_results ?? [],
    reasoning: raw.reasoning ?? "",
    depth: raw.depth,
    created_at: raw.created_at ?? raw.timestamp ?? 0,
  };
}

function formatTime(ts: number): string {
  const delta = Date.now() - ts * 1000;
  if (delta < 60_000) return "just now";
  if (delta < 3600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86400_000) return `${Math.floor(delta / 3600_000)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function PreBlock({ content, label }: { content: unknown; label: string }) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(content, null, 2);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between mb-0.5">
        <p className="text-[10px] text-shell-text-tertiary uppercase tracking-wide">{label}</p>
        <button
          onClick={(e) => { e.stopPropagation(); handleCopy(); }}
          aria-label={copied ? "Copied" : `Copy ${label.toLowerCase()}`}
          className="p-0.5 rounded text-shell-text-tertiary hover:text-shell-text focus:outline-none focus:ring-1 focus:ring-accent/50 transition-colors"
        >
          {copied ? <Check size={10} /> : <Copy size={10} />}
        </button>
      </div>
      <pre className="text-[10px] text-shell-text-secondary p-2 rounded bg-shell-bg-deep border border-white/5 overflow-x-auto select-text whitespace-pre-wrap break-all">
        {text}
      </pre>
    </div>
  );
}

export function AgentMessagesPanel({ agentName }: Props) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [fromAgent, setFromAgent] = useState("user");
  const [content, setContent] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/messages?limit=50&depth=3`,
      );
      if (res.ok && (res.headers.get("content-type") ?? "").includes("application/json")) {
        const data = await res.json();
        const rawList: AgentMessageRaw[] = Array.isArray(data)
          ? data
          : (data.messages ?? []);
        setMessages(rawList.map(normalizeMessage));
      } else {
        setMessages([]);
      }
    } catch {
      setMessages([]);
    }
    setLoading(false);
  }, [agentName]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10_000);
    return () => clearInterval(interval);
  }, [load]);

  const send = async () => {
    if (!content.trim()) return;
    setSending(true);
    try {
      await fetch(`/api/agents/${encodeURIComponent(agentName)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_agent: fromAgent,
          message: content,
          depth: 2,
        }),
      });
      setContent("");
      await load();
    } catch {
      // ignore
    }
    setSending(false);
  };

  const toggleExpand = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 size={20} className="animate-spin text-shell-text-tertiary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-shell-text-tertiary gap-2">
            <ArrowRightLeft size={24} />
            <p className="text-sm">No messages yet</p>
            <p className="text-xs">Inter-agent messages will appear here</p>
          </div>
        ) : (
          messages.map((msg) => {
            const isOpen = expanded[msg.id];
            const hasDetails =
              (msg.tool_calls && msg.tool_calls.length > 0) ||
              (msg.tool_results && msg.tool_results.length > 0) ||
              (msg.reasoning && msg.reasoning.length > 0);
            return (
              <Card
                key={msg.id}
                className="p-3 cursor-pointer hover:bg-shell-surface/40 transition-colors"
                onClick={() => hasDetails && toggleExpand(msg.id)}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-shell-text-secondary font-medium">
                    {msg.from_agent} &rarr; {msg.to_agent}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-shell-text-tertiary">
                      depth {msg.depth}
                    </span>
                    <span className="text-[10px] text-shell-text-tertiary">
                      {formatTime(msg.created_at)}
                    </span>
                  </div>
                </div>
                <p
                  className={`text-sm text-shell-text whitespace-pre-wrap select-text ${
                    isOpen ? "" : "line-clamp-3"
                  }`}
                >
                  {msg.message}
                </p>
                {isOpen && msg.reasoning && (
                  <div className="mt-2">
                    <p className="text-[10px] text-shell-text-tertiary uppercase tracking-wide mb-0.5">
                      Reasoning
                    </p>
                    <p className="text-xs text-shell-text-secondary pl-2 border-l border-white/10 whitespace-pre-wrap select-text">
                      {msg.reasoning}
                    </p>
                  </div>
                )}
                {isOpen && msg.tool_calls && msg.tool_calls.length > 0 && (
                  <PreBlock content={msg.tool_calls} label="Tool Calls" />
                )}
                {isOpen &&
                  msg.tool_results &&
                  msg.tool_results.length > 0 && (
                    <PreBlock content={msg.tool_results} label="Tool Results" />
                  )}
              </Card>
            );
          })
        )}
      </div>

      {/* Send form */}
      <div className="border-t border-white/10 p-3 space-y-2 bg-shell-surface/30 shrink-0">
        <div className="flex gap-2">
          <div className="flex-1">
            <Label htmlFor="from-agent" className="text-[10px] mb-0.5 block">
              From
            </Label>
            <Input
              id="from-agent"
              value={fromAgent}
              onChange={(e) => setFromAgent(e.target.value)}
              placeholder="user"
            />
          </div>
        </div>
        <div>
          <Label htmlFor="msg-content" className="text-[10px] mb-0.5 block">
            Message
          </Label>
          <Textarea
            id="msg-content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={`Send a message to ${agentName}...`}
            rows={3}
          />
        </div>
        <Button
          onClick={send}
          disabled={sending || !content.trim()}
          size="sm"
          className="w-full"
        >
          {sending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Send size={14} />
          )}
          Send to {agentName}
        </Button>
      </div>
    </div>
  );
}
