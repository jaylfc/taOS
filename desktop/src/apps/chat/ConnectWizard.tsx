import { useState, useEffect, useRef } from "react";
import { Copy, Check, ChevronRight, ChevronLeft, Play, X, ExternalLink } from "lucide-react";
import { Button, Input, Label } from "@/components/ui";

const BASH_SNIPPET = `#!/usr/bin/env bash
# taOStalk connect snippet — emits session events to the taOS A2A bus.
# Usage: TAOS_URL=http://controller:6969 AGENT_SLUG=my-agent bash connect-taostalk.sh
set -euo pipefail

TAOS_URL="${TAOS_URL:-http://localhost:6969}"
AGENT_SLUG="${AGENT_SLUG:-}"
THREAD="taostalk:${AGENT_SLUG}"

if [[ -z "$AGENT_SLUG" ]]; then
  echo "error: AGENT_SLUG is required" >&2
  exit 1
fi

send() {
  local type="$1"
  local data="$2"
  curl -sS -X POST "${TAOS_URL}/api/a2a/bus/send" \\
    -H "Content-Type: application/json" \\
    -d "{\\"thread\\":\\"${THREAD}\\",\\"body\\":$(printf '%s' "$data" | jq -c .)}"
}

turn_start='{"v":1,"type":"turn_start","session_id":"sess-${AGENT_SLUG}","turn_id":"turn-1","seq":0,"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","data":{}}'
send turn_start "$turn_start"

thinking='{"v":1,"type":"thinking","session_id":"sess-${AGENT_SLUG}","turn_id":"turn-1","seq":1,"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","data":{"text":"Planning..."}}'
send thinking "$thinking"

tool_call='{"v":1,"type":"tool_call","session_id":"sess-${AGENT_SLUG}","turn_id":"turn-1","seq":2,"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","data":{"call_id":"call-1","name":"bash","input_preview":"echo hello"}}'
send tool_call "$tool_call"

tool_result='{"v":1,"type":"tool_result","session_id":"sess-${AGENT_SLUG}","turn_id":"turn-1","seq":3,"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","data":{"call_id":"call-1","status":"done","result_preview":"hello"}}'
send tool_result "$tool_result"

text_delta='{"v":1,"type":"text_delta","session_id":"sess-${AGENT_SLUG}","turn_id":"turn-1","seq":4,"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","data":{"text":"Hello from the agent!"}}'
send text_delta "$text_delta"

turn_end='{"v":1,"type":"turn_end","session_id":"sess-${AGENT_SLUG}","turn_id":"turn-1","seq":5,"ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","data":{}}'
send turn_end "$turn_end"

echo "done — check taOS Messages > #session-${AGENT_SLUG}"`;

const PS1_SNIPPET = `# taOStalk connect snippet — PowerShell twin
# Usage: $env:TAOS_URL = 'http://controller:6969'; $env:AGENT_SLUG = 'my-agent'; .\\connect-taostalk.ps1
param(
  [string]$TaosUrl = $env:TAOS_URL,
  [string]$AgentSlug = $env:AGENT_SLUG
)

if (-not $TaosUrl) { Write-Error "TAOS_URL is required"; exit 1 }
if (-not $AgentSlug) { Write-Error "AGENT_SLUG is required"; exit 1 }

$thread = "taostalk:$AgentSlug"
$headers = @{ "Content-Type" = "application/json" }

function Send([string]$type, [string]$body) {
  $payload = @{ thread = $thread; body = $body } | ConvertTo-Json -Compress
  Invoke-RestMethod -Method POST -Uri "$TaosUrl/api/a2a/bus/send" -Headers $headers -Body $payload
}

$ts = (Get-Date).ToUniversalTime().ToString("o")
$base = @{ v = 1; session_id = "sess-$AgentSlug"; turn_id = "turn-1" }

Send turn_start (@$base + @{ type = "turn_start"; seq = 0; ts = $ts; data = @{} } | ConvertTo-Json -Compress)
Send thinking (@$base + @{ type = "thinking"; seq = 1; ts = $ts; data = @{ text = "Planning..." } } | ConvertTo-Json -Compress)
Send tool_call (@$base + @{ type = "tool_call"; seq = 2; ts = $ts; data = @{ call_id = "call-1"; name = "bash"; input_preview = "echo hello" } } | ConvertTo-Json -Compress)
Send tool_result (@$base + @{ type = "tool_result"; seq = 3; ts = $ts; data = @{ call_id = "call-1"; status = "done"; result_preview = "hello" } } | ConvertTo-Json -Compress)
Send text_delta (@$base + @{ type = "text_delta"; seq = 4; ts = $ts; data = @{ text = "Hello from the agent!" } } | ConvertTo-Json -Compress)
Send turn_end (@$base + @{ type = "turn_end"; seq = 5; ts = $ts; data = @{} } | ConvertTo-Json -Compress)

Write-Host "done — check taOS Messages > #session-$AgentSlug"`;

