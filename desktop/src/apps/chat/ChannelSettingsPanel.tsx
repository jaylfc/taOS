import { useEffect, useState } from "react";
import {
  patchChannel, addChannelMember, removeChannelMember, muteAgent, unmuteAgent,
} from "@/lib/channel-admin-api";

type Channel = {
  id: string;
  name: string;
  type: "dm" | "group" | "topic";
  topic: string;
  members: string[];
  settings: {
    response_mode?: "quiet" | "lively";
    max_hops?: number;
    cooldown_seconds?: number;
    muted?: string[];
    ephemeral_ttl_seconds?: number | null;
  };
};

type KnownAgent = { name: string };

export function ChannelSettingsPanel({
  channel, knownAgents, onClose, onChanged,
}: {
  channel: Channel;
  knownAgents: KnownAgent[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [name, setName] = useState(channel.name);
  const [topic, setTopic] = useState(channel.topic || "");
  const [mode, setMode] = useState(channel.settings.response_mode ?? "quiet");
  const [hops, setHops] = useState(channel.settings.max_hops ?? 3);
  const [cooldown, setCooldown] = useState(channel.settings.cooldown_seconds ?? 5);
  const [ephemeralTtl, setEphemeralTtl] = useState<number | null>(channel.settings.ephemeral_ttl_seconds ?? null);
  const [err, setErr] = useState<string | null>(null);

  // Keep local state in sync if the parent pushes an updated channel
  useEffect(() => {
    setName(channel.name);
    setTopic(channel.topic || "");
    setMode(channel.settings.response_mode ?? "quiet");
    setHops(channel.settings.max_hops ?? 3);
    setCooldown(channel.settings.cooldown_seconds ?? 5);
    setEphemeralTtl(channel.settings.ephemeral_ttl_seconds ?? null);
  }, [channel]);

  const apply = async (patch: Parameters<typeof patchChannel>[1], rollback: () => void) => {
    setErr(null);
    try { await patchChannel(channel.id, patch); onChanged(); }
    catch (e) { rollback(); setErr(e instanceof Error ? e.message : "failed"); }
  };

  const members = channel.members || [];
  const muted = channel.settings.muted || [];
  const candidateAdds = knownAgents
    .map((a) => a.name)
    .filter((s) => !members.includes(s));
  const candidateMutes = members.filter((m) => m !== "user" && !muted.includes(m));

  return (
    <aside
      role="complementary"
      aria-label="Channel settings"
      className="absolute top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 shadow-xl flex flex-col z-40"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <h2 className="text-sm font-semibold">Channel settings</h2>
        <button onClick={onClose} aria-label="Close" className="text-lg leading-none">×</button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-5 text-sm">
        <section aria-label="Overview" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Overview</h3>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Name</span>
            <input
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => name !== channel.name && apply({ name }, () => setName(channel.name))}
              className="bg-white/5 border border-white/10 rounded px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Topic</span>
            <textarea
              value={topic}
              maxLength={500}
              rows={3}
              onChange={(e) => setTopic(e.target.value)}
              onBlur={() => topic !== (channel.topic || "") && apply({ topic }, () => setTopic(channel.topic || ""))}
              className="bg-white/5 border border-white/10 rounded px-2 py-1.5 text-sm resize-none"
            />
          </label>
          <div className="text-[11px] text-shell-text-tertiary">
            Type: <span className="uppercase tracking-wide">{channel.type}</span>
          </div>
        </section>

        <section aria-label="Members" className="flex flex-col gap-2">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Members</h3>
          <ul className="flex flex-col gap-1">
            {members.map((m) => (
              <li key={m} className="flex items-center justify-between px-2 py-1 rounded hover:bg-white/5">
                <span>@{m}</span>
                {m !== "user" && (
                  <button
                    className="text-xs text-red-300 hover:text-red-200"
                    onClick={async () => {
                      try { await removeChannelMember(channel.id, m); onChanged(); }
                      catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
                    }}
                  >
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
          {candidateAdds.length > 0 && (
            <AddDropdown
              label="Add agent"
              options={candidateAdds}
              onPick={async (slug) => {
                try { await addChannelMember(channel.id, slug); onChanged(); }
                catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
              }}
            />
          )}
        </section>

        <section aria-label="Moderation" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Moderation</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-shell-text-secondary">Mode:</span>
            <button
              className={`px-2 py-1 rounded text-xs ${mode === "quiet" ? "bg-sky-500/30 text-sky-200" : "bg-white/5"}`}
              onClick={() => apply({ response_mode: "quiet" }, () => setMode(mode))}
            >quiet</button>
            <button
              className={`px-2 py-1 rounded text-xs ${mode === "lively" ? "bg-emerald-500/30 text-emerald-200" : "bg-white/5"}`}
              onClick={() => apply({ response_mode: "lively" }, () => setMode(mode))}
            >lively</button>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Muted</span>
            <div className="flex flex-wrap gap-1">
              {muted.map((m) => (
                <span key={m} className="inline-flex items-center gap-1 bg-white/5 rounded px-2 py-0.5 text-xs">
                  @{m}
                  <button
                    aria-label={`Unmute ${m}`}
                    onClick={async () => { try { await unmuteAgent(channel.id, m); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : "failed"); } }}
                  >×</button>
                </span>
              ))}
              {muted.length === 0 && <span className="text-[11px] text-shell-text-tertiary">none</span>}
            </div>
            {candidateMutes.length > 0 && (
              <AddDropdown
                label="Mute agent"
                options={candidateMutes}
                onPick={async (slug) => {
                  try { await muteAgent(channel.id, slug); onChanged(); }
                  catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
                }}
              />
            )}
          </div>
        </section>

        <section aria-label="Disappearing messages" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Disappearing messages</h3>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Auto-delete after</span>
            <select
              aria-label="Disappearing messages TTL"
              value={ephemeralTtl ?? ""}
              onChange={(e) => {
                const val = e.target.value === "" ? null : Number(e.target.value);
                setEphemeralTtl(val);
                apply({ ephemeral_ttl_seconds: val }, () => setEphemeralTtl(ephemeralTtl));
              }}
              className="bg-white/5 border border-white/10 rounded px-2 py-1.5 text-sm"
            >
              <option value="">Off</option>
              <option value={3600}>1 hour</option>
              <option value={86400}>1 day</option>
              <option value={604800}>1 week</option>
              <option value={2592000}>30 days</option>
            </select>
          </label>
        </section>

        <section aria-label="Advanced" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Advanced</h3>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Max hops: {hops}</span>
            <input
              type="range" min={1} max={10} value={hops}
              onChange={(e) => setHops(Number(e.target.value))}
              onMouseUp={() => apply({ max_hops: hops }, () => setHops(channel.settings.max_hops ?? 3))}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Cooldown: {cooldown}s</span>
            <input
              type="range" min={0} max={60} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              onMouseUp={() => apply({ cooldown_seconds: cooldown }, () => setCooldown(channel.settings.cooldown_seconds ?? 5))}
            />
          </label>
        </section>

        {err && (
          <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
            {err}
          </div>
        )}
      </div>
    </aside>
  );
}

function AddDropdown({
  label, options, onPick,
}: {
  label: string; options: string[]; onPick: (v: string) => void;
}) {
  return (
    <select
      aria-label={label}
      defaultValue=""
      onChange={(e) => { if (e.target.value) { onPick(e.target.value); e.target.value = ""; } }}
      className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs"
    >
      <option value="" disabled>{label}…</option>
      {options.map((s) => <option key={s} value={s}>@{s}</option>)}
    </select>
  );
}
