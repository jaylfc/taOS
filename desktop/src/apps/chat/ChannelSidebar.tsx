import {
  MessageCircle,
  Hash,
  Users,
  Wifi,
  WifiOff,
  ChevronRight,
  Bot,
  Archive,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui";
import { MessageAvatar } from "./MessageAvatar";
import { A2aBusSection, type BusChannel } from "./A2aBusPanel";
import type { Channel, LiveAgent, ArchivedAgentEntry, ProjectGroup, WsStatus } from "./types";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface SectionDef {
  label: string;
  icon: React.ReactNode;
  items: Channel[];
}

export interface ChannelSidebarProps {
  isMobile: boolean;
  wsStatus: WsStatus;
  allEmpty: boolean;
  sections: SectionDef[];
  collapsedSections: Record<string, boolean>;
  onToggleSection: (key: string) => void;
  visibleInSection: (items: Channel[], key: string) => Channel[];
  selectedChannel: string | null;
  onSelectChannel: (id: string) => void;
  unread: Record<string, number>;
  /** Current time for relative timestamps — pass `Date.now()` or a cached value. */
  nowMs: number;
  liveAgents: LiveAgent[];
  archivedAgents: ArchivedAgentEntry[];
  archivedChannels: Channel[];
  archivedExpanded: boolean;
  onToggleArchived: () => void;
  scope?: { projectId?: string };
  projectGroups: ProjectGroup[];
  projectsExpanded: boolean;
  onToggleProjects: () => void;
  projectChannelExpanded: Record<string, boolean>;
  onToggleProjectChannel: (projectId: string) => void;
  onOpenAgentsApp: () => void;
  onRestoreArchivedChannel: (channelId: string, channelName: string) => void;
  onDeleteArchivedChannel: (channelId: string) => void;
  bus: {
    channels: BusChannel[];
    available: boolean;
    loaded: boolean;
  };
  busSelected: string | null;
  onSelectBusChannel: (channel: string) => void;
  /** Relative time formatter. */
  formatRelativeTime: (ts: number | string, nowMs: number) => string;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function ChannelSidebar(props: ChannelSidebarProps) {
  const {
    isMobile,
    wsStatus,
    allEmpty,
    sections,
    collapsedSections,
    onToggleSection,
    visibleInSection,
    selectedChannel,
    onSelectChannel,
    unread,
    nowMs,
    archivedChannels,
    archivedExpanded,
    onToggleArchived,
    scope,
    projectGroups,
    projectsExpanded,
    onToggleProjects,
    projectChannelExpanded,
    onToggleProjectChannel,
    onOpenAgentsApp,
    onRestoreArchivedChannel,
    onDeleteArchivedChannel,
    bus,
    busSelected,
    onSelectBusChannel,
    formatRelativeTime,
  } = props;

  if (isMobile) {
    return (
      <div style={{ padding: "8px 0 16px" }}>
        {/* connection status */}
        <ConnectionStatus wsStatus={wsStatus} />

        {allEmpty ? (
          <EmptyState onOpenAgentsApp={onOpenAgentsApp} />
        ) : (
          sections.map((section) => (
            <div key={section.label} style={{ marginBottom: 20 }}>
              <button
                type="button"
                onClick={() => onToggleSection(section.label)}
                aria-expanded={!collapsedSections[section.label]}
                style={{
                  fontSize: 12,
                  textTransform: "uppercase" as const,
                  letterSpacing: 0.5,
                  color: "var(--color-shell-text-secondary)",
                  padding: "0 20px 6px",
                  fontWeight: 600,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  width: "100%",
                }}
              >
                <ChevronRight
                  size={13}
                  aria-hidden="true"
                  style={{
                    transition: "transform 0.15s",
                    transform: collapsedSections[section.label] ? "none" : "rotate(90deg)",
                  }}
                />
                {section.icon} {section.label}
              </button>
              {visibleInSection(section.items, section.label).length === 0 ? (
                collapsedSections[section.label] ? null : (
                  <div
                    style={{
                      padding: "0 20px",
                      fontSize: 12,
                      color: "var(--color-shell-text-tertiary)",
                      fontStyle: "italic",
                    }}
                  >
                    None yet
                  </div>
                )
              ) : (
                <div
                  style={{
                    margin: "0 12px",
                    borderRadius: 16,
                    background: "var(--color-shell-surface)",
                    border: "1px solid var(--color-shell-border)",
                    overflow: "hidden",
                  }}
                >
                  {visibleInSection(section.items, section.label).map(
                    (ch, idx, arr) => {
                      const isA2A = ch.settings?.kind === "a2a";
                      const agentMember =
                        ch.type === "dm"
                          ? (ch.members ?? []).find((m) => m !== "user")
                          : undefined;
                      const count = unread[ch.id] ?? 0;
                      return (
                        <button
                          key={ch.id}
                          type="button"
                          onClick={() => onSelectChannel(ch.id)}
                          aria-label={`Channel ${ch.name}`}
                          title={
                            isA2A
                              ? "Agent coordination — mention @<slug> to hand off."
                              : undefined
                          }
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 12,
                            width: "100%",
                            padding: "11px 14px",
                            background:
                              selectedChannel === ch.id
                                ? "var(--color-shell-surface-active)"
                                : "none",
                            border: "none",
                            borderBottom:
                              idx === arr.length - 1
                                ? "none"
                                : "1px solid var(--color-shell-border)",
                            cursor: "pointer",
                            color: "inherit",
                            textAlign: "left" as const,
                          }}
                        >
                          {agentMember ? (
                            <MessageAvatar
                              size={38}
                              authorId={agentMember}
                              displayName={agentMember}
                              kind="agent"
                            />
                          ) : isA2A ? (
                            <div
                              style={{
                                width: 38,
                                height: 38,
                                borderRadius: 11,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                background: "var(--color-accent-soft)",
                                border: "1px solid var(--color-accent-line)",
                                color: "var(--color-accent-strong)",
                                flexShrink: 0,
                              }}
                            >
                              <Bot size={18} aria-hidden />
                            </div>
                          ) : (
                            <div
                              style={{
                                width: 38,
                                height: 38,
                                borderRadius: 11,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                background:
                                  "var(--color-shell-surface-active)",
                                color: "var(--color-shell-text-secondary)",
                                flexShrink: 0,
                              }}
                            >
                              {ch.type === "group" ? (
                                <Users size={18} aria-hidden />
                              ) : (
                                <Hash size={18} aria-hidden />
                              )}
                            </div>
                          )}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div
                              style={{
                                display: "flex",
                                alignItems: "baseline",
                                gap: 8,
                              }}
                            >
                              <span
                                style={{
                                  flex: 1,
                                  fontSize: 15,
                                  fontWeight: count > 0 ? 700 : 600,
                                  color: "var(--color-shell-text)",
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {ch.name}
                              </span>
                              {ch.last_message_at && (
                                <span
                                  style={{
                                    fontSize: 11,
                                    color: "var(--color-shell-text-tertiary)",
                                    flexShrink: 0,
                                  }}
                                >
                                  {formatRelativeTime(ch.last_message_at, nowMs)}
                                </span>
                              )}
                            </div>
                            {ch.lastPreview && (
                              <div
                                style={{
                                  fontSize: 13,
                                  color: "var(--color-shell-text-secondary)",
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  whiteSpace: "nowrap",
                                  marginTop: 1,
                                }}
                              >
                                {ch.lastPreview}
                              </div>
                            )}
                          </div>
                          {count > 0 && (
                            <span
                              style={{
                                background: "var(--color-unread)",
                                color: "#fff",
                                fontSize: 10,
                                fontWeight: 700,
                                borderRadius: 9999,
                                minWidth: 18,
                                height: 18,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                padding: "0 5px",
                                flexShrink: 0,
                              }}
                            >
                              {count}
                            </span>
                          )}
                        </button>
                      );
                    },
                  )}
                </div>
              )}
            </div>
          ))
        )}

        {/* Projects section — mobile */}
        <ProjectsSectionMobile
          projectGroups={projectGroups}
          projectsExpanded={projectsExpanded}
          onToggleProjects={onToggleProjects}
          projectChannelExpanded={projectChannelExpanded}
          onToggleProjectChannel={onToggleProjectChannel}
          selectedChannel={selectedChannel}
          onSelectChannel={onSelectChannel}
          unread={unread}
          scope={scope}
        />

        {/* Archived channels — mobile */}
        <ArchivedSection
          isMobile={true}
          archivedChannels={archivedChannels}
          archivedExpanded={archivedExpanded}
          onToggleArchived={onToggleArchived}
          selectedChannel={selectedChannel}
          onSelectChannel={onSelectChannel}
          onRestoreArchivedChannel={onRestoreArchivedChannel}
          onDeleteArchivedChannel={onDeleteArchivedChannel}
          archivedAgents={props.archivedAgents}
        />

        {/* External taOSmd coordination bus (read-only) */}
        <A2aBusSection
          channels={bus.channels}
          available={bus.available}
          loaded={bus.loaded}
          selected={busSelected}
          onSelect={onSelectBusChannel}
        />
      </div>
    );
  }

  /* Desktop: compact sidebar */
  return (
    <div className="w-full flex flex-col h-full">
      {/* connection status */}
      <div className="px-3 py-1.5 text-[11px] flex items-center gap-1.5">
        {wsStatus === "connected" ? (
          <>
            <Wifi size={11} className="text-emerald-400" />
            <span className="text-emerald-400/80">Connected</span>
          </>
        ) : wsStatus === "connecting" ? (
          <>
            <Wifi size={11} className="text-amber-400 animate-pulse" />
            <span className="text-amber-400/80">Connecting...</span>
          </>
        ) : (
          <>
            <WifiOff size={11} className="text-red-400" />
            <span className="text-red-400/80">Offline</span>
          </>
        )}
      </div>

      {/* channel list */}
      <div className="flex-1 overflow-y-auto py-1">
        {allEmpty ? (
          <div className="flex flex-col items-center justify-center h-full px-4 py-10 text-center gap-2.5">
            <MessageCircle size={28} className="text-white/15" aria-hidden="true" />
            <p className="text-[13px] font-medium text-white/60">
              No conversations yet
            </p>
            <p className="text-[11px] text-white/30">
              Deploy an agent to start chatting
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={onOpenAgentsApp}
              className="mt-1 text-xs"
            >
              Open Agents
            </Button>
          </div>
        ) : (
          sections.map((section) => (
            <div key={section.label}>
              <button
                type="button"
                onClick={() => onToggleSection(section.label)}
                aria-expanded={!collapsedSections[section.label]}
                className="w-full px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-white/30 hover:text-white/50 flex items-center gap-1.5 transition-colors"
              >
                <ChevronRight
                  size={11}
                  aria-hidden="true"
                  className={`transition-transform ${
                    collapsedSections[section.label] ? "" : "rotate-90"
                  }`}
                />
                {section.icon} {section.label}
              </button>
              {!collapsedSections[section.label] &&
                section.items.length === 0 && (
                  <div className="px-3 py-1 text-[11px] text-white/20 italic">
                    None yet
                  </div>
                )}
              <div className="px-2 flex flex-col gap-px">
                {visibleInSection(section.items, section.label).map((ch) => {
                  const isA2A = ch.settings?.kind === "a2a";
                  const agentMember =
                    ch.type === "dm"
                      ? (ch.members ?? []).find((m) => m !== "user")
                      : undefined;
                  const count = unread[ch.id] ?? 0;
                  return (
                    <button
                      key={ch.id}
                      type="button"
                      onClick={() => onSelectChannel(ch.id)}
                      aria-pressed={selectedChannel === ch.id}
                      className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-[10px] text-left transition-colors ${
                        selectedChannel === ch.id
                          ? "bg-shell-surface-active"
                          : "hover:bg-shell-surface-hover"
                      }`}
                      aria-label={`Channel ${ch.name}`}
                      title={
                        isA2A
                          ? "Agent coordination — mention @<slug> to hand off."
                          : undefined
                      }
                    >
                      {agentMember ? (
                        <MessageAvatar
                          size={30}
                          authorId={agentMember}
                          displayName={agentMember}
                          kind="agent"
                        />
                      ) : isA2A ? (
                        <span className="shrink-0 grid place-items-center w-[30px] h-[30px] rounded-[9px] bg-accent-soft border border-accent-line text-accent-strong">
                          <Bot size={15} aria-hidden />
                        </span>
                      ) : (
                        <span className="shrink-0 grid place-items-center w-[30px] h-[30px] rounded-[9px] bg-shell-surface-active text-shell-text-secondary">
                          {ch.type === "group" ? (
                            <Users size={15} aria-hidden />
                          ) : (
                            <Hash size={15} aria-hidden />
                          )}
                        </span>
                      )}
                      <span
                        className={`truncate flex-1 text-[14px] tracking-tight ${
                          count > 0
                            ? "font-bold text-shell-text"
                            : "font-semibold text-shell-text"
                        }`}
                      >
                        {ch.name}
                      </span>
                      {count > 0 && (
                        <span className="shrink-0 bg-unread text-white text-[10px] font-bold rounded-full min-w-[18px] h-[18px] flex items-center justify-center px-1 tabular-nums">
                          {count}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))
        )}

        {/* Projects section — desktop */}
        {!scope?.projectId && projectGroups.length > 0 && (
          <details className="px-3 mt-2">
            <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-white/30 py-1">
              Projects
            </summary>
            {projectGroups.map((g) => (
              <details key={g.id} className="ml-2 mt-1">
                <summary className="cursor-pointer text-xs text-white/60 py-1">
                  {g.name}
                </summary>
                <div className="ml-2 mt-0.5">
                  {g.channels.map((ch) => (
                    <button
                      key={ch.id}
                      type="button"
                      onClick={() => onSelectChannel(ch.id)}
                      aria-pressed={selectedChannel === ch.id}
                      aria-label={`Channel ${ch.name}`}
                      title={
                        ch.settings?.kind === "a2a"
                          ? "Agent coordination — mention @<slug> to hand off."
                          : undefined
                      }
                      className={`w-full text-left text-xs py-1 px-2 rounded flex items-center gap-1.5 ${
                        selectedChannel === ch.id
                          ? "bg-white/10"
                          : "hover:bg-white/5"
                      }`}
                    >
                      {ch.settings?.kind === "a2a" && (
                        <Bot
                          size={12}
                          aria-hidden
                          style={{
                            color: "rgba(255,255,255,0.6)",
                            flexShrink: 0,
                          }}
                        />
                      )}
                      {ch.name}
                    </button>
                  ))}
                </div>
              </details>
            ))}
          </details>
        )}

        {/* Archived channels section — desktop */}
        <ArchivedSection
          isMobile={false}
          archivedChannels={archivedChannels}
          archivedExpanded={archivedExpanded}
          onToggleArchived={onToggleArchived}
          selectedChannel={selectedChannel}
          onSelectChannel={onSelectChannel}
          onRestoreArchivedChannel={onRestoreArchivedChannel}
          onDeleteArchivedChannel={onDeleteArchivedChannel}
          archivedAgents={props.archivedAgents}
        />

        {/* External taOSmd coordination bus (read-only) */}
        <A2aBusSection
          channels={bus.channels}
          available={bus.available}
          loaded={bus.loaded}
          selected={busSelected}
          onSelect={onSelectBusChannel}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Internal sub-components                                            */
/* ------------------------------------------------------------------ */

function ConnectionStatus({ wsStatus }: { wsStatus: WsStatus }) {
  return (
    <div
      style={{
        padding: "0 20px 8px",
        fontSize: 11,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      {wsStatus === "connected" ? (
        <>
          <Wifi size={11} style={{ color: "#34d399" }} />
          <span style={{ color: "rgba(52,211,153,0.8)" }}>Connected</span>
        </>
      ) : wsStatus === "connecting" ? (
        <>
          <Wifi size={11} style={{ color: "#fbbf24" }} />
          <span style={{ color: "rgba(251,191,36,0.8)" }}>Connecting…</span>
        </>
      ) : (
        <>
          <WifiOff size={11} style={{ color: "#f87171" }} />
          <span style={{ color: "rgba(248,113,113,0.8)" }}>Offline</span>
        </>
      )}
    </div>
  );
}

function EmptyState({ onOpenAgentsApp }: { onOpenAgentsApp: () => void }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
        textAlign: "center",
        gap: 12,
      }}
    >
      <MessageCircle
        size={36}
        style={{ color: "var(--color-shell-text-tertiary)" }}
        aria-hidden="true"
      />
      <p
        style={{
          fontSize: 15,
          fontWeight: 600,
          color: "var(--color-shell-text)",
          margin: 0,
        }}
      >
        No conversations yet
      </p>
      <p
        style={{
          fontSize: 13,
          color: "var(--color-shell-text-secondary)",
          margin: 0,
        }}
      >
        Deploy an agent to start chatting
      </p>
      <button
        type="button"
        onClick={onOpenAgentsApp}
        style={{
          marginTop: 4,
          fontSize: 13,
          padding: "8px 16px",
          borderRadius: 10,
          background: "var(--color-accent-soft)",
          border: "1px solid var(--color-accent-line)",
          color: "var(--color-accent-strong)",
          cursor: "pointer",
        }}
      >
        Open Agents
      </button>
    </div>
  );
}

function ProjectsSectionMobile({
  projectGroups,
  projectsExpanded,
  onToggleProjects,
  projectChannelExpanded,
  onToggleProjectChannel,
  selectedChannel,
  onSelectChannel,
  unread,
  scope,
}: {
  projectGroups: ProjectGroup[];
  projectsExpanded: boolean;
  onToggleProjects: () => void;
  projectChannelExpanded: Record<string, boolean>;
  onToggleProjectChannel: (projectId: string) => void;
  selectedChannel: string | null;
  onSelectChannel: (id: string) => void;
  unread: Record<string, number>;
  scope?: { projectId?: string };
}) {
  if (scope?.projectId || projectGroups.length === 0) return null;

  return (
    <div style={{ marginBottom: 20 }}>
      <button
        type="button"
        onClick={onToggleProjects}
        aria-expanded={projectsExpanded}
        aria-controls="projects-section-mobile"
        style={{
          fontSize: 12,
          textTransform: "uppercase" as const,
          letterSpacing: 0.5,
          color: "var(--color-shell-text-secondary)",
          padding: "0 20px 6px",
          fontWeight: 600,
          display: "flex",
          alignItems: "center",
          gap: 6,
          background: "none",
          border: "none",
          cursor: "pointer",
          width: "100%",
        }}
      >
        <ChevronRight
          size={12}
          style={{
            transition: "transform 0.15s",
            transform: projectsExpanded ? "rotate(90deg)" : "none",
            color: "var(--color-shell-text-tertiary)",
          }}
          aria-hidden="true"
        />
        Projects ({projectGroups.length})
      </button>
      <div
        id="projects-section-mobile"
        style={{ display: projectsExpanded ? "block" : "none" }}
      >
        {projectGroups.map((g) => {
          const isOpen = projectChannelExpanded[g.id] !== false;
          return (
            <div key={g.id} style={{ marginBottom: 12 }}>
              <button
                type="button"
                onClick={() => onToggleProjectChannel(g.id)}
                aria-expanded={isOpen}
                aria-controls={`project-section-mobile-${g.id}`}
                style={{
                  fontSize: 11,
                  color: "var(--color-shell-text-secondary)",
                  padding: "0 20px 4px",
                  fontWeight: 600,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  width: "100%",
                }}
              >
                <ChevronRight
                  size={10}
                  style={{
                    transition: "transform 0.15s",
                    transform: isOpen ? "rotate(90deg)" : "none",
                    color: "var(--color-shell-text-tertiary)",
                  }}
                  aria-hidden="true"
                />
                {g.name}
              </button>
              <div
                id={`project-section-mobile-${g.id}`}
                style={{ display: isOpen ? "block" : "none" }}
              >
                <div
                  style={{
                    margin: "0 12px",
                    borderRadius: 16,
                    background: "rgba(255,255,255,0.05)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    overflow: "hidden",
                  }}
                >
                  {g.channels.map((ch, idx, arr) => (
                    <button
                      key={ch.id}
                      type="button"
                      onClick={() => onSelectChannel(ch.id)}
                      aria-label={`Channel ${ch.name}`}
                      title={
                        ch.settings?.kind === "a2a"
                          ? "Agent coordination — mention @<slug> to hand off."
                          : undefined
                      }
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        width: "100%",
                        padding: "14px 16px",
                        background:
                          selectedChannel === ch.id
                            ? "var(--color-shell-surface-active)"
                            : "none",
                        border: "none",
                        borderBottom:
                          idx === arr.length - 1
                            ? "none"
                            : "1px solid var(--color-shell-border)",
                        cursor: "pointer",
                        color: "inherit",
                        textAlign: "left" as const,
                      }}
                    >
                      {ch.settings?.kind === "a2a" && (
                        <Bot
                          size={14}
                          aria-hidden
                          style={{
                            color: "var(--color-shell-text-secondary)",
                            flexShrink: 0,
                          }}
                        />
                      )}
                      <span
                        style={{
                          flex: 1,
                          fontSize: 15,
                          fontWeight: 400,
                          color: "var(--color-shell-text)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {ch.name}
                      </span>
                      {(unread[ch.id] ?? 0) > 0 && (
                        <span
                          style={{
                            background: "var(--color-unread)",
                            color: "#fff",
                            fontSize: 10,
                            fontWeight: 700,
                            borderRadius: 9999,
                            minWidth: 18,
                            height: 18,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            padding: "0 4px",
                          }}
                        >
                          {unread[ch.id]}
                        </span>
                      )}
                      <ChevronRight
                        size={16}
                        style={{
                          color: "var(--color-shell-text-tertiary)",
                          flexShrink: 0,
                        }}
                      />
                    </button>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ArchivedSection({
  isMobile,
  archivedChannels,
  archivedExpanded,
  onToggleArchived,
  selectedChannel,
  onSelectChannel,
  onRestoreArchivedChannel,
  onDeleteArchivedChannel,
  archivedAgents,
}: {
  isMobile: boolean;
  archivedChannels: Channel[];
  archivedExpanded: boolean;
  onToggleArchived: () => void;
  selectedChannel: string | null;
  onSelectChannel: (id: string) => void;
  onRestoreArchivedChannel: (channelId: string, name: string) => void;
  onDeleteArchivedChannel: (channelId: string) => void;
  archivedAgents: ArchivedAgentEntry[];
}) {
  if (archivedChannels.length === 0) return null;

  if (isMobile) {
    return (
      <div style={{ marginBottom: 20 }}>
        <button
          type="button"
          onClick={onToggleArchived}
          aria-expanded={archivedExpanded}
          aria-controls="archived-channels-mobile"
          style={{
            fontSize: 12,
            textTransform: "uppercase" as const,
            letterSpacing: 0.5,
            color: "var(--color-shell-text-tertiary)",
            padding: "0 20px 6px",
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            gap: 6,
            background: "none",
            border: "none",
            cursor: "pointer",
            width: "100%",
          }}
        >
          <ChevronRight
            size={12}
            style={{
              transition: "transform 0.15s",
              transform: archivedExpanded ? "rotate(90deg)" : "none",
              color: "var(--color-shell-text-tertiary)",
            }}
            aria-hidden="true"
          />
          <Archive size={12} aria-hidden="true" />
          Archived ({archivedChannels.length})
        </button>
        <div
          id="archived-channels-mobile"
          style={{ display: archivedExpanded ? "block" : "none" }}
        >
          <div
            style={{
              margin: "0 12px",
              borderRadius: 16,
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.06)",
              overflow: "hidden",
            }}
          >
            {archivedChannels.map((ch, idx, arr) => {
              const agentId = ch.settings?.archived_agent_id;
              const hasAgent = agentId
                ? archivedAgents.some((a) => a.id === agentId)
                : false;
              return (
                <div
                  key={ch.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    borderBottom:
                      idx === arr.length - 1
                        ? "none"
                        : "1px solid rgba(255,255,255,0.04)",
                    opacity: 0.6,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onSelectChannel(ch.id)}
                    aria-label={`Archived channel ${ch.name}`}
                    style={{
                      flex: 1,
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "12px 8px 12px 16px",
                      background:
                        selectedChannel === ch.id
                          ? "var(--color-shell-surface-active)"
                          : "none",
                      border: "none",
                      cursor: "pointer",
                      color: "inherit",
                      textAlign: "left" as const,
                      minWidth: 0,
                    }}
                  >
                    <Archive
                      size={11}
                      aria-hidden="true"
                      style={{
                        color: "var(--color-shell-text-tertiary)",
                        flexShrink: 0,
                      }}
                    />
                    <span
                      style={{
                        flex: 1,
                        fontSize: 14,
                        color: "var(--color-shell-text-secondary)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {ch.name}
                    </span>
                  </button>
                  <div style={{ display: "flex", gap: 2, paddingRight: 8 }}>
                    <button
                      type="button"
                      onClick={() =>
                        onRestoreArchivedChannel(ch.id, ch.name)
                      }
                      disabled={!hasAgent}
                      aria-label={`Restore archived channel ${ch.name}`}
                      title={
                        hasAgent
                          ? "Restore agent"
                          : "Agent entry missing — delete only"
                      }
                      style={{
                        background: "none",
                        border: "none",
                        cursor: hasAgent ? "pointer" : "not-allowed",
                        color: hasAgent
                          ? "rgba(52,211,153,0.7)"
                          : "rgba(255,255,255,0.2)",
                        padding: "6px",
                      }}
                    >
                      <RotateCcw size={13} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeleteArchivedChannel(ch.id)}
                      aria-label={`Permanently delete archived channel ${ch.name}`}
                      title="Delete permanently"
                      style={{
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        color: "rgba(248,113,113,0.7)",
                        padding: "6px",
                      }}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  /* Desktop archived section */
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={onToggleArchived}
        className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-white/25 hover:text-white/40 transition-colors w-full text-left"
        aria-expanded={archivedExpanded}
        aria-controls="archived-channels-desktop"
      >
        <ChevronRight
          size={11}
          className={`transition-transform ${
            archivedExpanded ? "rotate-90" : ""
          }`}
          aria-hidden="true"
        />
        <Archive size={11} aria-hidden="true" />
        Archived ({archivedChannels.length})
      </button>
      <div
        id="archived-channels-desktop"
        className={archivedExpanded ? "" : "hidden"}
      >
        {archivedChannels.map((ch) => {
          const agentId = ch.settings?.archived_agent_id;
          const hasAgent = agentId
            ? archivedAgents.some((a) => a.id === agentId)
            : false;
          return (
            <div
              key={ch.id}
              className="group relative flex items-center opacity-60 hover:opacity-80 transition-opacity"
            >
              <Button
                variant={
                  selectedChannel === ch.id ? "secondary" : "ghost"
                }
                onClick={() => onSelectChannel(ch.id)}
                className="flex-1 justify-start h-auto py-1.5 pl-3 pr-1 text-[13px] rounded-none font-normal min-w-0"
                aria-label={`Archived channel ${ch.name}`}
              >
                <Archive
                  size={11}
                  className="shrink-0 mr-1.5 text-white/40"
                  aria-hidden="true"
                />
                <span className="truncate flex-1 text-left">{ch.name}</span>
              </Button>
              <div className="hidden group-hover:flex items-center shrink-0 pr-1">
                <button
                  type="button"
                  onClick={() =>
                    onRestoreArchivedChannel(ch.id, ch.name)
                  }
                  disabled={!hasAgent}
                  aria-label={`Restore archived channel ${ch.name}`}
                  title={
                    hasAgent
                      ? "Restore agent"
                      : "Agent entry missing — delete only"
                  }
                  className={`p-1 rounded transition-colors ${
                    hasAgent
                      ? "text-white/30 hover:text-emerald-400 hover:bg-emerald-500/10 cursor-pointer"
                      : "text-white/15 cursor-not-allowed"
                  }`}
                >
                  <RotateCcw size={12} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => onDeleteArchivedChannel(ch.id)}
                  aria-label={`Permanently delete archived channel ${ch.name}`}
                  title="Delete permanently"
                  className="p-1 rounded text-white/30 hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer"
                >
                  <Trash2 size={12} aria-hidden="true" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
