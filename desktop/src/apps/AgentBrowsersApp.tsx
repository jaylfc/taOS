import { useState, useEffect, useCallback } from "react";
import {
  Globe,
  Plus,
  Trash2,
  Play,
  Square,
  Monitor,
  RefreshCw,
  ChevronLeft,
} from "lucide-react";
import { Button, Card, CardContent, Input } from "@/components/ui";
import {
  listProfiles,
  createProfile,
  deleteProfile,
  deleteProfileData,
  startBrowser,
  stopBrowser,
  getScreenshot,
  getLoginStatus,
  assignAgent,
  moveToNode,
} from "@/lib/agent-browsers";
import type { BrowserProfile, LoginStatus } from "@/lib/agent-browsers";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AgentInfo {
  name: string;
  color: string;
}

type PanelView = "detail" | "create";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const LOGIN_SITES = [
  { key: "x" as const, label: "X / Twitter" },
  { key: "github" as const, label: "GitHub" },
  { key: "youtube" as const, label: "YouTube" },
  { key: "reddit" as const, label: "Reddit" },
];

function StatusBadge({ status }: { status: BrowserProfile["status"] }) {
  const cls =
    status === "running"
      ? "bg-green-500/15 text-green-400 border border-green-500/30"
      : status === "error"
        ? "bg-red-500/15 text-red-400 border border-red-500/30"
        : "bg-white/10 text-shell-text-tertiary border border-white/10";
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${cls}`}>
      {status}
    </span>
  );
}

function NodeBadge({ node }: { node: string }) {
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-accent/10 text-accent border border-accent/20">
      {node}
    </span>
  );
}

function LoginDots({ status }: { status: LoginStatus | null }) {
  return (
    <div className="flex gap-1" aria-label="Login status indicators">
      {LOGIN_SITES.map(({ key, label }) => (
        <span
          key={key}
          title={label}
          aria-label={`${label}: ${status ? (status[key] ? "logged in" : "not logged in") : "unknown"}`}
          className={`w-2 h-2 rounded-full ${
            status
              ? status[key]
                ? "bg-green-400"
                : "bg-white/20"
              : "bg-white/10"
          }`}
        />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Profile Card                                                       */
/* ------------------------------------------------------------------ */

interface ProfileCardProps {
  profile: BrowserProfile;
  loginStatus: LoginStatus | null;
  selected: boolean;
  onSelect: () => void;
  onToggle: (e: React.MouseEvent) => void;
  toggling: boolean;
}

function ProfileCard({ profile, loginStatus, selected, onSelect, onToggle, toggling }: ProfileCardProps) {
  return (
    <Card
      role="button"
      tabIndex={0}
      aria-selected={selected}
      aria-label={`Browser profile: ${profile.profile_name}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`cursor-pointer transition-colors select-none ${
        selected
          ? "border-accent/50 bg-accent/5"
          : "border-white/5 hover:border-white/15 hover:bg-white/3"
      }`}
    >
      <CardContent className="p-3 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-semibold truncate">{profile.profile_name}</p>
            {profile.agent_name && (
              <p className="text-xs text-shell-text-tertiary truncate">{profile.agent_name}</p>
            )}
          </div>
          <StatusBadge status={profile.status} />
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <NodeBadge node={profile.node} />
            <LoginDots status={loginStatus} />
          </div>
          <Button
            variant="ghost"
            size="sm"
            aria-label={profile.status === "running" ? "Stop browser" : "Start browser"}
            disabled={toggling}
            onClick={onToggle}
            className="h-6 w-6 p-0 shrink-0"
          >
            {profile.status === "running" ? (
              <Square size={12} className="text-red-400" />
            ) : (
              <Play size={12} className="text-green-400" />
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  New Profile Card                                                   */
/* ------------------------------------------------------------------ */

function NewProfileCard({ onSelect, selected }: { onSelect: () => void; selected: boolean }) {
  return (
    <Card
      role="button"
      tabIndex={0}
      aria-label="Create new browser profile"
      aria-selected={selected}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`cursor-pointer transition-colors border-dashed ${
        selected
          ? "border-accent/50 bg-accent/5"
          : "border-white/10 hover:border-accent/30 hover:bg-white/3"
      }`}
    >
      <CardContent className="p-3 flex items-center gap-2 text-shell-text-tertiary">
        <Plus size={14} />
        <span className="text-sm">New Profile</span>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  AgentBrowsersApp                                                   */
/* ------------------------------------------------------------------ */

export function AgentBrowsersApp({ windowId: _windowId }: { windowId: string }) {
  /* ---------- view state ---------- */
  const [selectedProfile, setSelectedProfile] = useState<BrowserProfile | null>(null);
  const [panelView, setPanelView] = useState<PanelView | null>(null);

  /* ---------- data ---------- */
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [loginStatuses, setLoginStatuses] = useState<Record<string, LoginStatus>>({});
  const [screenshots, setScreenshots] = useState<Record<string, string>>({});
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  /* ---------- loading ---------- */
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);
  const [screenshotLoading, setScreenshotLoading] = useState(false);

  /* ---------- create form ---------- */
  const [newName, setNewName] = useState("");
  const [newAgentName, setNewAgentName] = useState("");
  const [creating, setCreating] = useState(false);

  /* ---------- detail actions ---------- */
  const [confirmDeleteData, setConfirmDeleteData] = useState(false);
  const [assignValue, setAssignValue] = useState("");
  const [nodeValue, setNodeValue] = useState("local");

  /* ---------- mobile ---------- */
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
  const [mobileShowDetail, setMobileShowDetail] = useState(false);

  /* ---------------------------------------------------------------- */
  /*  Data fetching                                                    */
  /* ---------------------------------------------------------------- */

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    const data = await listProfiles();
    setProfiles(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchProfiles();
  }, [fetchProfiles]);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await fetch("/api/agents", { headers: { Accept: "application/json" } });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setAgents(
              data.map((a: Record<string, unknown>) => ({
                name: String(a.name ?? "unknown"),
                color: String(a.color ?? "#3b82f6"),
              })),
            );
          }
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const fetchLoginStatus = useCallback(async (id: string) => {
    const status = await getLoginStatus(id);
    if (status) {
      setLoginStatuses((prev) => ({ ...prev, [id]: status }));
    }
  }, []);

  useEffect(() => {
    for (const p of profiles) {
      fetchLoginStatus(p.id);
    }
  }, [profiles, fetchLoginStatus]);

  const fetchScreenshot = useCallback(async (id: string) => {
    setScreenshotLoading(true);
    const data = await getScreenshot(id);
    if (data) {
      setScreenshots((prev) => ({ ...prev, [id]: data }));
    }
    setScreenshotLoading(false);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Selection                                                        */
  /* ---------------------------------------------------------------- */

  const selectProfile = useCallback(
    (profile: BrowserProfile) => {
      setSelectedProfile(profile);
      setPanelView("detail");
      setConfirmDeleteData(false);
      setAssignValue(profile.agent_name ?? "");
      setNodeValue(profile.node);
      if (profile.status === "running") {
        fetchScreenshot(profile.id);
      }
      if (isMobile) setMobileShowDetail(true);
    },
    [fetchScreenshot, isMobile],
  );

  const openCreate = useCallback(() => {
    setSelectedProfile(null);
    setPanelView("create");
    setNewName("");
    setNewAgentName("");
    if (isMobile) setMobileShowDetail(true);
  }, [isMobile]);

  const goBack = useCallback(() => {
    setMobileShowDetail(false);
    setPanelView(null);
    setSelectedProfile(null);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Actions                                                          */
  /* ---------------------------------------------------------------- */

  const handleToggle = useCallback(
    async (profile: BrowserProfile, e?: React.MouseEvent) => {
      e?.stopPropagation();
      setToggling(profile.id);
      let updated: BrowserProfile | null = null;
      if (profile.status === "running") {
        updated = await stopBrowser(profile.id);
      } else {
        updated = await startBrowser(profile.id);
      }
      if (updated) {
        setProfiles((prev) => prev.map((p) => (p.id === updated!.id ? updated! : p)));
        if (selectedProfile?.id === updated.id) {
          setSelectedProfile(updated);
          if (updated.status === "running") {
            fetchScreenshot(updated.id);
          }
        }
      }
      setToggling(null);
    },
    [selectedProfile, fetchScreenshot],
  );

  const handleCreate = useCallback(async () => {
    if (!newName.trim()) return;
    setCreating(true);
    const result = await createProfile(
      newName.trim(),
      newAgentName || undefined,
      "local",
    );
    if (result) {
      await fetchProfiles();
      setPanelView(null);
      setSelectedProfile(null);
      if (isMobile) setMobileShowDetail(false);
    }
    setCreating(false);
  }, [newName, newAgentName, fetchProfiles, isMobile]);

  const handleDeleteProfile = useCallback(async () => {
    if (!selectedProfile) return;
    const ok = await deleteProfile(selectedProfile.id);
    if (ok) {
      setProfiles((prev) => prev.filter((p) => p.id !== selectedProfile.id));
      setSelectedProfile(null);
      setPanelView(null);
      if (isMobile) setMobileShowDetail(false);
    }
  }, [selectedProfile, isMobile]);

  const handleDeleteData = useCallback(async () => {
    if (!selectedProfile) return;
    const ok = await deleteProfileData(selectedProfile.id);
    if (ok) {
      setConfirmDeleteData(false);
    }
  }, [selectedProfile]);

  const handleAssignAgent = useCallback(async () => {
    if (!selectedProfile || !assignValue) return;
    const updated = await assignAgent(selectedProfile.id, assignValue);
    if (updated) {
      setProfiles((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setSelectedProfile(updated);
    }
  }, [selectedProfile, assignValue]);

  const handleMoveNode = useCallback(async () => {
    if (!selectedProfile) return;
    const updated = await moveToNode(selectedProfile.id, nodeValue);
    if (updated) {
      setProfiles((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setSelectedProfile(updated);
    }
  }, [selectedProfile, nodeValue]);

  /* ---------------------------------------------------------------- */
  /*  Panel: Create Profile                                            */
  /* ---------------------------------------------------------------- */

  const createPanel = (
    <div className="flex flex-col h-full" aria-label="Create new browser profile">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        {isMobile && (
          <Button variant="ghost" size="sm" aria-label="Back" onClick={goBack} className="h-7 w-7 p-0 mr-1">
            <ChevronLeft size={14} />
          </Button>
        )}
        <Plus size={14} className="text-accent" />
        <h2 className="text-sm font-semibold">New Profile</h2>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <div className="space-y-1.5">
          <label htmlFor="new-profile-name" className="text-xs text-shell-text-tertiary">
            Profile name
          </label>
          <Input
            id="new-profile-name"
            placeholder="e.g. research-main"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            aria-required="true"
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="new-profile-agent" className="text-xs text-shell-text-tertiary">
            Assign agent (optional)
          </label>
          <select
            id="new-profile-agent"
            value={newAgentName}
            onChange={(e) => setNewAgentName(e.target.value)}
            className="w-full h-9 rounded-md border border-white/10 bg-shell-surface/50 px-3 text-sm text-shell-text focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="">Unassigned</option>
            {agents.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
        <Button
          onClick={handleCreate}
          disabled={!newName.trim() || creating}
          className="w-full"
          aria-busy={creating}
        >
          {creating ? "Creating…" : "Create Profile"}
        </Button>
      </div>
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Panel: Detail                                                    */
  /* ---------------------------------------------------------------- */

  const detailPanel = selectedProfile ? (
    <div className="flex flex-col h-full" aria-label={`Browser profile details: ${selectedProfile.profile_name}`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        {isMobile && (
          <Button variant="ghost" size="sm" aria-label="Back" onClick={goBack} className="h-7 w-7 p-0 mr-1">
            <ChevronLeft size={14} />
          </Button>
        )}
        <Globe size={14} className="text-accent shrink-0" />
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold truncate">{selectedProfile.profile_name}</h2>
          {selectedProfile.agent_name && (
            <p className="text-xs text-shell-text-tertiary truncate">{selectedProfile.agent_name}</p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <NodeBadge node={selectedProfile.node} />
          <StatusBadge status={selectedProfile.status} />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Screenshot preview */}
        <section aria-labelledby="screenshot-heading">
          <h3 id="screenshot-heading" className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-2">
            Preview
          </h3>
          <div className="relative w-full aspect-video bg-shell-surface/50 border border-white/5 rounded-md overflow-hidden flex items-center justify-center">
            {screenshotLoading ? (
              <div className="flex items-center gap-2 text-shell-text-tertiary text-xs">
                <RefreshCw size={12} className="animate-spin" />
                <span>Loading preview…</span>
              </div>
            ) : screenshots[selectedProfile.id] ? (
              <img
                src={screenshots[selectedProfile.id]}
                alt={`Screenshot of ${selectedProfile.profile_name}`}
                className="w-full h-full object-contain"
              />
            ) : (
              <p className="text-xs text-shell-text-tertiary text-center px-4">
                {selectedProfile.status === "running"
                  ? "No screenshot available"
                  : "Start browser to see preview"}
              </p>
            )}
          </div>
        </section>

        {/* Login status */}
        <section aria-labelledby="login-status-heading">
          <h3 id="login-status-heading" className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-2">
            Login Status
          </h3>
          <div className="space-y-1">
            {LOGIN_SITES.map(({ key, label }) => {
              const status = loginStatuses[selectedProfile.id];
              const loggedIn = status ? status[key] : null;
              return (
                <div key={key} className="flex items-center gap-2 text-sm">
                  <span
                    className={`w-2 h-2 rounded-full shrink-0 ${
                      loggedIn === true
                        ? "bg-green-400"
                        : loggedIn === false
                          ? "bg-red-400/60"
                          : "bg-white/20"
                    }`}
                    aria-hidden="true"
                  />
                  <span className="text-shell-text-secondary">{label}</span>
                  <span className="ml-auto text-xs text-shell-text-tertiary">
                    {loggedIn === true ? "Logged in" : loggedIn === false ? "Not logged in" : "Unknown"}
                  </span>
                </div>
              );
            })}
          </div>
        </section>

        {/* Actions */}
        <section aria-labelledby="actions-heading">
          <h3 id="actions-heading" className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-2">
            Actions
          </h3>
          <div className="space-y-2">
            {/* Start / Stop */}
            <div className="flex gap-2">
              <Button
                variant={selectedProfile.status === "running" ? "secondary" : "default"}
                size="sm"
                disabled={toggling === selectedProfile.id}
                onClick={() => handleToggle(selectedProfile)}
                aria-busy={toggling === selectedProfile.id}
                className="flex-1 flex items-center gap-1.5"
              >
                {selectedProfile.status === "running" ? (
                  <>
                    <Square size={12} />
                    Stop
                  </>
                ) : (
                  <>
                    <Play size={12} />
                    Start
                  </>
                )}
              </Button>

              {/* Connect (noVNC) */}
              <Button
                variant="secondary"
                size="sm"
                disabled={selectedProfile.status !== "running"}
                title="Opens browser in a taOS window"
                aria-label="Connect to browser via noVNC — opens browser in a taOS window"
                className="flex-1 flex items-center gap-1.5"
                onClick={() => {
                  /* noVNC connect placeholder */
                }}
              >
                <Monitor size={12} />
                Connect
              </Button>
            </div>

            {/* Refresh screenshot */}
            {selectedProfile.status === "running" && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => fetchScreenshot(selectedProfile.id)}
                disabled={screenshotLoading}
                aria-busy={screenshotLoading}
                className="w-full flex items-center gap-1.5 text-xs"
              >
                <RefreshCw size={11} className={screenshotLoading ? "animate-spin" : ""} />
                Refresh screenshot
              </Button>
            )}

            {/* Assign agent */}
            <div className="space-y-1">
              <label htmlFor="assign-agent-select" className="text-xs text-shell-text-tertiary">
                Assign agent
              </label>
              <div className="flex gap-2">
                <select
                  id="assign-agent-select"
                  value={assignValue}
                  onChange={(e) => setAssignValue(e.target.value)}
                  className="flex-1 h-8 rounded-md border border-white/10 bg-shell-surface/50 px-2 text-xs text-shell-text focus:outline-none focus:ring-1 focus:ring-accent"
                >
                  <option value="">Unassigned</option>
                  {agents.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleAssignAgent}
                  disabled={!assignValue}
                  className="shrink-0"
                >
                  Assign
                </Button>
              </div>
            </div>

            {/* Move to node */}
            <div className="space-y-1">
              <label htmlFor="move-node-select" className="text-xs text-shell-text-tertiary">
                Node
              </label>
              <div className="flex gap-2">
                <select
                  id="move-node-select"
                  value={nodeValue}
                  onChange={(e) => setNodeValue(e.target.value)}
                  className="flex-1 h-8 rounded-md border border-white/10 bg-shell-surface/50 px-2 text-xs text-shell-text focus:outline-none focus:ring-1 focus:ring-accent"
                >
                  <option value="local">local</option>
                </select>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleMoveNode}
                  className="shrink-0"
                >
                  Move
                </Button>
              </div>
            </div>
          </div>
        </section>

        {/* Danger zone */}
        <section aria-labelledby="danger-heading">
          <h3 id="danger-heading" className="text-xs font-medium text-red-400/70 uppercase tracking-wider mb-2">
            Danger Zone
          </h3>
          <div className="space-y-2">
            {/* Delete container */}
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDeleteProfile}
              className="w-full flex items-center gap-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/10 border border-red-500/20"
              aria-label="Delete container"
            >
              <Trash2 size={12} />
              Delete container
            </Button>

            {/* Delete data */}
            {!confirmDeleteData ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDeleteData(true)}
                className="w-full flex items-center gap-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/10 border border-red-500/20"
                aria-label="Delete browser data"
              >
                <Trash2 size={12} />
                Delete data
              </Button>
            ) : (
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 space-y-2">
                <p className="text-xs text-red-300">
                  This permanently removes all passwords, bookmarks, cookies, and browsing history.
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmDeleteData(false)}
                    className="flex-1 text-xs"
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleDeleteData}
                    className="flex-1 text-xs bg-red-600 hover:bg-red-700 text-white border-0"
                    aria-label="Confirm delete all browser data"
                  >
                    Delete all data
                  </Button>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  ) : null;

  /* ---------------------------------------------------------------- */
  /*  Card grid                                                        */
  /* ---------------------------------------------------------------- */

  const cardGrid = (
    <div
      className="flex flex-col h-full"
      role="region"
      aria-label="Browser profiles"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <Globe size={15} className="text-accent" />
        <h1 className="text-sm font-semibold">Agent Browsers</h1>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="flex items-center justify-center h-24 text-shell-text-tertiary text-sm">
            <RefreshCw size={14} className="animate-spin mr-2" />
            Loading profiles…
          </div>
        ) : (
          <div
            role="list"
            aria-label="Browser profile cards"
            className="grid grid-cols-1 gap-2"
          >
            {profiles.map((profile) => (
              <div key={profile.id} role="listitem">
                <ProfileCard
                  profile={profile}
                  loginStatus={loginStatuses[profile.id] ?? null}
                  selected={selectedProfile?.id === profile.id}
                  onSelect={() => selectProfile(profile)}
                  onToggle={(e) => handleToggle(profile, e)}
                  toggling={toggling === profile.id}
                />
              </div>
            ))}
            <div role="listitem">
              <NewProfileCard
                onSelect={openCreate}
                selected={panelView === "create"}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Layout                                                           */
  /* ---------------------------------------------------------------- */

  // Mobile: show either grid or detail (never both)
  if (isMobile) {
    return (
      <div className="w-full h-full bg-shell-bg text-shell-text overflow-hidden">
        {mobileShowDetail ? (
          panelView === "create" ? createPanel : detailPanel
        ) : (
          cardGrid
        )}
      </div>
    );
  }

  // Desktop: two-panel layout (grid left, detail right)
  return (
    <div className="w-full h-full bg-shell-bg text-shell-text flex overflow-hidden">
      {/* Left: card grid */}
      <div className="w-72 shrink-0 border-r border-white/5 flex flex-col overflow-hidden">
        {cardGrid}
      </div>

      {/* Right: detail / create panel */}
      <div className="flex-1 min-w-0 overflow-hidden">
        {panelView === "create" ? (
          createPanel
        ) : panelView === "detail" && selectedProfile ? (
          detailPanel
        ) : (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary">
            <div className="text-center space-y-2">
              <Globe size={32} className="mx-auto opacity-20" />
              <p className="text-sm">Select a profile to view details</p>
              <p className="text-xs opacity-60">or create a new one</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
