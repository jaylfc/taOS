import { useEffect, useRef, useState, useCallback } from "react";
import {
  ArrowUp,
  Sparkles,
  X,
  Settings,
  Paperclip,
  Camera,
  MonitorUp,
  ExternalLink,
  Copy,
  Check,
} from "lucide-react";
import { CodeBlock } from "@/components/CodeBlock";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { TaosAssistantSettings } from "./TaosAssistantSettings";
import {
  takeChatScreenshot,
  uploadChatAttachment,
  type AttachmentRecord,
} from "@/lib/taos-agent-api";
import {
  grantScreenCapture,
  hasScreenCapture,
  revokeScreenCapture,
  SCREEN_CAPTURE_CHANGED_EVENT,
} from "@/lib/screen-capture";
import { useProcessStore } from "@/stores/process-store";

export interface PendingAttachment {
  id: string;
  filename: string;
  size: number;
  uploading: boolean;
  record?: AttachmentRecord;
  error?: string;
}

export function TaosAssistantPanelInner({ embedded = false }: { embedded?: boolean } = {}) {
  const {
    isOpen,
    messages,
    appendMessage,
    appendDelta,
    model,
    setModel,
    streaming,
    setStreaming,
    settingsOpen,
    setSettingsOpen,
  } = useTaosAgentStore();

  const [input, setInput] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const noModel = !model;
  const showEmptyState = messages.length === 0;

  // Sync model from backend when panel opens (or when shown in the pop-out window)
  useEffect(() => {
    if (!isOpen && !embedded) return;
    fetch("/api/taos-agent/settings")
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.model !== undefined) setModel(data.model);
      })
      .catch(() => {});
  }, [isOpen, embedded, setModel]);

  // Auto-scroll on new content
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingAttachments.length]);

  /* ---------------------------------------------------------------- */
  /*  Core send logic — shared by manual send and screenshot           */
  /* ---------------------------------------------------------------- */

  const doSend = useCallback(
    async (text: string, attachments_at_send: PendingAttachment[]) => {
      const history = useTaosAgentStore.getState().messages;
      const historyPayload = history
        .filter((m) => m.role !== "system")
        .slice(0, -1)
        .map((m) => ({ role: m.role, content: m.content }));

      const imageAttachments = attachments_at_send
        .filter((p) => p.record && p.record.mime_type.startsWith("image/"))
        .map((p) => ({
          mime_type: p.record!.mime_type,
          size: p.record!.size,
          url: p.record!.url,
          filename: p.record!.filename,
        }));

      const resp = await fetch("/api/taos-agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [...historyPayload, { role: "user", content: text }],
          attachments: imageAttachments.length > 0 ? imageAttachments : undefined,
        }),
      });

      if (!resp.ok || !resp.body) {
        appendDelta(`\n\n_Error: ${(await resp.text().catch(() => "Unknown error"))}_`);
        setStreaming(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const obj = JSON.parse(line);
            if (obj.error) appendDelta(`\n\n_Error: ${obj.error}_`);
            else if (obj.delta) appendDelta(obj.delta);
          } catch {
            // skip malformed NDJSON
          }
        }
      }
    },
    [appendDelta, setStreaming],
  );

  /* ---------------------------------------------------------------- */
  /*  Screenshot                                                       */
  /* ---------------------------------------------------------------- */

  const handleScreenshot = useCallback(async () => {
    if (streaming) return;
    try {
      const resp = await takeChatScreenshot();
      const form = new FormData();
      form.append("file", new File([resp.blob], "screenshot.png", { type: resp.mime_type }));
      const uploaded = await uploadChatAttachment(form);
      const snap: PendingAttachment = {
        id: crypto.randomUUID(),
        filename: uploaded.filename,
        size: uploaded.size,
        uploading: false,
        record: uploaded as AttachmentRecord,
      };
      // Add to pending bar — user can review before sending, or send immediately
      setPendingAttachments((p) => [...p, snap]);
      appendMessage({ role: "user", content: "[screenshot]", ts: Date.now() });
      appendMessage({ role: "assistant", content: "", ts: Date.now() });
      setStreaming(true);
      try {
        await doSend("[screenshot]", [snap]);
      } finally {
        setStreaming(false);
        setPendingAttachments((p) => p.filter((x) => x.id !== snap.id));
      }
    } catch (e) {
      // User cancelled the screen-share picker or permission was denied — no crash
      const err = e as { name?: string };
      if (err?.name !== "NotAllowedError" && err?.name !== "AbortError") {
        appendDelta(`\n\n_Screenshot error: ${String(e)}_`);
      }
      setStreaming(false);
    }
  }, [streaming, appendMessage, appendDelta, setStreaming, doSend]);

  /* ---------------------------------------------------------------- */
  /*  File upload                                                      */
  /* ---------------------------------------------------------------- */

  const handleFileUpload = useCallback(async () => {
    const el = document.createElement("input");
    el.type = "file";
    el.accept = "image/*,*/*";
    el.multiple = true;
    el.onchange = async () => {
      const files = Array.from(el.files ?? []);
      for (const file of files) {
        const id = crypto.randomUUID();
        setPendingAttachments((p) => [
          ...p,
          { id, filename: file.name, size: file.size, uploading: true },
        ]);
        try {
          const form = new FormData();
          form.append("file", file);
          const record = await uploadChatAttachment(form);
          setPendingAttachments((p) =>
            p.map((x) =>
              x.id === id
                ? { ...x, record: record as AttachmentRecord, uploading: false }
                : x,
            ),
          );
        } catch (err) {
          setPendingAttachments((p) =>
            p.map((x) =>
              x.id === id
                ? { ...x, uploading: false, error: (err as Error).message }
                : x,
            ),
          );
        }
      }
    };
    el.click();
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Send                                                            */
  /* ---------------------------------------------------------------- */

  const sendMessage = useCallback(async () => {
    if (streaming) return;
    const text = input.trim() || "[empty message]";
    setInput("");
    const currentAttachments = pendingAttachments;
    setPendingAttachments([]);
    appendMessage({ role: "user", content: text, ts: Date.now() });
    appendMessage({ role: "assistant", content: "", ts: Date.now() });
    setStreaming(true);
    try {
      await doSend(text, currentAttachments);
    } catch (e) {
      appendDelta(`\n\n_Network error: ${String(e)}_`);
    } finally {
      setStreaming(false);
    }
  }, [input, streaming, pendingAttachments, appendMessage, appendDelta, setStreaming, doSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        sendMessage();
      }
    },
    [sendMessage],
  );

  /* ---------------------------------------------------------------- */
  /*  Render                                                          */
  /* ---------------------------------------------------------------- */

  // When embedded in the pop-out window the docked panel is closed (isOpen=false),
  // so only gate on isOpen for the docked panel, never for the pop-out.
  if (!isOpen && !embedded) return null;

  return (
    <>
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 min-h-0">
        {showEmptyState && noModel && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
            <Sparkles size={32} className="text-accent opacity-50" />
            <p className="text-sm text-shell-text-secondary">Pick a model to get started</p>
            <button
              className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors"
              onClick={() => setSettingsOpen(true)}
            >
              Choose a model
            </button>
          </div>
        )}

        {showEmptyState && !noModel && (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
            <Sparkles size={32} className="text-accent opacity-50" />
            <p className="text-sm text-shell-text-secondary">Ask me anything about taOS.</p>
            <p className="text-xs text-shell-text-tertiary">Cmd+Enter to send</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            role={msg.role}
            content={msg.content}
            streaming={streaming && i === messages.length - 1 && msg.role === "assistant"}
          />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Pending attachments bar */}
      {pendingAttachments.length > 0 && (
        <div className="px-4 py-2 border-t border-white/5 shrink-0 flex gap-2 flex-wrap">
          {pendingAttachments.map((a) => {
            const rec = a.record;
            return (
              <div
                key={a.id}
                className={`relative rounded-lg border border-white/10 overflow-hidden flex items-center gap-2 px-2 py-1 text-xs ${a.uploading ? "opacity-60" : ""}`}
                style={{ maxWidth: 160 }}
              >
                {rec?.mime_type?.startsWith("image/") && rec.url ? (
                  <img src={rec.url} alt="" className="w-8 h-8 object-cover rounded shrink-0" />
                ) : (
                  <Paperclip size={12} className="shrink-0 text-shell-text-tertiary" />
                )}
                <span className="truncate max-w-[80px]">{a.filename}</span>
                {a.error && <span className="text-red-400 ml-auto">{a.error}</span>}
                <button
                  onClick={() => setPendingAttachments((p) => p.filter((x) => x.id !== a.id))}
                  className="ml-auto text-shell-text-tertiary hover:text-white"
                  aria-label="Remove attachment"
                >
                  <X size={10} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Input area */}
      <div
        className="px-4 py-3 border-t border-white/5 shrink-0"
        style={{ paddingBottom: embedded ? "0.75rem" : "calc(0.75rem + var(--spacing-dock-h, 52px))" }}
      >
        {noModel && (
          <p className="text-xs text-shell-text-tertiary mb-2">
            No model selected.{" "}
            <button
              className="underline hover:text-shell-text transition-colors"
              onClick={() => setSettingsOpen(true)}
            >
              Choose one
            </button>
          </p>
        )}
        {/* One unified composer: attach + screenshot inside on the left, the
            field borderless in the middle, send on the right. Bottom-aligned so
            the icons sit on the last line as the textarea grows (chat-app
            convention). Shell tokens only, so it flips for the Light theme. */}
        <div className="flex items-end gap-1 rounded-2xl border border-shell-border bg-shell-bg-deep px-2 py-1.5 transition-colors focus-within:border-accent/50">
          <AttachButton onClick={handleFileUpload} disabled={noModel || streaming} />
          <ScreenshotButton onClick={handleScreenshot} disabled={noModel || streaming} />
          <ScreenCaptureGrantButton />

          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={noModel ? "Choose a model first…" : "Ask taOS agent…"}
            disabled={noModel || streaming}
            rows={1}
            aria-label="Message taOS agent"
            className="flex-1 resize-none border-none bg-transparent text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none px-1.5 py-2 disabled:opacity-40"
            style={{ minHeight: 36, maxHeight: 160 }}
          />
          <SendButton onClick={sendMessage} disabled={!input.trim() || noModel || streaming} />
        </div>
        <p className="text-[10px] text-shell-text-tertiary mt-1.5 pr-1 text-right">Cmd+Enter to send</p>
      </div>

      {/* Settings modal */}
      <TaosAssistantSettings open={settingsOpen} onClose={() => setSettingsOpen(false)} />

      {/* Keyframes */}
      <style>{`
        @keyframes taos-assistant-slidein {
          from { transform: translateX(100%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
      `}</style>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Slide-over wrapper                                                  */
/* ------------------------------------------------------------------ */

export function TaosAssistantPanel() {
  const store = useTaosAgentStore();
  if (!store.isOpen) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-[100]"
        onClick={store.closePanel}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-label="taOS agent"
        aria-modal="true"
        className="fixed right-0 z-[101] flex flex-col border-l border-white/10 shadow-2xl"
        style={{
          top: "var(--spacing-topbar-h)",
          bottom: 0,
          width: 420,
          backgroundColor: "rgba(21, 22, 37, 0.92)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          animation: "taos-assistant-slidein 300ms ease-out",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <HeaderBar />
        <TaosAssistantPanelInner />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Re-usable UI primitives                                             */
/* ------------------------------------------------------------------ */

function HeaderBar({}: { onClose?: () => void } = {}) {
  const { closePanel, setSettingsOpen } = useTaosAgentStore();
  const openWindow = useProcessStore((s) => s.openWindow);

  const handlePopOut = useCallback(() => {
    closePanel();
    setTimeout(() => {
      openWindow("taos-agent", { w: 420, h: 640 }, { popOut: true });
    }, 100);
  }, [openWindow, closePanel]);

  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0 select-none">
      <Sparkles size={15} className="text-accent shrink-0" />
      <span className="text-sm font-semibold flex-1">taOS agent</span>
      <ToolbarButton label="Pop out" icon={<ExternalLink size={14} />} onClick={handlePopOut} />
      <ToolbarButton label="Assistant settings" icon={<Settings size={14} />} onClick={() => setSettingsOpen(true)} />
      <ToolbarButton
        label="Close taOS agent"
        icon={<X size={14} />}
        onClick={closePanel}
      />
    </div>
  );
}

function SendButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label="Send message"
      className="grid place-items-center h-8 w-8 rounded-xl bg-accent text-white hover:brightness-110 transition disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
    >
      <ArrowUp size={16} strokeWidth={2.4} />
    </button>
  );
}

function AttachButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label="Upload file"
      title="Attach file"
      className="grid place-items-center h-8 w-8 rounded-lg text-shell-text-tertiary hover:bg-shell-surface-hover hover:text-shell-text-secondary transition-colors shrink-0 disabled:opacity-40"
    >
      <Paperclip size={16} />
    </button>
  );
}

/**
 * One-time screen-capture grant. getDisplayMedia needs a user gesture, so the
 * user clicks this once; the stream then persists and agent screenshots
 * (/api/desktop/screenshot) grab full-fidelity frames (incl. cross-origin
 * iframes) with no further prompt. Click again to stop sharing.
 */
function ScreenCaptureGrantButton() {
  const [granted, setGranted] = useState(hasScreenCapture());
  // Stay in sync when the share ends from anywhere (native browser bar, another
  // control), not just our own click.
  useEffect(() => {
    const sync = () => setGranted(hasScreenCapture());
    window.addEventListener(SCREEN_CAPTURE_CHANGED_EVENT, sync);
    return () => window.removeEventListener(SCREEN_CAPTURE_CHANGED_EVENT, sync);
  }, []);
  const toggle = useCallback(async () => {
    if (hasScreenCapture()) {
      revokeScreenCapture();
    } else {
      await grantScreenCapture();
    }
  }, []);
  return (
    <button
      onClick={toggle}
      aria-label={granted ? "Stop agent screen capture" : "Allow agent screen capture"}
      title={
        granted
          ? "Agent screen capture ON -- click to stop sharing"
          : "Allow agents to capture full-fidelity screenshots of this screen"
      }
      className={[
        "grid place-items-center h-8 w-8 rounded-lg transition-colors shrink-0",
        granted
          ? "text-accent hover:bg-shell-surface-hover"
          : "text-shell-text-tertiary hover:bg-shell-surface-hover hover:text-shell-text-secondary",
      ].join(" ")}
    >
      <MonitorUp size={16} />
    </button>
  );
}

function ScreenshotButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label="Take screenshot"
      title="Take screenshot"
      className="grid place-items-center h-8 w-8 rounded-lg text-shell-text-tertiary hover:bg-shell-surface-hover hover:text-shell-text-secondary transition-colors shrink-0 disabled:opacity-40"
    >
      <Camera size={16} />
    </button>
  );
}

function ToolbarButton({
  label,
  icon,
  onClick,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className="p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
    >
      {icon}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Message bubble                                                     */
/* ------------------------------------------------------------------ */

function renderBubbleContent(text: string): (string | React.ReactElement)[] {
  const result: (string | React.ReactElement)[] = [];
  const fenceRegex = /```(?:[^\n]*)?\n([\s\S]*?)```/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = fenceRegex.exec(text)) !== null) {
    if (match.index > last) result.push(text.slice(last, match.index));
    result.push(<CodeBlock key={`cb-${key++}`} code={match[1] ?? ""} />);
    last = match.index + match[0].length;
  }
  if (last < text.length) result.push(text.slice(last));
  return result;
}

function MessageBubble({
  role,
  content,
  streaming,
}: {
  role: "user" | "assistant" | "system";
  content: string;
  streaming?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  if (role === "system") return null;

  const isUser = role === "user";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} group`}>
      <div
        className={`relative max-w-[85%] rounded-xl px-3 py-2 text-sm break-words select-text ${
          isUser ? "bg-accent text-white" : "bg-shell-surface-hover text-shell-text"
        }`}
      >
        <div className="whitespace-pre-wrap">
          {renderBubbleContent(content)}
        </div>
        {streaming && !content && (
          <span className="inline-block w-2 h-3 bg-current opacity-60 animate-pulse ml-0.5 rounded-sm" />
        )}
        {streaming && content && (
          <span className="inline-block w-1.5 h-3 bg-current opacity-60 animate-pulse ml-0.5 rounded-sm" />
        )}
        {!streaming && content && (
          <button
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy message"}
            className="absolute -top-2 -right-2 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 bg-shell-surface border border-white/10 text-shell-text-secondary hover:text-shell-text transition-opacity select-none"
          >
            {copied ? <Check size={10} /> : <Copy size={10} />}
          </button>
        )}
      </div>
    </div>
  );
}
