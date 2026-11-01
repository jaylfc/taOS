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
}

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
