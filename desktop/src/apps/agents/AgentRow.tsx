import { type ReactNode } from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { ScrollText, Trash2, Server, Wrench, MessageSquare, PauseCircle, RotateCcw, HardDrive, Database, Cpu } from "lucide-react";
import { LatestVersion } from "@/lib/framework-api";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { Button, Card } from "@/components/ui";
import { type Agent, type DiskState } from "./types";

/* ------------------------------------------------------------------ */
/*  AgentRow                                                           */
/* ------------------------------------------------------------------ */

type AgentStatus = Agent["status"];

/** Semantic colour + copy for the status indicator dot. Colour here carries
 *  meaning (running/paused/error), so Tailwind colour utilities are allowed,
 *  kept at tasteful alpha so they read in both the dark and light themes. */
type StatusMeta = { label: string; dot: string; text: string; pulse: boolean };

const STATUS_STOPPED: StatusMeta = { label: "Stopped", dot: "bg-shell-text-tertiary", text: "text-shell-text-tertiary", pulse: false };
const STATUS_META: Record<string, StatusMeta> = {
  running: { label: "Running", dot: "bg-emerald-400", text: "text-emerald-400", pulse: true },
  stopped: STATUS_STOPPED,
  error: { label: "Error", dot: "bg-red-400", text: "text-red-400", pulse: false },
  deploying: { label: "Deploying", dot: "bg-amber-400", text: "text-amber-400", pulse: false },
};

/** A small dot + label conveying live agent state. The running dot breathes
 *  via a halo that is disabled under prefers-reduced-motion (see tokens.css). */
function StatusIndicator({ status, paused }: { status: AgentStatus; paused?: boolean }) {
  // A paused agent reads as paused regardless of its container status.
  const meta: StatusMeta = paused
    ? { label: "Paused", dot: "bg-amber-400", text: "text-amber-400", pulse: false }
    : STATUS_META[status] ?? STATUS_STOPPED;
  return (
    <span className="inline-flex items-center gap-1.5 shrink-0" aria-label={`Status: ${meta.label}`}>
      <span className="relative inline-flex h-2 w-2 shrink-0">
        {meta.pulse && (
          <span
            aria-hidden="true"
            className={`taos-status-pulse absolute inset-0 rounded-full ${meta.dot}`}
          />
        )}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${meta.dot}`} />
      </span>
      <span className={`text-xs font-medium ${meta.text}`}>{meta.label}</span>
    </span>
  );
}

/** The agent-identity tile: a colour-tinted rounded square holding the emoji. */
function IdentityTile({ color, emoji, size = 36 }: { color: string; emoji: string; size?: number }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-lg border border-shell-border shrink-0"
      style={{ width: size, height: size, backgroundColor: `${color}22` }}
      aria-hidden="true"
    >
      <span className="text-lg leading-none">{emoji}</span>
    </span>
  );
}

function PausedChip() {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/25"
      title="This agent is paused due to a worker failure"
    >
      <PauseCircle size={10} aria-hidden="true" />
      paused
    </span>
  );
}

/** A single indicator chip (icon + value) for the indicators line. */
function IndicatorChip({ icon, value, title }: { icon: ReactNode; value: string; title: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 max-w-full px-1.5 py-0.5 rounded-md bg-shell-surface border border-shell-border text-[10px] text-shell-text-tertiary font-mono"
      title={title}
    >
      {icon}
      <span className="truncate">{value}</span>
    </span>
  );
}

/** The indicators line under an agent's identity: model now, room for more
 *  (memory, region, ...) later. Renders nothing when there is nothing to show. */
function IndicatorRow({ agent }: { agent: Agent }) {
  if (!agent.model) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 mt-1">
      <IndicatorChip
        icon={<Cpu size={10} className="shrink-0" aria-hidden="true" />}
        value={agent.model}
        title={`Model: ${agent.model}`}
      />
    </div>
  );
}

function DiskChip({ diskState, verbose }: { diskState: DiskState; verbose?: boolean }) {
  const isHard = diskState.state === "hard";
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${
        isHard
          ? "bg-red-500/15 text-red-400 border-red-500/25"
          : "bg-amber-500/15 text-amber-400 border-amber-500/25"
      }`}
      title={
        isHard
          ? "Full - agent paused until user action"
          : `Used more than ${diskState.percent}% - needs audit or expand`
      }
      aria-label={`Disk usage: ${diskState.used_gib}/${diskState.quota_gib} GiB (${diskState.percent}%)`}
    >
      <HardDrive size={10} aria-hidden="true" />
      {diskState.used_gib}/{diskState.quota_gib} GiB{verbose ? ` (${diskState.percent}%)` : ""}
    </span>
  );
}

