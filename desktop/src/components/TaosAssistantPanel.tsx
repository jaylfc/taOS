import { useEffect, useRef, useState, useCallback } from "react";
import { X, Settings, Send, Sparkles } from "lucide-react";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { TaosAssistantSettings } from "./TaosAssistantSettings";

export function TaosAssistantPanel() {
  const {
    isOpen,
    closePanel,
    messages,
    appendMessage,
    appendDelta,
    model,
    setModel,
    streaming,
    setStreaming,
  } = useTaosAgentStore();

  const [input, setInput] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync model from backend on first open
  useEffect(() => {
    if (!isOpen) return;
    fetch("/api/taos-agent/settings")
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.model !== undefined) setModel(data.model);
      })
      .catch(() => {});
  }, [isOpen, setModel]);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = useCallback(async () => {
    const content = input.trim();
    if (!content || streaming) return;

    setInput("");

    appendMessage({ role: "user", content, ts: Date.now() });
    appendMessage({ role: "assistant", content: "", ts: Date.now() });
    setStreaming(true);

    // Build messages payload — only user/assistant (no system; backend injects system)
    const history = useTaosAgentStore.getState().messages;
    const payload = history
      .filter((m) => m.role !== "system")
      .slice(0, -1) // exclude the placeholder assistant we just added
      .map((m) => ({ role: m.role, content: m.content }));
    payload.push({ role: "user", content });

    try {
      const resp = await fetch("/api/taos-agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: payload }),
      });

      if (!resp.ok || !resp.body) {
        const err = await resp.text().catch(() => "Unknown error");
        appendDelta(`\n\n_Error: ${err}_`);
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
            const obj = JSON.parse(line) as { delta?: string; done?: boolean; error?: string };
            if (obj.error) {
              appendDelta(`\n\n_Error: ${obj.error}_`);
            } else if (obj.delta) {
              appendDelta(obj.delta);
            }
            // obj.done == true means stream is complete — loop ends naturally
          } catch {
            // skip malformed lines
          }
        }
      }
    } catch (e) {
      appendDelta(`\n\n_Network error: ${String(e)}_`);
    } finally {
      setStreaming(false);
    }
  }, [input, streaming, appendMessage, appendDelta, setStreaming]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        sendMessage();
      }
    },
    [sendMessage],
  );

  const noModel = !model;
  const showEmptyState = messages.length === 0;

  if (!isOpen) return null;

  return (
    <>
      {/* Transparent backdrop — click to close */}
      <div
        className="fixed inset-0 z-[100]"
        onClick={closePanel}
        aria-hidden="true"
      />

      {/* Slide-over panel.
          bg-shell-surface (4% white) is fine for inline cards layered
          inside windows but is unreadable as a top-level slide-over
          against the wallpaper. Use a heavy dark glass instead — solid
          enough that error/log text reads cleanly, with a hint of
          backdrop blur for the macOS-style sidebar feel. */}
      <div
        role="dialog"
        aria-label="taOS Assistant"
        aria-modal="true"
        className="fixed right-0 z-[101] flex flex-col border-l border-white/10 shadow-2xl"
        style={{
          // Fill from below the topbar to the bottom of the viewport.
          // The dock floats above this with its own z-index, so the
          // panel doesn't need to leave a hole for it. Removing the
          // bottom: dock-h gap closes the empty strip jay reported.
          top: "var(--spacing-topbar-h)",
          bottom: 0,
          width: 400,
          backgroundColor: "rgba(21, 22, 37, 0.92)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          animation: "taos-assistant-slidein 300ms ease-out",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0">
          <Sparkles size={15} className="text-accent shrink-0" />
          <span className="text-sm font-semibold flex-1">taOS Assistant</span>
          <button
            className="p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
            aria-label="Assistant settings"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings size={14} />
          </button>
          <button
            className="p-1 rounded hover:bg-shell-surface-hover transition-colors text-shell-text-secondary"
            aria-label="Close taOS Assistant"
            onClick={closePanel}
          >
            <X size={14} />
          </button>
        </div>

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
              <p className="text-sm text-shell-text-secondary">
                Ask me anything about taOS.
              </p>
              <p className="text-xs text-shell-text-tertiary">
                Cmd+Enter to send
              </p>
            </div>
          )}

          {messages.map((msg, i) => (
            <MessageBubble key={i} role={msg.role} content={msg.content} streaming={streaming && i === messages.length - 1 && msg.role === "assistant"} />
          ))}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area — extra bottom padding equal to the dock height
            so the textarea + send button never sit under the floating
            dock at the bottom-right corner of the screen. */}
        <div
          className="px-4 py-3 border-t border-white/5 shrink-0"
          style={{ paddingBottom: "calc(0.75rem + var(--spacing-dock-h, 52px))" }}
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
          <div className="flex gap-2 items-end">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={noModel ? "Choose a model first…" : "Ask taOS Assistant…"}
              disabled={noModel || streaming}
              rows={2}
              aria-label="Message taOS Assistant"
              className="flex-1 resize-none rounded-lg border border-white/10 bg-shell-bg-deep text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/40 px-3 py-2 disabled:opacity-40"
              style={{ minHeight: 64, maxHeight: 160 }}
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || noModel || streaming}
              aria-label="Send message"
              className="p-2.5 rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              <Send size={14} />
            </button>
          </div>
          <p className="text-[10px] text-shell-text-tertiary mt-1">Cmd+Enter to send</p>
        </div>
      </div>

      {/* Settings modal — rendered above the panel */}
      <TaosAssistantSettings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />

      {/* Slide-in keyframe */}
      <style>{`
        @keyframes taos-assistant-slidein {
          from { transform: translateX(100%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
      `}</style>
    </>
  );
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
  if (role === "system") return null;

  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap break-words ${
          isUser
            ? "bg-accent text-white"
            : "bg-shell-surface-hover text-shell-text"
        }`}
      >
        {content}
        {streaming && !content && (
          <span className="inline-block w-2 h-3 bg-current opacity-60 animate-pulse ml-0.5 rounded-sm" />
        )}
        {streaming && content && (
          <span className="inline-block w-1.5 h-3 bg-current opacity-60 animate-pulse ml-0.5 rounded-sm" />
        )}
      </div>
    </div>
  );
}