export function ConnectWizard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [agents, setAgents] = useState<{ name: string; display_name?: string }[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [channelName, setChannelName] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(false);
  const [copied, setCopied] = useState<"bash" | "ps1" | null>(null);
  const [snippet, setSnippet] = useState<"bash" | "ps1">("bash");
  const [testing, setTesting] = useState(false);
  const [testOk, setTestOk] = useState(false);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setSelectedAgent("");
    setChannelName("");
    setCreated(false);
    setTestOk(false);
    setTesting(false);
    setCopied(null);
    setAgentsLoaded(false);
    (async () => {
      try {
        const res = await fetch("/api/agents", { headers: { Accept: "application/json" } });
        const ct = res.headers.get("content-type") ?? "";
        if (res.ok && ct.includes("application/json")) {
          const data = await res.json();
          const list = Array.isArray(data) ? data : [];
          setAgents(
            list
              .filter((a: Record<string, unknown>) => a?.status === "running")
              .map((a: Record<string, unknown>) => ({
                name: String(a.name ?? a.id ?? ""),
                display_name: String(a.display_name ?? a.name ?? a.id ?? ""),
              })),
          );
        }
      } catch {
        /* leave empty */
      } finally {
        setAgentsLoaded(true);
      }
    })();
  }, [open]);

  const canNext = () => {
    if (step === 0) return selectedAgent.length > 0;
    if (step === 1) return channelName.trim().length > 0;
    return true;
  };

  async function handleCreateChannel() {
    setCreating(true);
    try {
      const res = await fetch("/api/chat/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: channelName.trim(),
          type: "topic",
          description: `taOStalk session for ${selectedAgent}`,
          settings: { taostalk_agent: selectedAgent },
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err?.error ?? `Failed to create channel (${res.status})`);
        setCreating(false);
        return;
      }
      setCreated(true);
      setStep(2);
    } catch {
      alert("Network error creating channel");
    } finally {
      setCreating(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setTestOk(false);
    try {
      const res = await fetch(`/api/a2a/bus/messages?channel=${encodeURIComponent(channelName)}&limit=1`);
      if (res.ok) {
        setTestOk(true);
      }
    } catch {
      /* ignore */
    } finally {
      setTesting(false);
    }
  }

  function copySnippet(type: "bash" | "ps1") {
    const text = type === "bash" ? BASH_SNIPPET : PS1_SNIPPET;
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(type);
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(null), 2000);
  }

  if (!open) return null;

  const STEPS = ["Pick agent", "Create surface", "Snippet"];
  const totalSteps = STEPS.length;

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      style={{
        paddingTop: "calc(1rem + env(safe-area-inset-top, 0px))",
        paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))",
      }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Connect session"
    >
      <div
        className="w-full max-w-lg max-h-full min-h-0 bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-2">
            <Play size={16} className="text-accent" />
            <h2 className="text-sm font-semibold">Connect session</h2>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onClose}
            aria-label="Close wizard"
          >
            <X size={16} />
          </Button>
        </div>

        {/* Step indicators */}
        <div className="flex items-center gap-1 px-5 py-3 border-b border-white/5 shrink-0 overflow-x-auto">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-1">
              <div
                aria-label={label}
                aria-current={i === step ? "step" : undefined}
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-medium transition-colors ${
                  i < step
                    ? "bg-accent/20 text-accent"
                    : i === step
                      ? "bg-accent text-white"
                      : "bg-white/5 text-shell-text-tertiary"
                }`}
              >
                {i < step ? <Check size={12} /> : i + 1}
              </div>
              {i === step && (
                <span className="text-[11px] font-medium text-shell-text whitespace-nowrap ml-0.5">
                  {label}
                </span>
              )}
              {i < STEPS.length - 1 && (
                <div className={`w-4 h-px mx-1 ${i < step ? "bg-accent/30" : "bg-white/10"}`} />
              )}
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex-1 min-h-0 overflow-y-auto">
          {step === 0 && (
            <div className="space-y-3">
              <Label className="block text-xs text-shell-text-secondary">Select agent</Label>
              {!agentsLoaded && (
                <p className="text-xs text-shell-text-tertiary">Loading agents...</p>
              )}
              {agentsLoaded && agents.length === 0 && (
                <p className="text-xs text-shell-text-tertiary">No running agents with a2a_send found.</p>
              )}
              <div className="space-y-1.5" role="listbox" aria-label="Agents">
                {agents.map((a) => (
                  <button
                    key={a.name}
                    role="option"
                    aria-selected={selectedAgent === a.name}
                    onClick={() => setSelectedAgent(a.name)}
                    className={`w-full text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                      selectedAgent === a.name
                        ? "border-accent bg-accent/10 text-shell-text"
                        : "border-white/10 bg-shell-bg-deep text-shell-text-secondary hover:bg-white/5"
                    }`}
                  >
                    {a.display_name || a.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-3">
              <div>
                <Label htmlFor="session-channel" className="block text-xs text-shell-text-secondary mb-1.5">
                  Channel name
                </Label>
                <Input
                  id="session-channel"
                  value={channelName}
                  onChange={(e) => setChannelName(e.target.value)}
                  placeholder={`#session-${selectedAgent}`}
                  autoFocus
                />
                <p className="mt-1 text-[11px] text-shell-text-tertiary">
                  Creates a dedicated session channel. The agent connects via the snippet below.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={handleCreateChannel}
                  disabled={creating}
                  className="flex-1"
                >
                  {creating ? "Creating..." : "Create channel"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleTestConnection}
                  disabled={testing || !created}
                >
                  {testing ? "Testing..." : testOk ? "Connected" : "Test connection"}
                </Button>
              </div>
              {testOk && (
                <p className="text-xs text-emerald-400">Connection test passed.</p>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="space-y-3">
              <Label className="block text-xs text-shell-text-secondary">Connect snippet</Label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSnippet("bash")}
                  className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                    snippet === "bash"
                      ? "border-accent bg-accent/10 text-shell-text"
                      : "border-white/10 bg-shell-bg-deep text-shell-text-secondary"
                  }`}
                >
                  bash
                </button>
                <button
                  type="button"
                  onClick={() => setSnippet("ps1")}
                  className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                    snippet === "ps1"
                      ? "border-accent bg-accent/10 text-shell-text"
                      : "border-white/10 bg-shell-bg-deep text-shell-text-secondary"
                  }`}
                >
                  PowerShell
                </button>
              </div>
              <div className="relative">
                <pre className="text-[12px] font-mono text-shell-text-secondary bg-black/40 border border-white/10 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">
                  {snippet === "bash" ? BASH_SNIPPET : PS1_SNIPPET}
                </pre>
                <button
                  type="button"
                  onClick={() => copySnippet(snippet)}
                  className="absolute top-2 right-2 p-1.5 rounded bg-shell-surface border border-white/10 text-shell-text-secondary hover:text-shell-text transition-colors"
                  aria-label={copied === snippet ? "Copied" : "Copy snippet"}
                >
                  {copied === snippet ? <Check size={12} /> : <Copy size={12} />}
                </button>
              </div>
              <p className="text-[11px] text-shell-text-tertiary">
                Paste this into your agent harness. Replace <code>TAOS_URL</code> and <code>AGENT_SLUG</code>.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-white/5 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => (step === 0 ? onClose() : setStep(step - 1))}
            disabled={step === 2 && !created}
          >
            <ChevronLeft size={14} />
            {step === 0 ? "Cancel" : "Back"}
          </Button>
          {step < totalSteps - 1 ? (
            <Button size="sm" onClick={() => setStep(step + 1)} disabled={!canNext()}>
              Next
              <ChevronRight size={14} />
            </Button>
          ) : (
            <Button size="sm" onClick={onClose} className="bg-emerald-600 hover:bg-emerald-500 text-white">
              Done
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