export function AgentRow({
  agent,
  diskState,
  latestByFramework,
  onViewLogs,
  onViewSkills,
  onViewMessages,
  onDelete,
  onResume,
  leftActions,
  protected: isProtected = false,
}: {
  agent: Agent;
  diskState?: DiskState | null;
  latestByFramework: Record<string, LatestVersion>;
  onViewLogs: (name: string) => void;
  onViewSkills: (name: string) => void;
  onViewMessages: (name: string) => void;
  onDelete: (name: string) => void;
  onResume: (name: string) => void;
  leftActions?: ReactNode;
  /** When true, destructive actions (delete, resume-from-paused) are hidden. */
  protected?: boolean;
}) {
  const isMobile = useIsMobile();
  const emoji = resolveAgentEmoji(agent.emoji, agent.framework);
  const latestForAgent = agent.framework ? latestByFramework[agent.framework] : undefined;
  const updateAvailable =
    agent.framework_version_sha &&
    latestForAgent &&
    latestForAgent.sha !== agent.framework_version_sha;
  // The framework an agent runs on (openclaw, hermes, ...). The emoji alone is
  // ambiguous, so surface the name as the identity sub-label. "none"/"generic"
  // agents have no meaningful framework, so the sub-label is simply omitted
  // (the host already has its own metadata slot, so don't repeat it here).
  const frameworkLabel =
    agent.framework && !["none", "generic"].includes(agent.framework)
      ? agent.framework
      : null;
  const subLabel = frameworkLabel;

  // Only allow management actions while the agent is running.
  const running = agent.status === "running";
  const disabledCls = running
    ? "hover:bg-shell-surface-hover hover:text-shell-text"
    : "opacity-40 cursor-not-allowed";
  const disabledAria = running ? undefined : "Agent is not running";

  // Icon-button base: quiet by default, brighten on hover. >=44px tap target
  // on mobile, compact on desktop.
  const btnCls = `${isMobile ? "h-11 w-11" : "h-8 w-8"} text-shell-text-tertiary`;

  const updateDot = updateAvailable ? (
    <span
      aria-label="framework update available"
      title="framework update available"
      className="inline-block w-2 h-2 bg-amber-400 rounded-full shrink-0"
    />
  ) : null;

  const actionButtons = (
    <>
      {!isProtected && agent.paused && (
        <Button
          variant="ghost"
          size="icon"
          className={`${isMobile ? "h-11 w-11" : "h-8 w-8"} text-amber-400 hover:bg-emerald-500/15 hover:text-emerald-400`}
          onClick={() => onResume(agent.name)}
          aria-label={`Resume ${agent.name}`}
          title="Resume agent"
        >
          <RotateCcw size={15} />
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon"
        className={`${btnCls} ${disabledCls}`}
        onClick={running ? () => onViewLogs(agent.name) : undefined}
        disabled={!running}
        aria-label={`View logs for ${agent.name}${disabledAria ? ` (${disabledAria})` : ""}`}
        title={running ? "View Logs" : "Agent must be running to view logs"}
      >
        <ScrollText size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={`${btnCls} ${disabledCls}`}
        onClick={running ? () => onViewSkills(agent.name) : undefined}
        disabled={!running}
        aria-label={`Manage skills for ${agent.name}${disabledAria ? ` (${disabledAria})` : ""}`}
        title={running ? "Skills" : "Agent must be running to manage skills"}
      >
        <Wrench size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={`${btnCls} ${disabledCls}`}
        onClick={running ? () => onViewMessages(agent.name) : undefined}
        disabled={!running}
        aria-label={`View messages for ${agent.name}${disabledAria ? ` (${disabledAria})` : ""}`}
        title={running ? "Messages" : "Agent must be running to view messages"}
      >
        <MessageSquare size={15} />
      </Button>
      {!isProtected && (
        <Button
          variant="ghost"
          size="icon"
          className={`${isMobile ? "h-11 w-11" : "h-8 w-8"} text-shell-text-tertiary hover:bg-red-500/15 hover:text-red-400`}
          onClick={() => onDelete(agent.name)}
          aria-label={`Delete ${agent.name}`}
          title="Delete"
        >
          <Trash2 size={15} />
        </Button>
      )}
    </>
  );

  // Shared card surface: one radius, token colours, tactile press + focus ring.
  // The "System" agent gets a faintly elevated ring + tint.
  const cardCls = [
    "taos-card-enter group rounded-xl bg-shell-surface shadow-[var(--shadow-card)]",
    "transition-[background-color,box-shadow,transform] duration-200",
    "hover:bg-shell-surface-hover hover:shadow-[var(--shadow-card-hover)]",
    "active:translate-y-px",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
    isProtected
      ? "border border-shell-border-strong bg-[color-mix(in_srgb,var(--color-accent)_6%,transparent)]"
      : "border border-shell-border",
  ].join(" ");


  if (isMobile) {
    return (
      <Card className={`${cardCls} px-3 py-3`}>
        {/* Row 1: identity tile + name/sub-label + status */}
        <div className="flex items-center gap-2.5 min-w-0">
          <IdentityTile color={agent.color} emoji={emoji} size={36} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-shell-text font-medium text-[15px] truncate">
                {agent.display_name || agent.name}
              </span>
              {updateDot}
            </div>
            {subLabel && (
              <span className="block text-shell-text-tertiary text-xs lowercase truncate">
                {subLabel}
              </span>
            )}
            <IndicatorRow agent={agent} />
          </div>
          <StatusIndicator status={agent.status} paused={agent.paused} />
        </div>
        {/* Row 2: host + vectors */}
        <div className="flex items-center gap-2 mt-2 min-w-0">
          <Server size={13} className="text-shell-text-tertiary shrink-0" />
          <span className="text-xs text-shell-text-secondary font-mono tabular-nums truncate flex-1 min-w-0">
            {agent.host}
          </span>
          <span className="inline-flex items-center gap-1 text-shell-text-tertiary tabular-nums shrink-0">
            <Database size={13} aria-hidden="true" />
            <span className="text-xs text-shell-text-secondary">{agent.vectors.toLocaleString()}</span>
            <span className="text-[10px]">vectors</span>
          </span>
        </div>
        {(agent.paused || (diskState && diskState.state !== "ok")) && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {agent.paused && <PausedChip />}
            {diskState && diskState.state !== "ok" && <DiskChip diskState={diskState} />}
          </div>
        )}
        {/* Row 3: action buttons */}
        <div className="flex items-center justify-between mt-1.5 -ml-1.5">
          <div className="flex items-center gap-0">{leftActions}</div>
          <div className="flex items-center gap-0">{actionButtons}</div>
        </div>
      </Card>
    );
  }

  return (
    <Card className={`${cardCls} flex items-center gap-4 px-4 py-3.5`}>
      {/* Identity: tile + two-line name / sub-label */}
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <IdentityTile color={agent.color} emoji={emoji} size={36} />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-shell-text font-medium text-[15px] truncate">
              {agent.display_name || agent.name}
            </span>
            {updateDot}
          </div>
          {subLabel && (
            <span className="block text-shell-text-tertiary text-xs lowercase truncate">
              {subLabel}
            </span>
          )}
          <IndicatorRow agent={agent} />
        </div>
        {agent.paused && <PausedChip />}
        {diskState && diskState.state !== "ok" && <DiskChip diskState={diskState} verbose />}
      </div>

      {/* Metadata: host */}
      <div className="hidden sm:flex items-center gap-1.5 text-sm min-w-0 w-40">
        <Server size={13} className="text-shell-text-tertiary shrink-0" />
        <span className="text-shell-text-secondary font-mono tabular-nums truncate">{agent.host}</span>
      </div>

      {/* Status (fixed-width column so the metadata columns line up row to row) */}
      <div className="w-24 shrink-0">
        <StatusIndicator status={agent.status} paused={agent.paused} />
      </div>

      {/* Vectors metric (de-emphasized) */}
      <span className="hidden sm:inline-flex items-center justify-end gap-1.5 w-28 text-right shrink-0">
        <Database size={13} className="text-shell-text-tertiary shrink-0" aria-hidden="true" />
        <span className="text-sm text-shell-text-secondary tabular-nums">
          {agent.vectors.toLocaleString()}
        </span>
        <span className="text-[10px] text-shell-text-tertiary">vectors</span>
      </span>

      {/* Actions: fixed-width + right-aligned so a protected agent's 3 icons
          reserve the same column as a deployed agent's 4 (no column drift). */}
      <div className="flex items-center justify-end gap-1 border-l border-shell-border pl-2 shrink-0 w-[152px]">
        {leftActions}
        {actionButtons}
      </div>
    </Card>
  );
}
