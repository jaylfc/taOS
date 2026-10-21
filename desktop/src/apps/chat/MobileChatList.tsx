import { useMemo, useState, useEffect } from "react";
import { Bot, Globe, Hash, Radio, Users } from "lucide-react";
import { MessageAvatar } from "./MessageAvatar";
import { A2aBusSection, type BusChannel } from "./A2aBusPanel";
import type { Channel, ProjectGroup } from "./types";

type FilterKey = "all" | "agents" | "people" | "groups" | "projects";

export interface MobileChatListProps {
  /** Same shape as ChannelSidebar's `sections`; only `items` is read. */
  sections: { items: Channel[] }[];
  projectGroups: ProjectGroup[];
  selectedChannel: string | null;
  onSelectChannel: (id: string) => void;
  unread: Record<string, number>;
  nowMs: number;
  formatRelativeTime: (ts: number | string, nowMs: number) => string;
  bus: { channels: BusChannel[]; available: boolean; loaded: boolean };
  busSelected: string | null;
  onSelectBusChannel: (channel: string) => void;
  /** Agent names (slugs and display names) to classify DMs. If omitted, all DMs stay under Agents for back-compat. */
  agentNames?: string[];
}

interface Row {
  channel: Channel;
  projectName?: string;
}

function isAgentDm(channel: Channel, agentNames: string[] | undefined): boolean {
  if (channel.type !== "dm" && channel.type !== "dm-remote") return false;
  if (agentNames === undefined) return true; // back-compat: all DMs are agents
  const members = channel.members ?? [];
  const lowerAgents = new Set(agentNames.map((n) => n.toLowerCase()));
  return members.some((m) => m !== "user" && lowerAgents.has(m.toLowerCase()));
}

function isPeopleDm(channel: Channel, agentNames: string[] | undefined): boolean {
  if (agentNames === undefined) return false; // back-compat: no people DMs
  if (channel.type === "dm-remote") return true;
  if (channel.type !== "dm") return false;
  const members = channel.members ?? [];
  const lowerAgents = new Set(agentNames.map((n) => n.toLowerCase()));
  return !members.some((m) => m !== "user" && lowerAgents.has(m.toLowerCase()));
}

const FILTERS: { key: FilterKey; label: string; test: (c: Channel, agentNames: string[] | undefined) => boolean }[] = [
  { key: "all", label: "All", test: () => true },
  { key: "agents", label: "Agents", test: (c, agentNames) => isAgentDm(c, agentNames) },
  { key: "people", label: "People", test: (c, agentNames) => isPeopleDm(c, agentNames) },
  { key: "groups", label: "Groups", test: (c) => c.type === "group" || c.type === "topic" },
  { key: "projects", label: "Projects", test: (c) => Boolean(c.project_id) },
];

function sortKey(c: Channel): number {
  const parsed = c.last_message_at ? Date.parse(c.last_message_at) : NaN;
  const t = Number.isNaN(parsed) ? Date.parse(c.created_at ?? "") : parsed;
  return Number.isNaN(t) ? -Infinity : t;
}

