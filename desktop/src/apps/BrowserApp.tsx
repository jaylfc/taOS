import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  RotateCw,
  Globe,
  ExternalLink,
  Copy,
  Bookmark,
  Bot,
  Star,
  AlertTriangle,
  Loader2,
} from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  Label,
  Textarea,
  Toolbar,
  ToolbarGroup,
} from "@/components/ui";

const DEFAULT_URL = "https://duckduckgo.com";

const BOOKMARKS = [
  { name: "DuckDuckGo", url: "https://duckduckgo.com", icon: "🦆" },
  { name: "Wikipedia", url: "https://en.wikipedia.org", icon: "📚" },
  { name: "GitHub", url: "https://github.com", icon: "🐙" },
  { name: "Hacker News", url: "https://news.ycombinator.com", icon: "🟧" },
];

function proxyUrl(url: string): string {
  return `/api/desktop/proxy?url=${encodeURIComponent(url)}`;
}

function normalizeUrl(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) return DEFAULT_URL;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

function isIOS(): boolean {
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

type BrowserMode = "embedded" | "external";

export function BrowserApp({ windowId: _windowId, initialUrl }: { windowId?: string; initialUrl?: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const initialUrlApplied = useRef(false);
  const [url, setUrl] = useState(DEFAULT_URL);
  const [inputValue, setInputValue] = useState(DEFAULT_URL);
  const [history, setHistory] = useState<string[]>([DEFAULT_URL]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const [loadError, setLoadError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const [agentCommand, setAgentCommand] = useState("");
  const [agentResult, setAgentResult] = useState<string | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [mode, setMode] = useState<BrowserMode>(
    isIOS() ? "external" : "embedded",
  );

  const canGoBack = historyIndex > 0;
  const canGoForward = historyIndex < history.length - 1;

  const navigate = useCallback(
    (newUrl: string) => {
      const normalized = normalizeUrl(newUrl);
      setUrl(normalized);
      setInputValue(normalized);
      setLoadError(false);
      setLoading(true);
      setHistory((prev) => [...prev.slice(0, historyIndex + 1), normalized]);
      setHistoryIndex((i) => i + 1);
    },
    [historyIndex],
  );

  const goBack = useCallback(() => {
    if (!canGoBack) return;
    const newIndex = historyIndex - 1;
    setHistoryIndex(newIndex);
    const target = history[newIndex] ?? DEFAULT_URL;
    setUrl(target);
    setInputValue(target);
    setLoadError(false);
    setLoading(true);
  }, [canGoBack, history, historyIndex]);

  const goForward = useCallback(() => {
    if (!canGoForward) return;
    const newIndex = historyIndex + 1;
    setHistoryIndex(newIndex);
    const target = history[newIndex] ?? DEFAULT_URL;
    setUrl(target);
    setInputValue(target);
    setLoadError(false);
    setLoading(true);
  }, [canGoForward, history, historyIndex]);

  const refresh = useCallback(() => {
    setLoadError(false);
    setLoading(true);
    if (iframeRef.current) {
      iframeRef.current.src = proxyUrl(url);
    }
  }, [url]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate(inputValue);
  };

  const openInTab = useCallback(() => {
    window.open(url, "_blank", "noopener,noreferrer");
  }, [url]);

  const copyUrl = useCallback(() => {
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [url]);

  const handleIframeLoad = useCallback(() => {
    setLoading(false);
    // Check if iframe loaded successfully by trying to detect error responses
    try {
      const iframe = iframeRef.current;
      if (iframe) {
        // We can't access cross-origin content, but if the proxy returned
        // an error JSON, it will be same-origin and we can check
        try {
          const doc = iframe.contentDocument;
          if (doc) {
            const body = doc.body?.textContent?.trim() ?? "";
            // Proxy returns JSON errors like {"error": "..."}
            if (body.startsWith("{") && body.includes('"error"')) {
              setLoadError(true);
              return;
            }
          }
        } catch {
          // Cross-origin access blocked — means content loaded (good)
        }
      }
    } catch {
      // Ignore any inspection errors
    }
  }, []);

  const handleIframeError = useCallback(() => {
    setLoading(false);
    setLoadError(true);
  }, []);

  // Auto-detect: if on iOS, start in external mode
  useEffect(() => {
    if (isIOS()) {
      setMode("external");
    }
  }, []);

  // Apply initialUrl once on mount — idempotent across rerenders.
  // Also sync url/inputValue/history so the address bar, copy, and
  // back/forward all reflect the launch URL rather than DEFAULT_URL.
  useEffect(() => {
    if (!initialUrl || initialUrlApplied.current) return;
    try {
      const parsed = new URL(initialUrl, window.location.href);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        console.warn("[BrowserApp] rejected initialUrl with non-http(s) scheme:", parsed.protocol);
        return;
      }
      initialUrlApplied.current = true;
      const safe = parsed.toString();
      setUrl(safe);
      setInputValue(safe);
      setHistory([safe]);
      setHistoryIndex(0);
      if (iframeRef.current) {
        // initialUrl is set directly on the iframe (NOT via proxyUrl) because the
        // agent-shortcuts feature redirects the browser straight to the worker — the
        // worker sets its own session cookie via /redeem and serves dashboard
        // content directly. Routing through the controller's proxyUrl wrapping would
        // break that worker-direct cookie flow.
        iframeRef.current.src = safe;
      }
    } catch (err) {
      console.warn("[BrowserApp] invalid initialUrl:", initialUrl, err);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When in external mode and URL changes, don't auto-open — user clicks the button
  const toggleMode = useCallback(() => {
    setMode((m) => (m === "embedded" ? "external" : "embedded"));
    setLoadError(false);
  }, []);

  const runAgentCommand = useCallback(async () => {
    if (!agentCommand.trim()) return;
    setAgentLoading(true);
    setAgentResult(null);
    try {
      const res = await fetch("/api/desktop/browser/agent-command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, command: agentCommand }),
      });
      const data = await res.json();
      if (data.error) {
        setAgentResult(
          `Error: ${data.error}${data.install ? ` (Install: ${data.install})` : ""}`,
        );
      } else {
        setAgentResult(data.message || "Command sent");
      }
    } catch (e) {
      setAgentResult(`Error: ${e}`);
    }
    setAgentLoading(false);
  }, [agentCommand, url]);

  return (
    <div className="flex flex-col h-full">
      {/* Navigation toolbar */}
      <form onSubmit={handleSubmit}>
        <Toolbar>
          <ToolbarGroup>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={goBack}
              disabled={!canGoBack}
              aria-label="Back"
            >
              <ArrowLeft size={16} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={goForward}
              disabled={!canGoForward}
              aria-label="Forward"
            >
              <ArrowRight size={16} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={refresh}
              aria-label="Refresh"
            >
              <RotateCw
                size={16}
                className={loading ? "animate-spin" : ""}
              />
            </Button>
          </ToolbarGroup>

          <div className="flex-1 min-w-0 flex items-center gap-2 px-3 py-1 rounded-lg bg-shell-bg-deep border border-white/10">
            <Globe size={14} className="text-shell-text-tertiary shrink-0" />
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              className="flex-1 bg-transparent text-sm text-shell-text outline-none placeholder:text-shell-text-tertiary min-w-0"
              placeholder="Enter URL"
              aria-label="URL"
            />
          </div>

          <ToolbarGroup>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={openInTab}
              aria-label="Open in new tab"
              title="Open in new tab"
            >
              <ExternalLink size={12} />
              <span>Open in Tab</span>
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={toggleMode}
              aria-label={
                mode === "embedded"
                  ? "Switch to external mode"
                  : "Switch to embedded mode"
              }
              title={mode === "embedded" ? "Embedded mode" : "External mode"}
            >
              <Globe size={12} />
              <span>{mode === "embedded" ? "Embed" : "Ext"}</span>
            </Button>
            <div className="relative">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={copyUrl}
                aria-label="Copy URL"
                title="Copy URL"
              >
                <Copy size={14} />
              </Button>
              {copied && (
                <span className="absolute -bottom-6 right-0 text-[10px] bg-shell-surface border border-shell-border rounded px-1 py-0.5 text-shell-text-secondary whitespace-nowrap z-10">
                  Copied!
                </span>
              )}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setAgentDialogOpen(true)}
              aria-label="Agent Browse"
              title="Ask an agent to browse"
            >
              <Bot size={12} />
              <span>Agent</span>
            </Button>
          </ToolbarGroup>
        </Toolbar>
      </form>

      {/* Bookmarks bar */}
      <div className="flex items-center gap-1 px-2 py-1 bg-shell-surface/50 border-b border-shell-border">
        <Bookmark
          size={12}
          className="text-shell-text-tertiary shrink-0 mr-1"
        />
        {BOOKMARKS.map((bm) => (
          <button
            key={bm.url}
            type="button"
            onClick={() => navigate(bm.url)}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-xs text-shell-text-secondary hover:bg-shell-surface-hover hover:text-shell-text transition-colors"
            aria-label={`Go to ${bm.name}`}
          >
            <Star size={10} className="text-shell-text-tertiary" />
            <span>{bm.name}</span>
          </button>
        ))}
      </div>

      {/* Browser content */}
      {mode === "external" ? (
        /* External mode — show prompt to open in tab */
        <div className="flex-1 flex items-center justify-center p-8">
          <Card className="max-w-md w-full">
            <CardContent className="flex flex-col items-center gap-4 pt-6 text-center">
              <Globe size={48} className="text-shell-text-tertiary" />
              <div>
                <h3 className="text-lg font-medium text-shell-text mb-1">
                  External Browser Mode
                </h3>
                <p className="text-sm text-shell-text-secondary">
                  Pages open in a new browser tab for full compatibility. This
                  is the default on iOS and recommended for sites that don't
                  render well embedded.
                </p>
              </div>
              <Button type="button" onClick={openInTab}>
                <ExternalLink size={16} />
                Open {new URL(url).hostname} in Tab
              </Button>
              <Button
                type="button"
                variant="link"
                size="sm"
                onClick={toggleMode}
              >
                Switch to embedded mode
              </Button>
            </CardContent>
          </Card>
        </div>
      ) : loadError ? (
        /* Embedded mode — load error fallback */
        <div className="flex-1 flex items-center justify-center p-8">
          <Card className="max-w-md w-full">
            <CardContent className="flex flex-col items-center gap-4 pt-6 text-center">
              <AlertTriangle size={48} className="text-amber-500" />
              <div>
                <h3 className="text-lg font-medium text-shell-text mb-1">
                  Could not load this page
                </h3>
                <p className="text-sm text-shell-text-secondary">
                  This site could not be loaded through the proxy. Open it
                  directly in a new tab instead.
                </p>
              </div>
              <Button type="button" onClick={openInTab}>
                <ExternalLink size={16} />
                Open in Tab
              </Button>
              <Button
                type="button"
                variant="link"
                size="sm"
                onClick={() => {
                  setLoadError(false);
                  setLoading(true);
                }}
              >
                Try again in embedded mode
              </Button>
            </CardContent>
          </Card>
        </div>
      ) : (
        /* Embedded mode — proxy iframe */
        <iframe
          ref={iframeRef}
          src={proxyUrl(url)}
          className="flex-1 w-full border-none bg-white"
          sandbox="allow-downloads allow-forms allow-modals allow-pointer-lock allow-popups allow-presentation allow-same-origin allow-scripts"
          title="Browser"
          onLoad={handleIframeLoad}
          onError={handleIframeError}
        />
      )}

      {agentDialogOpen && (
        <div
          className="fixed inset-0 z-[10002] flex items-center justify-center bg-black/50 backdrop-blur-sm"
          onClick={() => setAgentDialogOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Agent Browse command"
        >
          <Card
            className="w-[90vw] max-w-md p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <CardContent className="p-0 space-y-3">
              <div>
                <Label>Ask an agent to help on this page</Label>
                <p className="text-xs text-shell-text-tertiary mt-1">
                  Example: "Find the pricing", "Fill out the contact form", "Extract all product names"
                </p>
              </div>
              <Textarea
                value={agentCommand}
                onChange={(e) => setAgentCommand(e.target.value)}
                placeholder="What do you want the agent to do?"
                rows={3}
              />
              {agentResult && (
                <div className="p-2 rounded bg-white/5 text-xs text-shell-text-secondary border border-white/10">
                  {agentResult}
                </div>
              )}
              <div className="flex gap-2 justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setAgentDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={runAgentCommand}
                  disabled={agentLoading || !agentCommand.trim()}
                >
                  {agentLoading ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Bot size={14} />
                  )}
                  Run
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

export default BrowserApp;
