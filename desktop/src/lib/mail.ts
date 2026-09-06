import { withCsrf } from "./csrf";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface MailAccount {
  id: string;
  display_name: string;
  email_address: string;
  imap_host: string;
  imap_port: number;
  imap_security: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: string;
  username: string;
  created_at: number;
  updated_at: number;
}

export interface NewAccount {
  display_name: string;
  email_address: string;
  imap_host: string;
  imap_port: number;
  imap_security: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: string;
  username: string;
  password: string;
}

export interface MailEnvelope {
  uid: string;
  from_name: string;
  from_addr: string;
  to: string;
  subject: string;
  date: string;
  snippet: string;
  unread: boolean;
  flagged: boolean;
  has_attachment: boolean;
}

export interface MailAttachment {
  filename: string;
  content_type: string;
  size: number;
}

export interface MailDetail {
  uid: string;
  from_name: string;
  from_addr: string;
  to: string;
  cc: string;
  subject: string;
  date: string;
  body_text: string;
  body_html: string;
  attachments: MailAttachment[];
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function ensureOk(r: Response): Promise<void> {
  if (r.ok) return;
  let body: { error?: string } | null = null;
  try {
    body = await r.json();
  } catch {
    /* ignore */
  }
  throw new Error(body?.error || `HTTP ${r.status}`);
}

/* ------------------------------------------------------------------ */
/*  Account CRUD                                                       */
/* ------------------------------------------------------------------ */

export async function fetchAccounts(): Promise<MailAccount[]> {
  const r = await fetch("/api/mail/accounts", {
    headers: { Accept: "application/json" },
  });
  await ensureOk(r);
  return r.json();
}

export async function addAccount(account: NewAccount): Promise<MailAccount> {
  const r = await fetch(
    "/api/mail/accounts",
    withCsrf({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(account),
    }),
  );
  await ensureOk(r);
  return r.json();
}

export async function deleteAccount(accountId: string): Promise<void> {
  const r = await fetch(
    `/api/mail/accounts/${encodeURIComponent(accountId)}`,
    withCsrf({ method: "DELETE" }),
  );
  await ensureOk(r);
}

/* ------------------------------------------------------------------ */
/*  Folders + messages                                                */
/* ------------------------------------------------------------------ */

export async function fetchFolders(accountId: string): Promise<string[]> {
  const r = await fetch(
    `/api/mail/accounts/${encodeURIComponent(accountId)}/folders`,
    { headers: { Accept: "application/json" } },
  );
  await ensureOk(r);
  const data = await r.json();
  return Array.isArray(data?.folders) ? data.folders : [];
}

export async function fetchMessages(
  accountId: string,
  folder: string,
  limit = 50,
): Promise<MailEnvelope[]> {
  const params = new URLSearchParams({ folder, limit: String(limit) });
  const r = await fetch(
    `/api/mail/accounts/${encodeURIComponent(accountId)}/messages?${params}`,
    { headers: { Accept: "application/json" } },
  );
  await ensureOk(r);
  const data = await r.json();
  return Array.isArray(data?.messages) ? data.messages : [];
}

export async function fetchMessage(
  accountId: string,
  uid: string,
  folder: string,
): Promise<MailDetail> {
  const params = new URLSearchParams({ folder });
  const r = await fetch(
    `/api/mail/accounts/${encodeURIComponent(accountId)}/messages/${encodeURIComponent(uid)}?${params}`,
    { headers: { Accept: "application/json" } },
  );
  await ensureOk(r);
  return r.json();
}

export async function sendMessage(
  accountId: string,
  payload: { to: string; subject: string; body: string; cc?: string },
): Promise<void> {
  const r = await fetch(
    `/api/mail/accounts/${encodeURIComponent(accountId)}/send`,
    withCsrf({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
  await ensureOk(r);
}
