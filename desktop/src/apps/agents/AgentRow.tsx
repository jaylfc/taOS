import { type ReactNode } from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { ScrollText, Trash2, Server, Wrench, MessageSquare, PauseCircle, RotateCcw, HardDrive, Database } from "lucide-react";
import { LatestVersion } from "@/lib/framework-api";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { Button, Card } from "@/components/ui";
import { type Agent, type DiskState } from "./types";

/* ------------------------------------------------------------------ */
/*  AgentRow                                                           */
/* ------------------------------------------------------------------ */

type AgentStatus = Agent["status"];

/** Semantic colour + copy for the status indicator. Colour here carries
 *  meaning (running/paused/error), so Tailwind colour utilities are allowed,
 *  kept at tasteful alpha so they read in both the dark and light themes. The
 *  pill tints its background + border from the same semantic colour. */
type StatusMeta = { label: string; dot: string; text: string; pill: string; pulse: boolean };

const STATUS_STOPPED: StatusMeta = {
  label: "Stopped",
  dot: "bg-shell-text-tertiary",
  text: "text-shell-text-tertiary",
  pill: "bg-shell-surface-hover border-shell-border",
  pulse: false,
};
const STATUS_META: Record<string, StatusMeta> = {
  running: { label: "Running", dot: "bg-emerald-400", text: "text-emerald-400", pill: "bg-emerald-500/12 border-emerald-500/25", pulse: true },
  stopped: STATUS_STOPPED,
  error: { label: "Error", dot: "bg-red-400", text: "text-red-400", pill: "bg-red-500/12 border-red-500/25", pulse: false },
  deploying: { label: "Deploying", dot: "bg-amber-400", text: "text-amber-400", pill: "bg-amber-500/12 border-amber-500/25", pulse: false },
};

const STATUS_PAUSED: StatusMeta = {
  label: "Paused",
  dot: "bg-amber-400",
  text: "text-amber-400",
  pill: "bg-amber-500/12 border-amber-500/25",
  pulse: false,
};

/** A pill chip with a glowing dot + label conveying live agent state. The
 *  running dot breathes via a halo that is disabled under
 *  prefers-reduced-motion (see tokens.css). */
function StatusIndicator({ status, paused }: { status: AgentStatus; paused?: boolean }) {
  // A paused agent reads as paused regardless of its container status.
  const meta: StatusMeta = paused ? STATUS_PAUSED : STATUS_META[status] ?? STATUS_STOPPED;
  return (
    <span
      className={`inline-flex items-center gap-1.5 shrink-0 rounded-full border px-2.5 py-1 ${meta.pill}`}
      aria-label={`Status: ${meta.label}`}
    >
      <span className="relative inline-flex h-1.5 w-1.5 shrink-0">
        {meta.pulse && (
          <span
            aria-hidden="true"
            className={`taos-status-pulse absolute inset-0 rounded-full ${meta.dot}`}
          />
        )}
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      </span>
      <span className={`text-xs font-semibold ${meta.text}`}>{meta.label}</span>
    </span>
  );
}

/** The agent-identity tile: a glossy, colour-tinted rounded square holding the
 *  emoji. The gradient + tinted border + inner highlight + soft glow give it the
 *  Store's depth (the colour is the agent's own identity colour, not a theme
 *  literal, so it carries per-agent meaning and reads in both themes). */
function IdentityTile({ color, emoji, size = 36 }: { color: string; emoji: string; size?: number }) {
  return (
    <span
      className="relative inline-flex items-center justify-center rounded-xl border shrink-0 overflow-hidden"
      style={{
        width: size,
        height: size,
        background: `linear-gradient(145deg, ${color}38, ${color}12)`,
        borderColor: `${color}40`,
        boxShadow: `inset 0 1px 0 0 ${color}33, 0 3px 10px -3px ${color}2b`,
      }}
      aria-hidden="true"
    >
      <span className="relative text-lg leading-none">{emoji}</span>
    </span>
  );
}

/** The framework an agent runs on (openclaw, hermes, ...) as a small uppercase
 *  accent-tinted pill, echoing the Store's framework tags. */
function FrameworkPill({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-full bg-accent/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-accent border border-accent/20 shrink-0">
      {label}
    </span>
  );
}

