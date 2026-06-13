import { useState, useEffect, useCallback, useRef } from "react";
import { Github, Copy, Check, ExternalLink, Trash2, Plus, Loader2 } from "lucide-react";
import { Button, Card, CardContent } from "@/components/ui";
import {
  startDeviceFlow,
  pollDeviceFlow,
  listIdentities,
  deleteIdentity,
  type GitHubIdentity,
} from "@/lib/github";

type FlowState =
  | { phase: "idle" }
  | { phase: "starting" }
  | {
      phase: "awaiting";
      userCode: string;
      verificationUri: string;
      deviceCode: string;
    }
  | { phase: "error"; message: string };

/* ------------------------------------------------------------------ */
/*  GitHubConnect                                                      */
/* ------------------------------------------------------------------ */

export function GitHubConnect() {
  const [identities, setIdentities] = useState<GitHubIdentity[]>([]);
  const [flow, setFlow] = useState<FlowState>({ phase: "idle" });
  const [copied, setCopied] = useState(false);

  // Refs so the polling loop can be cancelled cleanly on unmount / restart.
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshIdentities = useCallback(async () => {
    setIdentities(await listIdentities());
  }, []);

  useEffect(() => {
    refreshIdentities();
  }, [refreshIdentities]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    if (expiryTimer.current) clearTimeout(expiryTimer.current);
    pollTimer.current = null;
    expiryTimer.current = null;
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const beginPolling = useCallback(
    (deviceCode: string, intervalSec: number, expiresInSec: number) => {
      const intervalMs = Math.max(intervalSec, 1) * 1000;

      const tick = async () => {
        const result = await pollDeviceFlow(deviceCode);
        if (result.status === "connected") {
          stopPolling();
          setFlow({ phase: "idle" });
          await refreshIdentities();
          return;
        }
        if (result.status === "error") {
          stopPolling();
          setFlow({
            phase: "error",
            message:
              result.error === "expired_token"
                ? "The code expired. Please try again."
                : result.error === "access_denied"
                  ? "Authorization was denied."
                  : "Could not connect. Please try again.",
          });
          return;
        }
        // pending -> schedule the next poll
        pollTimer.current = setTimeout(tick, intervalMs);
      };

      pollTimer.current = setTimeout(tick, intervalMs);
      expiryTimer.current = setTimeout(() => {
        stopPolling();
        setFlow({ phase: "error", message: "The code expired. Please try again." });
      }, expiresInSec * 1000);
    },
    [refreshIdentities, stopPolling],
  );

  const handleConnect = useCallback(async () => {
    stopPolling();
    setCopied(false);
    setFlow({ phase: "starting" });
    try {
      const start = await startDeviceFlow();
      setFlow({
        phase: "awaiting",
        userCode: start.user_code,
        verificationUri: start.verification_uri,
        deviceCode: start.device_code,
      });
      beginPolling(start.device_code, start.interval, start.expires_in);
    } catch {
      setFlow({ phase: "error", message: "Could not start the connect flow. Please try again." });
    }
  }, [beginPolling, stopPolling]);

  const handleCancel = useCallback(() => {
    stopPolling();
    setFlow({ phase: "idle" });
  }, [stopPolling]);

  const handleCopy = useCallback(async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard may be unavailable; ignore */
    }
  }, []);

  const handleRemove = useCallback(
    async (id: string) => {
      if (await deleteIdentity(id)) await refreshIdentities();
    },
    [refreshIdentities],
  );

  return (
    <Card className="bg-shell-surface border-white/5">
      <CardContent className="p-5 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Github size={18} className="text-shell-text" />
            <h2 className="text-sm font-semibold text-shell-text">GitHub</h2>
            <span className="text-xs text-shell-text-tertiary">
              {identities.length} connected
            </span>
          </div>
          {flow.phase !== "awaiting" && flow.phase !== "starting" && (
            <Button size="sm" onClick={handleConnect} aria-label="Connect GitHub account">
              <Plus size={14} />
              Connect GitHub account
            </Button>
          )}
        </div>

        {/* Flow card */}
        {flow.phase === "starting" && (
          <div className="flex items-center gap-2 text-sm text-shell-text-secondary">
            <Loader2 size={14} className="animate-spin" />
            Starting...
          </div>
        )}

        {flow.phase === "awaiting" && (
          <div className="rounded-lg border border-white/10 bg-shell-bg-deep p-4 space-y-4">
            <p className="text-sm text-shell-text-secondary">
              Enter this code on GitHub to authorize taOS:
            </p>
            <div className="flex items-center gap-3">
              <span className="font-mono text-2xl tracking-widest text-shell-text select-all">
                {flow.userCode}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleCopy(flow.userCode)}
                aria-label="Copy code"
                title="Copy code"
                className="h-8 w-8"
              >
                {copied ? <Check size={15} className="text-emerald-400" /> : <Copy size={15} />}
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <Button asChild size="sm">
                <a href={flow.verificationUri} target="_blank" rel="noopener noreferrer">
                  <ExternalLink size={14} />
                  Open github.com/login/device
                </a>
              </Button>
              <Button variant="secondary" size="sm" onClick={handleCancel}>
                Cancel
              </Button>
            </div>
            <div className="flex items-center gap-2 text-xs text-shell-text-tertiary">
              <Loader2 size={12} className="animate-spin" />
              Waiting for you to authorize on GitHub...
            </div>
          </div>
        )}

        {flow.phase === "error" && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
            {flow.message}
          </div>
        )}

        {/* Connected identities */}
        {identities.length > 0 && (
          <ul className="space-y-2" aria-label="Connected GitHub accounts">
            {identities.map((id) => (
              <li
                key={id.id}
                className="flex items-center justify-between rounded-lg border border-white/5 bg-shell-bg-deep px-3 py-2"
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  {id.avatar_url ? (
                    <img
                      src={id.avatar_url}
                      alt=""
                      className="h-7 w-7 rounded-full shrink-0"
                    />
                  ) : (
                    <div className="h-7 w-7 rounded-full bg-white/10 flex items-center justify-center shrink-0">
                      <Github size={14} className="text-shell-text-tertiary" />
                    </div>
                  )}
                  <span className="text-sm font-medium text-shell-text truncate">
                    {id.login}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleRemove(id.id)}
                  className="h-7 w-7 hover:text-red-400 hover:bg-red-500/15"
                  aria-label={`Remove ${id.login}`}
                  title="Remove"
                >
                  <Trash2 size={14} />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
