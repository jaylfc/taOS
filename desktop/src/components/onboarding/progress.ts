/**
 * Where first-run setup has got to, so a reload (or a phone that reboots
 * mid-setup) resumes at the same screen instead of dropping the user on a
 * half-configured desktop.
 *
 * The server copy lives in the "onboarding" preference namespace, which is
 * admin-only to write, so it exists only after /auth/setup has signed this
 * browser in as the owner. Steps before that have nothing to resume: no
 * account exists yet, and the setup screen starts over by design.
 */

export interface OnboardingProgress {
  /** True from account creation until the last step is done. */
  in_progress: boolean;
  /** The step to show on resume. */
  step: string;
  completed: string[];
  /** Steps the user chose to set up later; Setup checklist offers them. */
  skipped: string[];
  /** "About you" answers, kept here so one writer owns the whole blob (PUT replaces it). */
  about?: AboutYou;
  /** The on-device model taken at the model step; absent when set up later. */
  model?: OnDeviceChoice;
  /** The taOS Agent's model id, when one was set at the agent step. */
  agent_model?: string;
  /** The assistant the first chat deployed (its agent slug). */
  first_chat?: { agent: string };
}

import { parseAbout, type AboutYou } from "./about";
import { parseChoice, type OnDeviceChoice } from "./model";

const ENDPOINT = "/api/preferences/onboarding";

/** Null when nothing is saved or the read fails: callers treat that as "not in setup". */
export async function loadProgress(): Promise<OnboardingProgress | null> {
  try {
    const res = await fetch(ENDPOINT, { credentials: "include" });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data || typeof data !== "object" || typeof data.step !== "string") return null;
    return {
      in_progress: data.in_progress === true,
      step: data.step,
      completed: Array.isArray(data.completed) ? data.completed : [],
      skipped: Array.isArray(data.skipped) ? data.skipped : [],
      about: parseAbout(data.about),
      model: parseChoice(data.model),
      agent_model: typeof data.agent_model === "string" ? data.agent_model : undefined,
      first_chat:
        data.first_chat && typeof data.first_chat.agent === "string" && data.first_chat.agent
          ? { agent: data.first_chat.agent }
          : undefined,
    };
  } catch {
    return null;
  }
}

/**
 * Resolves whether or not the write succeeded. Await it only where a later
 * read depends on it (finishing setup, before LoginGate re-reads progress).
 */
export async function saveProgress(progress: OnboardingProgress): Promise<void> {
  try {
    await fetch(ENDPOINT, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(progress),
    });
  } catch {
    /* resume is a convenience; setup itself must not fail on it */
  }
}
