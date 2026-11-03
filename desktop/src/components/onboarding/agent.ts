/**
 * Connecting a cloud model for the taOS Agent: save the API key, add the
 * provider, read the models it lists, then set the agent's model. Each hop
 * can fail on its own, so each failure says which one it was.
 *
 * Names follow Providers (apps/ProvidersApp.tsx): the provider is named after
 * its type and its key is the secret "provider-<name>-key", so what setup adds
 * is the same entry Providers shows and edits.
 */
import { CLOUD_PROVIDER_META } from "@/lib/cloud-providers";

/** Providers setup offers: those that need nothing but a key. */
export const SETUP_PROVIDERS = Object.entries(CLOUD_PROVIDER_META)
  .filter(([, meta]) => meta.url)
  .map(([type, meta]) => ({ type, ...meta }));

export class HopError extends Error {}

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function reason(res: Response): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return String(data?.error ?? data?.detail ?? `error ${res.status}`);
}

function label(type: string): string {
  return CLOUD_PROVIDER_META[type]?.label ?? type;
}

export function secretName(type: string): string {
  return `provider-${type}-key`;
}

/**
 * Hop 1. A key saved on an earlier try (Back, or a resume) is replaced, but
 * never one a provider is already working with: setup cannot check a new key
 * before it is live, so a typo would break that provider.
 */
export async function saveKey(type: string, key: string): Promise<void> {
  const name = secretName(type);
  try {
    let res = await fetch("/api/secrets", {
      method: "POST",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({
        name,
        value: key,
        category: "api-key",
        description: `API key for ${type} provider`,
        agents: [],
      }),
    });
    if (res.status === 409) {
      if (await keyInUse(type)) {
        throw new HopError(
          `${label(type)} is already set up and working. To use a different key, change it in Providers, or set this up later.`,
        );
      }
      res = await fetch(`/api/secrets/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: JSON_HEADERS,
        credentials: "include",
        body: JSON.stringify({ value: key }),
      });
    }
    if (!res.ok) throw new HopError(`Couldn't save the API key: ${await reason(res)}.`);
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError("Couldn't save the API key: taOS didn't answer.");
  }
}

/**
 * Hop 2. A provider that already exists is used as it is, but only when it
 * reads the key hop 1 saved: otherwise the key the user typed would go unused.
 */
export async function addProvider(type: string): Promise<void> {
  try {
    const res = await fetch("/api/providers", {
      method: "POST",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({ name: type, type, api_key_secret: secretName(type) }),
    });
    if (res.status === 409) {
      if (!(await usesSavedKey(type))) {
        throw new HopError(
          `Couldn't add ${label(type)}: an ${label(type)} provider already exists without the key you just saved. Change it in Providers, or set this up later.`,
        );
      }
      return;
    }
    if (!res.ok) throw new HopError(`Couldn't add ${label(type)}: ${await reason(res)}.`);
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError(`Couldn't add ${label(type)}: taOS didn't answer.`);
  }
}

/** A provider reads this secret and lists models it found with it. */
async function keyInUse(type: string): Promise<boolean> {
  const res = await fetch("/api/providers", { credentials: "include", headers: { Accept: "application/json" } });
  if (!res.ok) throw new HopError(`Couldn't save the API key: ${await reason(res)}.`);
  const providers = await res.json().catch(() => null);
  return Array.isArray(providers)
    ? (providers as { api_key_secret?: string; models?: unknown[]; models_seeded?: boolean }[]).some(
        (p) => p?.api_key_secret === secretName(type) && (p.models ?? []).length > 0 && !p.models_seeded,
      )
    : false;
}

/** The existing entry named `type` reads its key from the secret hop 1 saved. */
async function usesSavedKey(type: string): Promise<boolean> {
  const res = await fetch("/api/providers", { credentials: "include", headers: { Accept: "application/json" } });
  if (!res.ok) throw new HopError(`Couldn't add ${label(type)}: ${await reason(res)}.`);
  const providers = await res.json().catch(() => null);
  const entry = Array.isArray(providers)
    ? (providers as { name?: string; api_key_secret?: string }[]).find((p) => p?.name === type)
    : undefined;
  return entry?.api_key_secret === secretName(type);
}

/**
 * Hop 3. The models the provider entry lists (found when it was added). A
 * fallback list (models_seeded) means discovery with the key found nothing,
 * so it is no proof the key works.
 */
export async function providerModels(type: string): Promise<string[]> {
  let providers: unknown;
  try {
    const res = await fetch("/api/providers", { credentials: "include", headers: { Accept: "application/json" } });
    if (!res.ok) throw new HopError(`Couldn't read ${label(type)}'s models: ${await reason(res)}.`);
    providers = await res.json();
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError(`Couldn't read ${label(type)}'s models: taOS didn't answer.`);
  }
  const entry = Array.isArray(providers)
    ? (providers as { name?: string; models?: unknown[]; models_seeded?: boolean }[]).find((p) => p?.name === type)
    : undefined;
  if (entry?.models_seeded) {
    throw new HopError(`${label(type)} didn't accept the key, so it listed no models for it. Check the key, or set this up later in Providers.`);
  }
  const ids = (entry?.models ?? [])
    .map((m) => (typeof m === "string" ? m : (m as { id?: string; name?: string })?.id ?? (m as { name?: string })?.name))
    .filter((id): id is string => typeof id === "string" && id.length > 0);
  if (ids.length === 0) {
    throw new HopError(`${label(type)} didn't list any models. Check the key, or set this up later in Providers.`);
  }
  return ids;
}

/** Hop 4. */
export async function setAgentModel(model: string): Promise<void> {
  try {
    const res = await fetch("/api/taos-agent/settings", {
      method: "PATCH",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({ model }),
    });
    if (!res.ok) throw new HopError(`Couldn't give the taOS Agent this model: ${await reason(res)}.`);
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError("Couldn't give the taOS Agent this model: taOS didn't answer.");
  }
}
