import { useEffect, useState } from "react";
import {
  fetchShellProfile,
  revokeShellRule,
  setShellRule,
  type ShellEffect,
  type ShellProfile,
} from "@/lib/taos-agent-shell-api";

/* ------------------------------------------------------------------ */
/*  TaosAgentShellTab                                                  */
/*  The shell commands the user has allowed or denied for the taOS    */
/*  Agent (Claude Code harness), built up as the agent asks.          */
/* ------------------------------------------------------------------ */

export function TaosAgentShellTab() {
  const [profile, setProfile] = useState<ShellProfile | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    fetchShellProfile()
      .then((p) => { setProfile(p); setLoadErr(null); })
      .catch((e: any) => setLoadErr(String(e?.message ?? e)));
  }, []);

  async function run(rule: string, action: () => Promise<ShellProfile>) {
    setBusy(rule);
    setActionErr(null);
    try {
      setProfile(await action());
    } catch (e: any) {
      setActionErr(String(e?.message ?? e));
    } finally {
      setBusy(null);
    }
  }

  if (loadErr) {
    return (
      <div className="text-xs text-red-400" role="alert">
        Failed to load shell permissions: {loadErr}
      </div>
    );
  }
  if (!profile) return <div className="text-sm opacity-60">Loading…</div>;

  function ruleList(effect: ShellEffect, label: string, rules: string[]) {
    const flip: ShellEffect = effect === "allow" ? "deny" : "allow";
    return (
      <section aria-label={label}>
        <div className="opacity-60 text-sm mb-2">{label}</div>
        {rules.length === 0 ? (
          <p className="text-xs opacity-50">None.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {rules.map((rule) => (
              <li key={rule} className="flex items-center gap-2 text-xs">
                <code className="bg-white/5 px-2 py-0.5 rounded">{rule}</code>
                <button
                  onClick={() => run(rule, () => setShellRule(rule, flip))}
                  disabled={busy === rule}
                  aria-label={`${flip === "allow" ? "Allow" : "Deny"} ${rule}`}
                  className="bg-white/10 hover:bg-white/15 px-2 py-0.5 rounded disabled:opacity-50"
                >
                  {flip === "allow" ? "Allow" : "Deny"}
                </button>
                <button
                  onClick={() => run(rule, () => revokeShellRule(rule))}
                  disabled={busy === rule}
                  aria-label={`Revoke ${rule}`}
                  className="opacity-60 hover:opacity-100 disabled:opacity-20"
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  const waiting = Object.keys(profile.asked);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">
        When the agent wants a shell command you have not decided yet, it asks you in the chat
        and in Decisions. Your answers build this list. A program on its own covers all of its
        subcommands. Revoke a rule and the agent asks again next time. <code>git push</code> and{" "}
        <code>gh</code> are always blocked.
      </p>
      {ruleList("allow", "Allowed commands", profile.allow)}
      {ruleList("deny", "Denied commands", profile.deny)}
      {waiting.length > 0 && (
        <section aria-label="Waiting for your answer">
          <div className="opacity-60 text-sm mb-2">Waiting for your answer</div>
          <ul className="flex flex-wrap gap-2">
            {waiting.map((rule) => (
              <li key={rule}>
                <code className="text-xs bg-white/5 px-2 py-0.5 rounded">{rule}</code>
              </li>
            ))}
          </ul>
        </section>
      )}
      {actionErr && <div className="text-xs text-red-400">{actionErr}</div>}
    </div>
  );
}
