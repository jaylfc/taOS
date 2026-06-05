import { type ReactNode } from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { ScrollText, Trash2, Server, Wrench, MessageSquare, PauseCircle, RotateCcw, HardDrive } from "lucide-react";
import { LatestVersion } from "@/lib/framework-api";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { Button, Card } from "@/components/ui";
import { type Agent, type DiskState } from "./types";
import { STATUS_STYLES } from "./constants";

/* ------------------------------------------------------------------ */
/*  AgentRow                                                           */
/* ------------------------------------------------------------------ */

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
  // The framework an agent runs on (openclaw, hermes, …). The emoji alone is
  // ambiguous, so surface the name as a small pill. "none"/"generic" agents
  // have no meaningful framework to show.
  const frameworkLabel =
    agent.framework && !["none", "generic"].includes(agent.framework)
      ? agent.framework
      : null;
  const FrameworkPill = frameworkLabel ? (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium lowercase shrink-0 bg-white/5 text-shell-text-secondary border border-white/10"
      title={`Framework: ${frameworkLabel}`}
      aria-label={`Framework: ${frameworkLabel}`}
    >
      {frameworkLabel}
    </span>
  ) : null;

  const btnCls = isMobile ? "h-11 w-11" : "h-8 w-8";
  // Only allow management actions while the agent is running
  const running = agent.status === "running";
  const disabledCls = running
    ? "hover:bg-shell-surface-hover"
    : "opacity-40 cursor-not-allowed";
  const disabledAria = running ? undefined : "Agent is not running";

  const actionButtons = (
    <>
      {!isProtected && agent.paused && (
        <Button
          variant="ghost"
          size="icon"
          className={`${btnCls} hover:bg-emerald-500/15 hover:text-emerald-400 text-amber-400`}
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
          className={`${btnCls} hover:bg-red-500/15 hover:text-red-400`}
          onClick={() => onDelete(agent.name)}
          aria-label={`Delete ${agent.name}`}
          title="Delete"
        >
          <Trash2 size={15} />
        </Button>
      )}
    </>
  );

  if (isMobile) {
    return (
      <Card className="px-3 py-2.5 hover:bg-shell-surface/50 transition-colors">
        {/* Row 1: identity + status chip */}
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: agent.color }}
            aria-label={`Color: ${agent.color}`}
          />
          <span className="text-base leading-none shrink-0" aria-hidden="true">
            {emoji}
          </span>
          <span className="font-medium text-sm truncate flex-1 min-w-0">
            {agent.display_name || agent.name}
          </span>
          {updateAvailable && (
            <span
              aria-label="framework update available"
              title="framework update available"
              className="inline-block w-2 h-2 bg-yellow-400 rounded-full shrink-0"
            />
          )}
          <span
            className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize shrink-0 ${STATUS_STYLES[agent.status] ?? STATUS_STYLES.stopped}`}
            aria-label={`Status: ${agent.status}`}
          >
            {agent.status}
          </span>
        </div>
        {/* Row 2: host + vectors + optional chips */}
        <div className="flex items-center gap-2 mt-1 min-w-0">
          <Server size={11} className="text-shell-text-tertiary shrink-0" />
          <span className="text-xs text-shell-text-secondary truncate flex-1 min-w-0">{agent.host}</span>
          {FrameworkPill}
          <span className="text-xs text-shell-text-tertiary tabular-nums shrink-0">
            {agent.vectors.toLocaleString()} vectors
          </span>
        </div>
        {(agent.paused || (diskState && diskState.state !== "ok")) && (
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {agent.paused && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/20 text-amber-400 border border-amber-500/20"
                title="This agent is paused due to a worker failure"
              >
                <PauseCircle size={10} aria-hidden="true" />
                paused
              </span>
            )}
            {diskState && diskState.state !== "ok" && (
              <span
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${
                  diskState.state === "hard"
                    ? "bg-red-500/20 text-red-400 border-red-500/20"
                    : "bg-amber-500/20 text-amber-400 border-amber-500/20"
                }`}
                aria-label={`Disk usage: ${diskState.used_gib}/${diskState.quota_gib} GiB (${diskState.percent}%)`}
              >
                <HardDrive size={10} aria-hidden="true" />
                {diskState.used_gib}/{diskState.quota_gib} GiB
              </span>
            )}
          </div>
        )}
        {/* Row 3: action buttons */}
        <div className="flex items-center justify-between mt-1 -ml-1.5">
          <div className="flex items-center gap-0">
            {leftActions}
          </div>
          <div className="flex items-center gap-0">
            {actionButtons}
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card className="flex items-center gap-4 px-4 py-3 hover:bg-shell-surface/50 transition-colors">
      <div className="flex items-center gap-2.5 flex-1 min-w-0">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: agent.color }}
          aria-label={`Color: ${agent.color}`}
        />
        <span
          className="text-base leading-none shrink-0"
          aria-hidden="true"
        >
          {emoji}
        </span>
        <span className="font-medium text-sm truncate">{agent.display_name || agent.name}</span>
        {FrameworkPill}
        {updateAvailable && (
          <span
            aria-label="framework update available"
            title="framework update available"
            className="inline-block w-2 h-2 bg-yellow-400 rounded-full ml-1 shrink-0"
          />
        )}
        {agent.paused && (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/20 text-amber-400 border border-amber-500/20"
            title="This agent is paused due to a worker failure"
          >
            <PauseCircle size={10} aria-hidden="true" />
            paused
          </span>
        )}
        {diskState && diskState.state !== "ok" && (
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${
              diskState.state === "hard"
                ? "bg-red-500/20 text-red-400 border-red-500/20"
                : "bg-amber-500/20 text-amber-400 border-amber-500/20"
            }`}
            title={
              diskState.state === "hard"
                ? "Full — agent paused until user action"
                : `Used more than ${diskState.percent}% — needs audit or expand`
            }
            aria-label={`Disk usage: ${diskState.used_gib}/${diskState.quota_gib} GiB (${diskState.percent}%)`}
          >
            <HardDrive size={10} aria-hidden="true" />
            {diskState.used_gib}/{diskState.quota_gib} GiB ({diskState.percent}%)
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5 text-sm text-shell-text-secondary min-w-0">
        <Server size={13} className="text-shell-text-tertiary" />
        <span className="truncate">{agent.host}</span>
      </div>
      <span
        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${STATUS_STYLES[agent.status] ?? STATUS_STYLES.stopped}`}
        aria-label={`Status: ${agent.status}`}
      >
        {agent.status}
      </span>
      <span className="text-sm text-shell-text-secondary tabular-nums w-20 text-right">
        {agent.vectors.toLocaleString()}
      </span>
      <div className="flex items-center gap-1">
        {leftActions}
        {actionButtons}
      </div>
    </Card>
  );
}
