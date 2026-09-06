import { useState, useEffect, useCallback } from "react";
import { Radio, Plus, Trash2, MessageSquare, X } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  Input,
  Label,
} from "@/components/ui";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Channel {
  id: number | string;
  agent_name: string;
  type: string;
  enabled: boolean;
  config: Record<string, string>;
}

type ChannelType = string;

interface ChannelField {
  key: string;
  label: string;
  placeholder: string;
  type?: string;
  required?: boolean;
}

interface ChannelTypeDef {
  id: ChannelType;
  label: string;
  group: "easy" | "advanced";
  description?: string;
  fields: ChannelField[];
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const FALLBACK_CHANNEL_TYPES: ChannelTypeDef[] = [
  {
    id: "telegram",
    label: "Telegram",
    group: "easy",
    fields: [
      { key: "botToken", label: "Bot Token", placeholder: "123456:ABC-DEF..." },
    ],
  },
  {
    id: "discord",
    label: "Discord",
    group: "easy",
    fields: [
      { key: "botToken", label: "Bot Token", placeholder: "MTIz..." },
      { key: "guildId", label: "Guild ID", placeholder: "123456789" },
    ],
  },
  {
    id: "slack",
    label: "Slack",
    group: "easy",
    fields: [
      { key: "botToken", label: "Bot Token", placeholder: "xoxb-..." },
      { key: "signingSecret", label: "Signing Secret", placeholder: "abc123..." },
    ],
  },
  {
    id: "webchat",
    label: "WebChat",
    group: "easy",
    fields: [
      { key: "title", label: "Widget Title", placeholder: "Chat with us" },
      { key: "primaryColor", label: "Primary Color", placeholder: "#3b82f6" },
    ],
  },
  {
    id: "email",
    label: "Email",
    group: "advanced",
    fields: [
      { key: "imapHost", label: "IMAP Host", placeholder: "imap.example.com" },
      { key: "smtpHost", label: "SMTP Host", placeholder: "smtp.example.com" },
      { key: "username", label: "Username", placeholder: "agent@example.com" },
      { key: "password", label: "Password", placeholder: "password", type: "password" },
    ],
  },
  {
    id: "webhook",
    label: "Webhook",
    group: "advanced",
    fields: [
      { key: "url", label: "Webhook URL", placeholder: "https://example.com/hook" },
      { key: "secret", label: "Secret", placeholder: "optional signing secret" },
    ],
  },
];

const STATUS_STYLES: Record<string, string> = {
  enabled: "bg-emerald-500/20 text-emerald-400",
  disabled: "bg-zinc-500/20 text-zinc-400",
  error: "bg-red-500/20 text-red-400",
};

/* ------------------------------------------------------------------ */
/*  ChannelForm — bottom sheet on mobile, centred modal on desktop     */
/* ------------------------------------------------------------------ */

function ChannelForm({
  agents,
  channelTypes,
  onSave,
  onClose,
}: {
  agents: string[];
  channelTypes: ChannelTypeDef[];
  onSave: () => void;
  onClose: () => void;
}) {
  const isMobile = useIsMobile();

  const easyTypes = channelTypes.filter((t) => t.group === "easy");
  const advancedTypes = channelTypes.filter((t) => t.group === "advanced");

  const [formAgent, setFormAgent] = useState("");
  const [formType, setFormType] = useState<ChannelType>(channelTypes[0]?.id ?? "telegram");
  const [formConfig, setFormConfig] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const selectedTypeDef = channelTypes.find((t) => t.id === formType) ?? channelTypes[0];

  async function handleAdd() {
    if (!formAgent) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const res = await fetch("/api/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          agent_name: formAgent,
          type: formType,
          config: { ...formConfig },
        }),
      });
      if (!res.ok) {
        let msg = `Add failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        setFormError(msg);
        setSubmitting(false);
        return;
      }
      onSave();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Network error");
      setSubmitting(false);
    }
  }

  return (
    <div
      className={
        isMobile
          ? "absolute inset-0 z-50 flex items-end bg-black/50 backdrop-blur-sm"
          : "absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      }
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Add channel"
    >
      <Card
        className={
          isMobile
            ? "w-full max-h-[92%] overflow-y-auto bg-shell-surface shadow-2xl"
            : "w-full max-w-md max-h-full overflow-y-auto bg-shell-surface shadow-2xl"
        }
        style={isMobile ? { borderRadius: "20px 20px 0 0" } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        <CardContent className="p-5 space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Radio size={16} className="text-accent" />
              <h2 className="text-sm font-semibold">Add Channel</h2>
            </div>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close form" className="h-7 w-7">
              <X size={16} />
            </Button>
          </div>

          {/* Agent select */}
          <div className="space-y-1.5">
            <Label htmlFor="channel-agent">Agent</Label>
            <select
              id="channel-agent"
              value={formAgent}
              onChange={(e) => setFormAgent(e.target.value)}
              className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
            >
              <option value="">Select an agent...</option>
              {agents.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>

          {/* Channel type select */}
          <div className="space-y-1.5">
            <Label htmlFor="channel-type">Channel Type</Label>
            <select
              id="channel-type"
              value={formType}
              onChange={(e) => {
                setFormType(e.target.value as ChannelType);
                setFormConfig({});
              }}
              className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
            >
              <optgroup label="Easy Setup">
                {easyTypes.map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </optgroup>
              <optgroup label="Advanced">
                {advancedTypes.map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </optgroup>
            </select>
          </div>

          {/* Dynamic config fields */}
          {(selectedTypeDef?.fields ?? []).map((field) => (
            <div key={field.key} className="space-y-1.5">
              <Label htmlFor={`channel-${field.key}`}>{field.label}</Label>
              <Input
                id={`channel-${field.key}`}
                type={field.type ?? "text"}
                value={formConfig[field.key] ?? ""}
                onChange={(e) =>
                  setFormConfig((prev) => ({ ...prev, [field.key]: e.target.value }))
                }
                placeholder={field.placeholder}
              />
            </div>
          ))}

          {formError && (
            <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1.5">
              {formError}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-2">
            <Button
              onClick={handleAdd}
              disabled={!formAgent || submitting}
            >
              <Plus size={14} />
              {submitting ? "Adding..." : "Add Channel"}
            </Button>
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ChannelDetail — shown in detail pane                               */
/* ------------------------------------------------------------------ */

function ChannelDetail({
  channel,
  onDelete,
}: {
  channel: Channel;
  onDelete: () => void;
}) {
  const isMobile = useIsMobile();
  const statusKey = channel.enabled ? "enabled" : "disabled";

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Desktop header */}
      {!isMobile && (
        <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-white/5 shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-sm font-semibold text-shell-text truncate">{channel.agent_name}</h2>
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wide bg-white/5 text-shell-text-secondary">
                {channel.type}
              </span>
              <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${STATUS_STYLES[statusKey] ?? STATUS_STYLES.disabled}`}>
                {statusKey}
              </span>
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={onDelete}
            className="hover:bg-red-500/15 hover:text-red-300 shrink-0"
            aria-label={`Delete ${channel.type} channel for ${channel.agent_name}`}
          >
            <Trash2 size={13} />
            Delete
          </Button>
        </div>
      )}

      {/* Mobile summary + action row */}
      {isMobile && (
        <div className="shrink-0 px-4 py-3 border-b border-white/5">
          <div className="flex items-center gap-2 flex-wrap mb-3">
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wide bg-white/5 text-shell-text-secondary">
              {channel.type}
            </span>
            <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${STATUS_STYLES[statusKey] ?? STATUS_STYLES.disabled}`}>
              {statusKey}
            </span>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={onDelete}
            className="w-full hover:bg-red-500/15 hover:text-red-300"
            aria-label={`Delete ${channel.type} channel for ${channel.agent_name}`}
          >
            <Trash2 size={13} />
            Delete Channel
          </Button>
        </div>
      )}

      {/* Config body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <Card className="p-3">
          <CardContent className="p-0 space-y-2">
            <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary">Configuration</span>
            {Object.keys(channel.config).length === 0 ? (
              <p className="text-xs text-shell-text-tertiary italic">No configuration fields</p>
            ) : (
              Object.entries(channel.config).map(([k, v]) => (
                <div key={k} className="flex items-start gap-2">
                  <span className="text-[11px] text-shell-text-tertiary w-28 shrink-0 pt-0.5">{k}</span>
                  <span className="text-xs text-shell-text font-mono break-all">
                    {String(v) || <span className="italic text-shell-text-tertiary">empty</span>}
                  </span>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ChannelsApp                                                        */
/* ------------------------------------------------------------------ */

export function ChannelsApp({ windowId: _windowId }: { windowId: string }) {
  const isMobile = useIsMobile();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [channelTypes, setChannelTypes] = useState<ChannelTypeDef[]>(FALLBACK_CHANNEL_TYPES);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  // Fetch channel type schema from backend
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/channels/types", {
          headers: { Accept: "application/json" },
        });
        const ct = res.headers.get("content-type") ?? "";
        if (res.ok && ct.includes("application/json")) {
          const data = await res.json();
          if (data && typeof data === "object" && !Array.isArray(data)) {
            const defs: ChannelTypeDef[] = Object.entries(
              data as Record<string, Record<string, unknown>>
            ).map(([id, def]) => {
              const rawFields = Array.isArray(def.config_fields) ? def.config_fields : [];
              const fields: ChannelField[] = (rawFields as Record<string, unknown>[]).map((f) => ({
                key: String(f.name ?? ""),
                label: String(f.label ?? f.name ?? ""),
                placeholder: String(f.default ?? ""),
                type: typeof f.type === "string" && ["text", "number", "url", "password"].includes(f.type)
                  ? (f.type as string)
                  : "text",
                required: Boolean(f.required),
              }));
              const difficulty = String(def.difficulty ?? "easy");
              return {
                id,
                label: String(def.name ?? id),
                group: (difficulty === "advanced" ? "advanced" : "easy") as "easy" | "advanced",
                description: def.description ? String(def.description) : undefined,
                fields,
              };
            });
            if (defs.length > 0) {
              setChannelTypes(defs);
            }
          }
        }
      } catch { /* keep fallback */ }
    })();
  }, []);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await fetch("/api/channels", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setChannels(
              data.map((c: Record<string, unknown>) => ({
                id: (c.id as number | string) ?? `${c.agent_name}-${c.type}`,
                agent_name: String(c.agent_name ?? ""),
                type: String(c.type ?? "") as ChannelType,
                enabled: Boolean(c.enabled ?? true),
                config: (c.config as Record<string, string>) ?? {},
              }))
            );
            setLoading(false);
            return;
          }
        }
      }
    } catch { /* fall through */ }
    setChannels([]);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchChannels();
  }, [fetchChannels]);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/agents", {
          headers: { Accept: "application/json" },
        });
        if (res.ok) {
          const ct = res.headers.get("content-type") ?? "";
          if (ct.includes("application/json")) {
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
              setAgents(data.map((a: Record<string, unknown>) => String(a.name ?? "unknown")));
            }
          }
        }
      } catch { /* use fallback */ }
    })();
  }, []);

  async function handleDelete(ch: Channel) {
    if (!window.confirm(`Remove ${ch.type} channel for ${ch.agent_name}?`)) return;
    try {
      const res = await fetch(
        `/api/channels/${encodeURIComponent(ch.agent_name)}/${encodeURIComponent(ch.type)}`,
        { method: "DELETE", headers: { Accept: "application/json" } }
      );
      if (!res.ok) {
        let msg = `Delete failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        window.alert(msg);
        return;
      }
      setSelected(null);
      fetchChannels();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Network error");
    }
  }

  function handleFormSave() {
    setShowForm(false);
    fetchChannels();
  }

  const selectedChannel = channels.find((c) => String(c.id) === selected) ?? null;

  // Hide app-level toolbar on mobile when detail is open — MobileSplitView provides its own nav
  const showToolbar = !isMobile || selected === null;

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none relative">
      {/* Toolbar */}
      {showToolbar && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-2">
            <Radio size={18} className="text-accent" />
            <h1 className="text-sm font-semibold">Channels</h1>
            <span className="text-xs text-shell-text-tertiary">
              {channels.length} configured
            </span>
          </div>
          <Button
            size="sm"
            onClick={() => setShowForm(true)}
            aria-label="Add channel"
          >
            <Plus size={14} />
            {isMobile ? "Add" : "Add Channel"}
          </Button>
        </div>
      )}

      {/* Master-detail */}
      <MobileSplitView
        selectedId={selected}
        onBack={() => setSelected(null)}
        listTitle="Channels"
        detailTitle={selectedChannel ? `${selectedChannel.agent_name} · ${selectedChannel.type}` : ""}
        list={
          <div className={isMobile ? "py-2" : "p-3 space-y-2"} aria-label="Channel list">
            {loading ? (
              <div className="text-[11px] text-shell-text-tertiary px-4 py-6 text-center">
                Loading channels...
              </div>
            ) : channels.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 py-10 px-4 text-center text-shell-text-tertiary">
                <MessageSquare size={40} className="opacity-30" />
                <p className="text-sm">No channels configured</p>
                <Button size="sm" onClick={() => setShowForm(true)} className="mt-1">
                  <Plus size={13} />
                  Connect your first channel
                </Button>
              </div>
            ) : isMobile ? (
              /* iOS 26 grouped list */
              <div style={{ padding: "8px 0 16px" }}>
                <div
                  style={{
                    margin: "0 12px",
                    borderRadius: 16,
                    background: "rgba(255,255,255,0.05)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    overflow: "hidden",
                  }}
                >
                  {channels.map((ch, idx, arr) => {
                    const statusKey = ch.enabled ? "enabled" : "disabled";
                    return (
                      <button
                        key={String(ch.id)}
                        type="button"
                        onClick={() => setSelected(String(ch.id))}
                        aria-label={`Select ${ch.type} channel for ${ch.agent_name}`}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          width: "100%",
                          padding: "14px 16px",
                          background: "none",
                          border: "none",
                          borderBottom: idx === arr.length - 1 ? "none" : "1px solid rgba(255,255,255,0.06)",
                          cursor: "pointer",
                          color: "inherit",
                          textAlign: "left",
                        }}
                      >
                        <MessageSquare size={16} style={{ color: "rgba(255,255,255,0.35)", flexShrink: 0 }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 15, fontWeight: 600, color: "rgba(255,255,255,0.95)", marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {ch.agent_name}
                          </div>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                            <span style={{ fontSize: 11, padding: "1px 6px", borderRadius: 4, background: "rgba(255,255,255,0.08)", color: "rgba(255,255,255,0.5)", textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600 }}>
                              {ch.type}
                            </span>
                            <span className={`text-xs font-medium capitalize px-2 py-0.5 rounded-full ${STATUS_STYLES[statusKey] ?? STATUS_STYLES.disabled}`}>
                              {statusKey}
                            </span>
                          </div>
                        </div>
                        <svg width="8" height="14" viewBox="0 0 8 14" fill="none" style={{ color: "rgba(255,255,255,0.3)", flexShrink: 0 }}>
                          <path d="M1 1L7 7L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              /* Desktop list */
              <div className="space-y-1.5">
                {channels.map((ch) => {
                  const statusKey = ch.enabled ? "enabled" : "disabled";
                  return (
                    <button
                      key={String(ch.id)}
                      type="button"
                      onClick={() => setSelected(String(ch.id))}
                      aria-pressed={selected === String(ch.id)}
                      aria-label={`Select ${ch.type} channel for ${ch.agent_name}`}
                      className={`w-full text-left p-2.5 rounded-lg border transition-colors ${
                        selected === String(ch.id)
                          ? "border-accent/50 bg-accent/10"
                          : "border-white/5 bg-white/[0.02] hover:bg-white/[0.04]"
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <MessageSquare size={14} className="text-shell-text-tertiary shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className="text-[12px] font-semibold text-shell-text truncate">{ch.agent_name}</span>
                            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wide bg-white/5 text-shell-text-secondary">
                              {ch.type}
                            </span>
                          </div>
                          <p className="text-[10px] text-shell-text-tertiary truncate">
                            {Object.values(ch.config).filter(Boolean).join(" / ") || "No config"}
                          </p>
                        </div>
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${STATUS_STYLES[statusKey] ?? STATUS_STYLES.disabled}`}>
                          {statusKey}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        }
        detail={
          selectedChannel ? (
            <ChannelDetail
              channel={selectedChannel}
              onDelete={() => handleDelete(selectedChannel)}
            />
          ) : !isMobile ? (
            <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
              {loading ? "Loading..." : channels.length === 0 ? "Add a channel to get started" : "Select a channel"}
            </div>
          ) : null
        }
      />

      {/* Add Channel form modal / bottom sheet */}
      {showForm && (
        <ChannelForm
          agents={agents}
          channelTypes={channelTypes}
          onSave={handleFormSave}
          onClose={() => setShowForm(false)}
        />
      )}
    </div>
  );
}
