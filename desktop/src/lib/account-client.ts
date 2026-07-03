/**
 * Client for the taOS account / identity service (taOSgo P1).
 *
 * Calls are same-origin against the host at /api/account/*, which the controller
 * proxies to taos.my. Same-origin keeps the taos.my base URL server-side and
 * avoids CORS, and lets the host attach host-linking context later.
 *
 * The backend proxy may not exist yet; every call degrades to a clear state
 * (signed-out / unavailable) rather than throwing, so the UI ships ahead of it.
 */

export type TaosgoStatus = "none" | "trialing" | "active" | "past_due";

export interface TaosgoEntitlement {
  status: TaosgoStatus;
  trial_ends_at?: string | null;
  current_period_end?: string | null;
}

export interface Account {
  user_id: string;
  email: string;
  taosgo: TaosgoEntitlement;
  /** Reserved taOS username (e.g. for a future website/blog/portfolio). Not
   *  yet returned by /me; treated as absent until the backend adds it. */
  handle?: string | null;
}

export type AccountState =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "signed-in"; account: Account }
  | { kind: "unavailable" };

export interface AuthError {
  message: string;
}

const BASE = "/api/account";

/** Validate an unknown payload is a well-formed Account before the UI trusts it.
 *  The backend is external (taos.my); a malformed /me must not crash the render. */
function isAccount(x: unknown): x is Account {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  const t = o.taosgo as Record<string, unknown> | undefined;
  return (
    typeof o.user_id === "string" &&
    typeof o.email === "string" &&
    !!t &&
    typeof t === "object" &&
    typeof t.status === "string"
  );
}

async function call(path: string, body?: unknown): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    method: body !== undefined ? "POST" : "GET",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
}

export async function fetchAccount(): Promise<AccountState> {
  let r: Response;
  try {
    r = await call("/me");
  } catch {
    return { kind: "unavailable" };
  }
  if (r.status === 401) return { kind: "signed-out" };
  if (!r.ok) return { kind: "unavailable" };
  try {
    const data: unknown = await r.json();
    return isAccount(data)
      ? { kind: "signed-in", account: data }
      : { kind: "unavailable" };
  } catch {
    return { kind: "unavailable" };
  }
}

async function authAction(
  path: string,
  email: string,
  password: string,
): Promise<Account | AuthError> {
  let r: Response;
  try {
    r = await call(path, { email, password });
  } catch {
    return { message: "Could not reach the account service. Check your connection." };
  }
  if (r.status === 404 || r.status === 503) {
    return { message: "The account service is not available yet." };
  }
  if (!r.ok) {
    let msg = `Request failed (${r.status}).`;
    try {
      const d = (await r.json()) as { error?: string; detail?: string };
      if (d?.error || d?.detail) msg = String(d.error || d.detail);
    } catch {
      /* keep the status-code default */
    }
    return { message: msg };
  }
  try {
    const data: unknown = await r.json();
    return isAccount(data)
      ? data
      : { message: "Unexpected response from the account service." };
  } catch {
    return { message: "Unexpected response from the account service." };
  }
}

export const login = (email: string, password: string) =>
  authAction("/login", email, password);

export const register = (email: string, password: string) =>
  authAction("/register", email, password);

export async function logout(): Promise<void> {
  try {
    await call("/logout", {});
  } catch {
    /* signing out client-side is enough even if the call fails */
  }
}

export function isAuthError(x: Account | AuthError): x is AuthError {
  return (x as AuthError).message !== undefined;
}
