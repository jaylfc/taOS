/**
 * The first chat: deploy the personal assistant that "About you" described,
 * then open Messages on it. The deploy makes the agent's DM itself once the
 * agent is up (routes/agents.py), so setup never creates one: a second POST
 * /api/chat/channels would leave two DMs with the same agent.
 */
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { HopError } from "./agent";

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

export const DEFAULT_ASSISTANT_NAME = "Assistant";

/** Mirrors validate_agent_name (config.py): the slug needs a letter or digit. */
export function assistantNameError(name: string): string {
  const n = name.trim();
  if (!n) return "Give your assistant a name.";
  if (n.length > 64) return "Use 64 characters or fewer.";
  if (!/[\p{L}\p{N}]/u.test(n)) return "Use at least one letter or number.";
  return "";
}

async function reason(res: Response): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return String(data?.error ?? data?.detail ?? `error ${res.status}`);
}

/** Hop 1: the agent framework this device should run. */
export async function deviceFramework(): Promise<string> {
  let res: Response;
  try {
    res = await fetch("/api/hardware", { credentials: "include" });
  } catch {
    throw new HopError("Couldn't read this device's hardware. Check the connection and try again.");
  }
  if (!res.ok) throw new HopError(`Couldn't read this device's hardware: ${await reason(res)}.`);
  const data = await res.json().catch(() => ({}));
  const framework = data?.recommended_framework;
  if (typeof framework !== "string" || !framework) {
    throw new HopError("Couldn't tell which assistant this device can run.");
  }
  return framework;
}

/**
 * Hop 2. Returns the agent's slug: the route renames it to a unique slug and
 * answers with that. The Idempotency-Key is one per visit to the step, so a
 * retry after a lost answer gets the first deploy back instead of a second agent.
 */
export async function deployAssistant(
  body: { name: string; framework: string; model: string; soul_md: string },
  idempotencyKey: string,
): Promise<string> {
  let res: Response;
  try {
    res = await fetch("/api/agents/deploy", {
      method: "POST",
      headers: { ...JSON_HEADERS, "Idempotency-Key": idempotencyKey },
      credentials: "include",
      body: JSON.stringify(body),
    });
  } catch {
    throw new HopError("Couldn't reach taOS to set up your assistant. Try again.");
  }
  if (!res.ok) throw new HopError(`Couldn't set up your assistant: ${await reason(res)}.`);
  const data = await res.json().catch(() => ({}));
  if (typeof data?.name !== "string" || !data.name) {
    throw new HopError("Couldn't set up your assistant: taOS gave no agent name.");
  }
  return data.name;
}

/**
 * Open Messages in this page with the new agent and its prompts. Props keep
 * it in-page on a handset too (shouldLaunchInShell sends a bare open to the
 * device shell, which could not be handed the agent), and the activate event
 * makes it the visible window on the mobile layout. App is already mounted
 * behind LoginGate, so neither is lost.
 */
export function openFirstChat(agent: string, prompts: string[]): void {
  const app = getApp("messages");
  const wid = useProcessStore
    .getState()
    .openWindow("messages", app?.defaultSize ?? { w: 900, h: 600 }, { firstChatAgent: agent, suggestedPrompts: prompts });
  if (wid) window.dispatchEvent(new CustomEvent("taos:activate-window", { detail: { windowId: wid } }));
}

/** The owner's full name, for a resumed setup that never saw the name screen. */
export async function ownerName(): Promise<string> {
  try {
    const res = await fetch("/auth/me", { credentials: "include" });
    if (!res.ok) return "";
    const data = await res.json();
    const name = data?.user?.full_name;
    return typeof name === "string" ? name : "";
  } catch {
    return "";
  }
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `oobe-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