/** A compact metadata chip (host, vectors) with a small leading icon, matching
 *  the mockup's `.chip`: deep surface, hairline border, rounded-full. */
function MetricChip({
  icon: Icon,
  children,
  title,
  className = "",
}: {
  icon: typeof Server;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full bg-shell-bg-deep border border-shell-border px-2.5 py-1 text-[11px] text-shell-text-secondary ${className}`}
      title={title}
    >
      <Icon size={11} className="text-shell-text-tertiary shrink-0" aria-hidden="true" />
      {children}
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

/** The model label under an agent's identity: plain muted text, no pill or
 *  icon. Renders nothing when there is no model. */
function IndicatorRow({ agent }: { agent: Agent }) {
  if (!agent.model) return null;
  return (
    <span
      className="block mt-0.5 text-[11px] text-shell-text-tertiary font-mono truncate"
      title={`Model: ${agent.model}`}
    >
      {agent.model}
    </span>
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

  // A thin vertical accent bar keyed to the agent's identity colour, hugging
  // the card's left edge (the card clips it via overflow-hidden + rounded).
  const accentBar = (
    <span
      aria-hidden="true"
      className="absolute left-0 top-0 bottom-0 w-[3px] rounded-r-[3px]"
      style={{ background: `linear-gradient(180deg, ${agent.color}, ${agent.color}aa)` }}
    />
  );

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
    "taos-card-enter group relative overflow-hidden rounded-xl bg-shell-surface shadow-[var(--shadow-card)]",
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
        {accentBar}
        {/* Row 1: identity tile + name/framework + status pill */}
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
              <div className="mt-1">
                <FrameworkPill label={subLabel} />
              </div>
            )}
            <IndicatorRow agent={agent} />
          </div>
          <StatusIndicator status={agent.status} paused={agent.paused} />
        </div>
        {/* Row 2: host + vectors chips */}
        <div className="flex items-center gap-2 mt-2 min-w-0">
          <MetricChip icon={Server} title={`Host: ${agent.host}`} className="min-w-0 flex-1">
            <span className="font-mono tabular-nums truncate">{agent.host}</span>
          </MetricChip>
          <MetricChip icon={Database} title={`${agent.vectors.toLocaleString()} vectors`}>
            <span className="tabular-nums">{agent.vectors.toLocaleString()}</span>
            <span className="text-shell-text-tertiary">vectors</span>
          </MetricChip>
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
      {accentBar}
      {/* Identity: tile + name (with framework pill) / model */}
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <IdentityTile color={agent.color} emoji={emoji} size={36} />
        <div className="min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-shell-text font-medium text-[15px] truncate">
              {agent.display_name || agent.name}
            </span>
            {subLabel && <FrameworkPill label={subLabel} />}
            {updateDot}
          </div>
          <IndicatorRow agent={agent} />
        </div>
        {agent.paused && <PausedChip />}
        {diskState && diskState.state !== "ok" && <DiskChip diskState={diskState} verbose />}
      </div>

      {/* Metadata chips: host + vectors */}
      <div className="hidden sm:flex items-center gap-2 shrink-0">
        <MetricChip icon={Server} title={`Host: ${agent.host}`} className="max-w-44">
          <span className="font-mono tabular-nums truncate">{agent.host}</span>
        </MetricChip>
        <MetricChip
          icon={Database}
          title={`${agent.vectors.toLocaleString()} vectors`}
        >
          <span className="tabular-nums">{agent.vectors.toLocaleString()}</span>
          <span className="text-shell-text-tertiary">vectors</span>
        </MetricChip>
      </div>

      {/* Status pill (fixed-width column so it lines up row to row) */}
      <div className="w-24 shrink-0 flex justify-end">
        <StatusIndicator status={agent.status} paused={agent.paused} />
      </div>

      {/* Actions: fixed-width + right-aligned so a protected agent's 3 icons
          reserve the same column as a deployed agent's 4 (no column drift).
          Calm at rest, revealed on card hover / keyboard focus. */}
      <div className="flex items-center justify-end gap-1 border-l border-shell-border pl-2 shrink-0 w-[152px] opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        {leftActions}
        {actionButtons}
      </div>
    </Card>
  );
}