export function MobileChatList(props: MobileChatListProps) {
  const {
    sections,
    projectGroups,
    selectedChannel,
    onSelectChannel,
    unread,
    nowMs,
    formatRelativeTime,
    bus,
    busSelected,
    onSelectBusChannel,
    agentNames,
  } = props;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [busOpen, setBusOpen] = useState(false);

  const rows = useMemo(() => {
    const byId = new Map<string, Row>();
    for (const section of sections) {
      for (const channel of section.items) {
        if (!byId.has(channel.id)) byId.set(channel.id, { channel });
      }
    }
    for (const group of projectGroups) {
      for (const channel of group.channels) {
        const existing = byId.get(channel.id);
        if (existing) existing.projectName = group.name;
        else byId.set(channel.id, { channel, projectName: group.name });
      }
    }
    return [...byId.values()].sort((a, b) => sortKey(b.channel) - sortKey(a.channel));
  }, [sections, projectGroups]);

  const visibleFilters = FILTERS.filter(
    (f) => f.key === "all" || rows.some((r) => f.test(r.channel, agentNames)),
  );
  const activeFilter = FILTERS.find((f) => f.key === filter);
  const needle = query.trim().toLowerCase();
  const shown = rows.filter(
    (r) =>
      (activeFilter?.test(r.channel, agentNames) ?? true) &&
      (!needle ||
        r.channel.name.toLowerCase().includes(needle) ||
        (r.channel.lastPreview ?? "").toLowerCase().includes(needle) ||
        (r.projectName ?? "").toLowerCase().includes(needle)),
  );

  // Reset filter to "all" if the active filter has no matching rows in the current rows
  useEffect(() => {
    if (filter !== "all" && activeFilter && !rows.some((r) => activeFilter.test(r.channel, agentNames))) {
      setFilter("all");
    }
  }, [filter, rows, activeFilter, agentNames]);

  const cardStyle = {
    margin: "0 12px",
    borderRadius: 16,
    background: "var(--color-shell-surface)",
    border: "1px solid var(--color-shell-border)",
    overflow: "hidden",
  };

  const busCardStyle = {
    margin: "0 12px",
    borderRadius: 16,
    background: "var(--color-shell-surface)",
    border: "1px solid var(--color-shell-border)",
    overflow: "hidden",
    marginTop: 14,
  };

  const busHeaderStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    width: "100%",
    padding: "11px 14px",
    background: "none",
    border: "none",
    cursor: "pointer",
    color: "inherit",
    textAlign: "left",
  };

  const busTitleStyle = {
    flex: 1,
    fontSize: 15,
    fontWeight: 600,
    color: "var(--color-shell-text)",
  };

  const busCountStyle = {
    fontSize: 12,
    color: "var(--color-shell-text-tertiary)",
  };

  return (
    <div>
      <div style={{ padding: "0 12px 8px" }}>
        <input
          type="search"
          aria-label="Search chats"
          placeholder="Search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            width: "100%",
            padding: "8px 12px",
            borderRadius: 10,
            border: "1px solid var(--color-shell-border)",
            background: "var(--color-shell-surface)",
            color: "var(--color-shell-text)",
            fontSize: 15,
          }}
        />
      </div>
      <div
        role="group"
        aria-label="Filter chats"
        style={{ display: "flex", gap: 8, padding: "0 12px 10px", overflowX: "auto" }}
      >
        {visibleFilters.map((f) => (
          <button
            key={f.key}
            type="button"
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
            style={{
              padding: "5px 12px",
              borderRadius: 9999,
              fontSize: 13,
              cursor: "pointer",
              whiteSpace: "nowrap",
              border: "1px solid var(--color-shell-border)",
              background:
                filter === f.key ? "var(--color-shell-surface-active)" : "var(--color-shell-surface)",
              color: "var(--color-shell-text)",
              fontWeight: filter === f.key ? 600 : 400,
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <div
          style={{
            padding: "8px 20px",
            fontSize: 13,
            fontStyle: "italic",
            color: "var(--color-shell-text-tertiary)",
          }}
        >
          No chats match
        </div>
      ) : (
        <div style={cardStyle}>
          {shown.map(({ channel: ch, projectName }, idx) => {
            const count = unread[ch.id] ?? 0;
            const isA2A = ch.settings?.kind === "a2a";
            const agentMember =
              ch.type === "dm" ? (ch.members ?? []).find((m) => m !== "user") : undefined;
            return (
              <button
                key={ch.id}
                type="button"
                aria-label={`Chat ${ch.name}`}
                onClick={() => onSelectChannel(ch.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  width: "100%",
                  padding: "11px 14px",
                  background:
                    selectedChannel === ch.id ? "var(--color-shell-surface-active)" : "none",
                  border: "none",
                  borderBottom:
                    idx === shown.length - 1 ? "none" : "1px solid var(--color-shell-border)",
                  cursor: "pointer",
                  color: "inherit",
                  textAlign: "left",
                }}
              >
                {agentMember ? (
                  <MessageAvatar
                    size={38}
                    authorId={agentMember}
                    displayName={agentMember}
                    kind="agent"
                  />
                ) : (
                  <div
                    style={{
                      width: 38,
                      height: 38,
                      borderRadius: 11,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      background: "var(--color-shell-surface-active)",
                      color: "var(--color-shell-text-secondary)",
                      flexShrink: 0,
                    }}
                  >
                    {isA2A ? (
                      <Bot size={18} aria-hidden />
                    ) : ch.type === "group" ? (
                      <Users size={18} aria-hidden />
                    ) : ch.type === "dm-remote" ? (
                      <Globe size={18} aria-hidden />
                    ) : (
                      <Hash size={18} aria-hidden />
                    )}
                  </div>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
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
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 1 }}>
                    <span
                      style={{
                        flex: 1,
                        fontSize: 13,
                        color: "var(--color-shell-text-secondary)",
                        fontStyle: ch.lastPreview ? "normal" : "italic",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {ch.lastPreview
                        ? projectName
                          ? `${projectName} · ${ch.lastPreview}`
                          : ch.lastPreview
                        : "No messages yet"}
                    </span>
                    {count > 0 && (
                      <span
                        style={{
                          background: "var(--color-unread)",
                          color: "white",
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
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {bus.available && bus.channels.length > 0 && (
        <div style={busCardStyle}>
          <button type="button" aria-expanded={busOpen} onClick={() => setBusOpen((open) => !open)} style={busHeaderStyle}>
            <Radio size={18} aria-hidden />
            <span style={busTitleStyle}>Agent activity</span>
            <span style={busCountStyle}>{bus.channels.length} channels</span>
          </button>
          {busOpen && (
            <A2aBusSection
              channels={bus.channels}
              available={bus.available}
              loaded={bus.loaded}
              selected={busSelected}
              onSelect={onSelectBusChannel}
            />
          )}
        </div>
      )}
    </div>
  );
}