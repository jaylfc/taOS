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
  /** Free taOS username (the social/identity namespace). Not yet returned by
   *  /me; treated as absent until the backend adds it. */
  username?: string | null;
  /** Claimed taOSgo subdomains under taos.my. Not yet returned by /me. */
  subdomains?: SubdomainClaim[];
  /** @deprecated reserved taOS username alias. Kept for one release so older
   *  client builds keep rendering; prefer `username`. */
  handle?: string | null;
}

export type SubdomainStatus = "active" | "grace" | "released";

export interface SubdomainBinding {
  type: "site";
  host_id: string;
  site_ref: string;
}

export interface SubdomainClaim {
  id: string;
  account_id: string;
  name: string;
  status: SubdomainStatus;
  claimed_at?: string | null;
  lapsed_at?: string | null;
  released_at?: string | null;
  binding?: SubdomainBinding | null;
}

export interface SubdomainCheck {
  available: boolean;
  reason?: "taken" | "reserved" | "invalid" | "cooldown";
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

/** Validate a single subdomain claim record before the UI trusts it. The backend
 *  is external (taos.my); a malformed entry must not crash the render. */
function isSubdomain(x: unknown): x is SubdomainClaim {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o.id === "string" &&
    typeof o.name === "string" &&
    typeof o.status === "string" &&
    (o.status === "active" || o.status === "grace" || o.status === "released")
  );
}

/** Validate an unknown payload is a well-formed Account before the UI trusts it.
 *  The backend is external (taos.my); a malformed /me must not crash the render. */
function isAccount(x: unknown): x is Account {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  const t = o.taosgo as Record<string, unknown> | undefined;
  if (
    typeof o.user_id !== "string" ||
    typeof o.email !== "string" ||
    !t ||
    typeof t !== "object" ||
    typeof t.status !== "string"
  ) {
    return false;
  }
  if (o.username !== undefined && o.username !== null && typeof o.username !== "string") {
    return false;
  }
  if (o.subdomains !== undefined) {
    if (!Array.isArray(o.subdomains) || !o.subdomains.every(isSubdomain)) return false;
  }
  return true;
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

export function isAuthError(x: unknown): x is AuthError {
  return (x as AuthError).message !== undefined;
}

/* ------------------------------------------------------------------ */
/*  Subdomain claims (taOSgo, paid)                                   */
/*                                                                     */
/*  Same-origin proxy to taos.my (slice 3 of the account model): the   */
/*  client only ever sees /api/account/subdomains/*. Every call degrades */
/*  to an AuthError rather than throwing, matching the rest of this      */
/*  module, so the UI ships ahead of the backend.                       */
/* ------------------------------------------------------------------ */

/** Call the subdomain proxy and normalize the result the same way the auth
 *  actions do: 404/503 mean the service is not live yet, any non-ok becomes a
 *  readable message, and a bad body degrades to a generic error. */
async function subdomainRequest<T>(path: string, body?: unknown): Promise<T | AuthError> {
  let r: Response;
  try {
    r = await call(path, body);
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
    return data as T;
  } catch {
    return { message: "Unexpected response from the account service." };
  }
}

/** Advisory availability check (GET /api/account/subdomains/check?name=...). */
export const checkSubdomain = (name: string): Promise<SubdomainCheck | AuthError> =>
  subdomainRequest<SubdomainCheck>(`/subdomains/check?name=${encodeURIComponent(name)}`);

/** Claim a subdomain (POST /api/account/subdomains/claim). Returns the new claim
 *  record, or an AuthError (e.g. "taken", "reserved", "cap reached"). */
export const claimSubdomain = (name: string): Promise<SubdomainClaim | AuthError> =>
  subdomainRequest<SubdomainClaim>("/subdomains/claim", { name });

/** Release a claimed subdomain (POST /api/account/subdomains/release). */
export const releaseSubdomain = (name: string): Promise<{ released: boolean } | AuthError> =>
  subdomainRequest<{ released: boolean }>("/subdomains/release", { name });
