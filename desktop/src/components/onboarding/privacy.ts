/**
 * The two things setup lets the user turn off before anything leaves the
 * device: the anonymous update ping (auto_update.py) and sharing downloaded
 * models with other installs (torrent_settings.py). Both are read from the
 * server, never assumed: a resume, or a setting changed elsewhere, must show
 * what is really on.
 *
 * Each store replaces its whole value on write, so a save sends back every
 * field it read with only the switch changed, and a setting that could not
 * be read is never written.
 */
import { CLOUD_PROVIDER_META } from "@/lib/cloud-providers";

export interface Setting {
  /** The stored value as read, sent back on save. */
  saved: Record<string, unknown>;
  enabled: boolean;
}

export interface Privacy {
  /** Null when the preference could not be read. */
  ping: Setting | null;
  /** Null when this install does not share models (no libtorrent, or no store). */
  sharing: Setting | null;
  /** Upload cap in KB/s while sharing; 0 = none. */
  uploadKbps: number;
  /** Cloud providers already added, by label. */
  providers: string[];
}

const PING = "/api/preferences/auto-update";
const SHARING = "/api/torrent/settings";
const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function getJson(url: string): Promise<{ ok: boolean; data: unknown }> {
  try {
    const res = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
    if (!res.ok) return { ok: false, data: null };
    return { ok: true, data: await res.json() };
  } catch {
    return { ok: false, data: null };
  }
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export async function loadPrivacy(): Promise<Privacy> {
  const [ping, sharing, providers] = await Promise.all([getJson(PING), getJson(SHARING), getJson("/api/providers")]);

  // A fresh install has saved nothing yet: null is a valid read, and the
  // server default (on) applies.
  let pingSetting: Setting | null = null;
  if (ping.ok && (ping.data === null || isObject(ping.data))) {
    const saved = ping.data ?? {};
    pingSetting = { saved, enabled: saved.update_ping_enabled !== false };
  }

  let sharingSetting: Setting | null = null;
  let uploadKbps = 0;
  if (sharing.ok && isObject(sharing.data) && sharing.data.libtorrent_available === true) {
    const { libtorrent_available: _, ...saved } = sharing.data;
    sharingSetting = { saved, enabled: saved.seed_enabled !== false };
    uploadKbps = typeof saved.upload_rate_limit_kbps === "number" ? saved.upload_rate_limit_kbps : 0;
  }

  const labels = providers.ok && Array.isArray(providers.data)
    ? (providers.data as { type?: string }[])
        .map((p) => (p?.type && CLOUD_PROVIDER_META[p.type]?.url ? CLOUD_PROVIDER_META[p.type]!.label : null))
        .filter((l): l is string => l !== null)
    : [];

  return { ping: pingSetting, sharing: sharingSetting, uploadKbps, providers: [...new Set(labels)] };
}

async function put(url: string, body: Record<string, unknown>, what: string): Promise<void> {
  let ok = false;
  try {
    const res = await fetch(url, {
      method: "PUT",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify(body),
    });
    ok = res.ok;
  } catch {
    /* reported below */
  }
  if (!ok) throw new Error(`Couldn't save ${what}. Try again.`);
}

/** Writes only a switch that moved. Throws with a message naming the setting. */
export async function savePrivacy(loaded: Privacy, ping: boolean, sharing: boolean): Promise<void> {
  if (loaded.ping && ping !== loaded.ping.enabled) {
    await put(PING, { ...loaded.ping.saved, update_ping_enabled: ping }, "the update ping setting");
  }
  if (loaded.sharing && sharing !== loaded.sharing.enabled) {
    await put(SHARING, { ...loaded.sharing.saved, seed_enabled: sharing }, "the model sharing setting");
  }
}

/** "5 MB/s", "512 KB/s", or null for no cap. */
export function formatRate(kbps: number): string | null {
  if (kbps <= 0) return null;
  if (kbps < 1000) return `${kbps} KB/s`;
  const mb = kbps / 1000;
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB/s`;
}
