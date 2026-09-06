import { useState, useEffect, useCallback, useRef } from "react";
import { Github, Copy, Check, ExternalLink, Trash2, Plus, Loader2, Settings, X, Save } from "lucide-react";
import { Button, Card, CardContent } from "@/components/ui";
import {
  startDeviceFlow,
  pollDeviceFlow,
  listIdentities,
  deleteIdentity,
  listAppInstallations,
  beginAppInstallation,
  deleteAppInstallation,
  type GitHubIdentity,
  type GitHubAppInstallation,
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

  // GitHub App installations state
  const [installations, setInstallations] = useState<GitHubAppInstallation[]>([]);
  const [installsLoading, setInstallsLoading] = useState(false);
  const [installing, setInstalling] = useState(false);

  // Refs so the polling loop can be cancelled cleanly on unmount / restart.
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshIdentities = useCallback(async () => {
    setIdentities(await listIdentities());
  }, []);

  const refreshInstallations = useCallback(async () => {
    setInstallsLoading(true);
    try {
      setInstallations(await listAppInstallations());
    } finally {
      setInstallsLoading(false);
    }
  }, []);

  const handleAppInstall = useCallback(async () => {
    setInstalling(true);
    try {
      const url = await beginAppInstallation();
      if (url) {
        window.open(url, "_blank");
      }
    } finally {
      setInstalling(false);
    }
  }, []);

  const handleAppUninstall = useCallback(
    async (installationId: number) => {
      if (await deleteAppInstallation(installationId)) {
        await refreshInstallations();
      }
    },
    [refreshInstallations],
  );

  // -- Per-agent GitHub repo grants ----------------------------------

  const [repoAgents, setRepoAgents] = useState<Record<string, string>>({});
  const [savingGrants, setSavingGrants] = useState<Set<string>>(new Set());
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});

  const fetchAgentGrants = useCallback(async () => {
    // Fetch existing github-installation secrets for all known repos.
    try {
      const res = await fetch("/api/secrets?category=github-installation", {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) return;
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) return;
      const data = await res.json();
      if (Array.isArray(data)) {
        const grants: Record<string, string> = {};
        for (const s of data) {
          if (s.name && s.agents && s.agents.length > 0) {
            grants[s.name] = s.agents.join(", ");
          }
        }
        setRepoAgents(grants);
      }
    } catch { /* ignore */ }
  }, []);

  const handleSaveGrants = useCallback(
    async (repoFullName: string, installationId: number, permissions: string[]) => {
      setSavingGrants((prev) => new Set(prev).add(repoFullName));
      setSaveErrors((prev) => {
        const next = { ...prev };
        delete next[repoFullName];
        return next;
      });
      try {
        const agentsStr = repoAgents[repoFullName] || "";
        const agents = agentsStr
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean);

        const body = JSON.stringify({
          name: repoFullName,
          category: "github-installation",
          value: JSON.stringify({
            installation_id: installationId,
            repo_full_name: repoFullName,
            permissions,
          }),
          description: `GitHub App installation for ${repoFullName}`,
          agents,
        });

        // Try create first; if exists, update.
        let res = await fetch("/api/secrets", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body,
        });
        if (res.status === 409) {
          // Already exists — update instead
          res = await fetch(`/api/secrets/${encodeURIComponent(repoFullName)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ agents, value: JSON.stringify({
              installation_id: installationId,
              repo_full_name: repoFullName,
              permissions,
            }) }),
          });
        }
      } catch {
        setSaveErrors((prev) => ({
          ...prev,
          [repoFullName]: "Save failed — please try again.",
        }));
      } finally {
        setSavingGrants((prev) => {
          const next = new Set(prev);
          next.delete(repoFullName);
          return next;
        });
      }
    },
    [repoAgents],
  );

  useEffect(() => {
    refreshIdentities();
    refreshInstallations();
    fetchAgentGrants();

    // Refresh when the user returns from the external GitHub App install flow
    const onFocus = () => {
      refreshIdentities();
      refreshInstallations();
      fetchAgentGrants();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshIdentities, refreshInstallations, fetchAgentGrants]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    if (expiryTimer.current) clearTimeout(expiryTimer.current);
    pollTimer.current = null;
    expiryTimer.current = null;
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const beginPolling = useCallback(
    (deviceCode: string, intervalSec: number, expiresInSec: number) => {
      let intervalMs = Math.max(intervalSec, 1) * 1000;

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
        // pending -> back off by 5s on slow_down (RFC 8628 §3.5), then poll again
        if ("slow_down" in result && result.slow_down) {
          intervalMs += 5000;
        }
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

        {/* GitHub App Installations */}
        <div className="rounded-lg border border-white/10 bg-shell-bg-deep p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Settings size={16} className="text-shell-text-secondary" />
              <h3 className="text-sm font-medium text-shell-text">GitHub App</h3>
            </div>
            <Button
              size="sm"
              onClick={handleAppInstall}
              disabled={installing}
              aria-label="Install GitHub App on repos"
            >
              {installing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Plus size={14} />
              )}
              {installing ? "Opening..." : "Install on repos"}
            </Button>
          </div>

          {installsLoading ? (
            <div className="flex items-center gap-2 text-sm text-shell-text-secondary">
              <Loader2 size={14} className="animate-spin" />
              Loading installations...
            </div>
          ) : installations.length > 0 ? (
            <ul className="space-y-3" aria-label="GitHub App installations">
              {installations.map((inst) => (
                <li key={inst.id} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {inst.account.avatar_url ? (
                        <img
                          src={inst.account.avatar_url}
                          alt=""
                          className="h-5 w-5 rounded-full shrink-0"
                        />
                      ) : (
                        <div className="h-5 w-5 rounded-full bg-white/10 shrink-0" />
                      )}
                      <span className="text-sm font-medium text-shell-text">
                        {inst.account.login}
                      </span>
                      <span className="text-xs text-shell-text-tertiary capitalize">
                        ({inst.account.type})
                      </span>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleAppUninstall(inst.id)}
                      className="h-7 w-7 hover:text-red-400 hover:bg-red-500/15"
                      aria-label={`Uninstall ${inst.account.login}`}
                      title="Uninstall"
                    >
                      <X size={14} />
                    </Button>
                  </div>
                  {inst.repositories.length > 0 && (
                    <div className="ml-7 space-y-3">
                      {inst.repositories.map((repo) => (
                        <div key={repo.full_name} className="space-y-1.5">
                          <div className="flex items-center gap-1.5 text-xs text-shell-text-secondary">
                            <span className="text-shell-text-tertiary">
                              {repo.private ? "🔒" : "📁"}
                            </span>
                            <span className="font-mono">{repo.full_name}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <input
                              type="text"
                              placeholder="agent names, comma-separated"
                              className="flex-1 h-8 rounded-lg border border-white/10 bg-shell-bg-deep px-2.5 text-xs text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/20"
                              value={repoAgents[repo.full_name] || ""}
                              onChange={(e) =>
                                setRepoAgents((prev) => ({
                                  ...prev,
                                  [repo.full_name]: e.target.value,
                                }))
                              }
                              aria-label={`Agents for ${repo.full_name}`}
                            />
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 shrink-0"
                              onClick={() =>
                                handleSaveGrants(repo.full_name, inst.id, inst.permissions ?? [])
                              }
                              aria-label={`Save agent grants for ${repo.full_name}`}
                              title={`Save agent grants for ${repo.full_name}`}
                              disabled={savingGrants.has(repo.full_name)}
                            >
                              {savingGrants.has(repo.full_name) ? (
                                <Loader2 size={14} className="animate-spin" />
                              ) : (
                                <Save size={14} />
                              )}
                            </Button>
                          </div>
                          {saveErrors[repo.full_name] && (
                            <p className="text-xs text-red-400" role="alert">
                              {saveErrors[repo.full_name]}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-shell-text-tertiary">
              No GitHub App installations yet. Click "Install on repos" to give
              taOS access to your repositories.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
