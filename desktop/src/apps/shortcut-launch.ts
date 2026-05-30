/**
 * Helpers for launching terminal-kind agent shortcuts.
 *
 * The launch endpoint returns a one-shot redeem URL of the form
 * `<worker>/redeem?t=<ticket>`. Performing a GET against it sets the
 * `taos_shortcut` session cookie and 302-redirects to the real PTY endpoint
 * `/shortcut/terminal/<agent>/<idx>`.
 *
 * The PTY WebSocket must therefore be opened against that endpoint on the
 * worker origin — NOT against `/redeem`, which is a plain HTTP route. Opening a
 * WebSocket straight at `/redeem` (the previous behaviour) failed the upgrade
 * immediately and never set the cookie the PTY socket authenticates with.
 */
export interface TerminalShortcutTarget {
  /** Opaque ticket carried in the redeem URL's `t=` param. */
  ticket: string;
  /** WebSocket URL of the PTY endpoint on the worker origin. */
  wsUrl: string;
  /** The redeem URL to GET first so the session cookie is established. */
  redeemUrl: string;
}

export function deriveTerminalShortcutTarget(
  redirectUrl: string,
  agentId: string,
  idx: number,
  baseHref: string,
): TerminalShortcutTarget {
  const parsed = new URL(redirectUrl, baseHref);
  const ticket = parsed.searchParams.get("t") ?? "";
  const wsProto = parsed.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProto}//${parsed.host}/shortcut/terminal/${encodeURIComponent(agentId)}/${idx}`;
  return { ticket, wsUrl, redeemUrl: parsed.toString() };
}
