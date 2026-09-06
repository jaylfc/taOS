const KEY_PREFIX = "taos.projects.";
const KEY_SUFFIX = ".lastChannel";

export function projectChannelStorageKey(projectId: string): string {
  return `${KEY_PREFIX}${projectId}${KEY_SUFFIX}`;
}

export function readLastChannel(projectId: string): string | null {
  try {
    return window.localStorage.getItem(projectChannelStorageKey(projectId));
  } catch {
    return null;
  }
}

export function writeLastChannel(projectId: string, channelId: string): void {
  try {
    window.localStorage.setItem(projectChannelStorageKey(projectId), channelId);
  } catch {
    /* localStorage unavailable — ignore */
  }
}

export function findA2aChannelId(channels: Array<{ id: string; settings?: { kind?: string } }>): string | null {
  for (const c of channels) {
    if (c.settings?.kind === "a2a") return c.id;
  }
  return null;
}
