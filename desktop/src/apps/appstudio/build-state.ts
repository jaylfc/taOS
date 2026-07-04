/** Shared prompt seeding between TemplatesView and BuildView, and the
 *  hand-off of the most recently built app from BuildView to PublishView.
 *  AppStudioApp only ever mounts one view at a time (see AppStudioApp.tsx),
 *  so a plain module-level value -- not React state -- is what survives the
 *  switch between tabs. */

export const PROMPT_SEEDED_EVENT = "appstudio:prompt-seeded";
export const SHOW_BUILD_VIEW_EVENT = "appstudio:show-build";
export const BUILD_SESSION_CHANGED_EVENT = "appstudio:build-session-changed";

let pendingPrompt: string | null = null;

export function seedBuildPrompt(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  pendingPrompt = trimmed;
  window.dispatchEvent(new CustomEvent(PROMPT_SEEDED_EVENT, { detail: trimmed }));
  window.dispatchEvent(new CustomEvent(SHOW_BUILD_VIEW_EVENT));
}

export function takePendingPrompt(): string | null {
  const prompt = pendingPrompt;
  pendingPrompt = null;
  return prompt;
}

/** The app most recently built (generated, analyzed clean, and installed) by
 *  BuildView -- the source PublishView shows and re-shares. */
export interface BuildSession {
  name: string;
  files: Record<string, string>;
  appId: string;
}

let buildSession: BuildSession | null = null;

export function setBuildSession(session: BuildSession | null): void {
  buildSession = session;
  window.dispatchEvent(new CustomEvent(BUILD_SESSION_CHANGED_EVENT));
}

export function getBuildSession(): BuildSession | null {
  return buildSession;
}