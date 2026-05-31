import type { ComponentType } from "react";

export interface AppManifest {
  id: string;
  name: string;
  icon: string;
  category: "platform" | "os" | "streaming" | "game";
  component: () => Promise<{ default: ComponentType<{ windowId: string }> }>;
  defaultSize: { w: number; h: number };
  minSize: { w: number; h: number };
  singleton: boolean;
  pinned: boolean;
  launchpadOrder: number;
}

const apps: AppManifest[] = [
  // Platform apps
  { id: "messages", name: "Messages", icon: "message-circle", category: "platform", component: () => import("@/apps/MessagesApp").then((m) => ({ default: m.MessagesApp })), defaultSize: { w: 900, h: 600 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: true, launchpadOrder: 1 },
  { id: "projects", name: "Projects", icon: "folder-kanban", category: "platform", component: () => import("@/apps/ProjectsApp").then((m) => ({ default: m.ProjectsApp })), defaultSize: { w: 1100, h: 720 }, minSize: { w: 700, h: 500 }, singleton: true, pinned: true, launchpadOrder: 1.5 },
  { id: "agents", name: "Agents", icon: "bot", category: "platform", component: () => import("@/apps/AgentsApp").then((m) => ({ default: m.AgentsApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: true, launchpadOrder: 2 },
  { id: "files", name: "Files", icon: "folder", category: "platform", component: () => import("@/apps/FilesApp").then((m) => ({ default: m.FilesApp })), defaultSize: { w: 900, h: 550 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: true, launchpadOrder: 3 },
  { id: "store", name: "Store", icon: "shopping-bag", category: "platform", component: () => import("@/apps/StoreApp").then((m) => ({ default: m.StoreApp })), defaultSize: { w: 1000, h: 700 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: true, launchpadOrder: 4 },
  { id: "settings", name: "Settings", icon: "settings", category: "platform", component: () => import("@/apps/SettingsApp").then((m) => ({ default: m.SettingsApp })), defaultSize: { w: 800, h: 550 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: true, launchpadOrder: 5 },
  { id: "models", name: "Models", icon: "brain", category: "platform", component: () => import("@/apps/ModelsApp").then((m) => ({ default: m.ModelsApp })), defaultSize: { w: 900, h: 600 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: false, launchpadOrder: 6 },
  { id: "providers", name: "Providers", icon: "cloud", category: "platform", component: () => import("@/apps/ProvidersApp").then((m) => ({ default: m.ProvidersApp })), defaultSize: { w: 950, h: 640 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 6.5 },
  { id: "dashboard", name: "Activity", icon: "activity", category: "platform", component: () => import("@/apps/ActivityApp").then((m) => ({ default: m.ActivityApp })), defaultSize: { w: 1100, h: 720 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 7 },
  { id: "cluster", name: "Cluster", icon: "network", category: "platform", component: () => import("@/apps/ClusterApp").then((m) => ({ default: m.ClusterApp })), defaultSize: { w: 1000, h: 680 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 7.5 },
  { id: "memory", name: "Memory", icon: "database", category: "platform", component: () => import("@/apps/MemoryApp").then((m) => ({ default: m.MemoryApp })), defaultSize: { w: 850, h: 550 }, minSize: { w: 450, h: 350 }, singleton: true, pinned: false, launchpadOrder: 8 },
  { id: "mcp", name: "MCP", icon: "plug", category: "platform", component: () => import("@/apps/MCPApp").then((m) => ({ default: m.MCPApp })), defaultSize: { w: 1000, h: 680 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 9.5 },
  { id: "channels", name: "Channels", icon: "radio", category: "platform", component: () => import("@/apps/ChannelsApp").then((m) => ({ default: m.ChannelsApp })), defaultSize: { w: 800, h: 500 }, minSize: { w: 450, h: 350 }, singleton: true, pinned: false, launchpadOrder: 9 },
  { id: "secrets", name: "Secrets", icon: "key-round", category: "platform", component: () => import("@/apps/SecretsApp").then((m) => ({ default: m.SecretsApp })), defaultSize: { w: 750, h: 500 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: false, launchpadOrder: 10 },
  { id: "tasks", name: "Tasks", icon: "calendar-clock", category: "platform", component: () => import("@/apps/TasksApp").then((m) => ({ default: m.TasksApp })), defaultSize: { w: 800, h: 500 }, minSize: { w: 450, h: 350 }, singleton: true, pinned: false, launchpadOrder: 11 },
  { id: "import", name: "Import", icon: "upload", category: "platform", component: () => import("@/apps/ImportApp").then((m) => ({ default: m.ImportApp })), defaultSize: { w: 700, h: 450 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: false, launchpadOrder: 12 },
  { id: "images", name: "Images", icon: "image", category: "platform", component: () => import("@/apps/ImagesApp").then((m) => ({ default: m.ImagesApp })), defaultSize: { w: 900, h: 600 }, minSize: { w: 500, h: 400 }, singleton: true, pinned: false, launchpadOrder: 13 },
  { id: "library", name: "Library", icon: "book-open", category: "platform", component: () => import("@/apps/LibraryApp").then((m) => ({ default: m.LibraryApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: true, launchpadOrder: 13.5 },
  { id: "reddit", name: "Reddit", icon: "scroll-text", category: "platform", component: () => import("@/apps/RedditApp").then((m) => ({ default: m.RedditApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: false, launchpadOrder: 14 },
  { id: "youtube-library", name: "YouTube", icon: "play-circle", category: "platform", component: () => import("@/apps/YouTubeApp").then((m) => ({ default: m.YouTubeApp })), defaultSize: { w: 1050, h: 700 }, minSize: { w: 600, h: 450 }, singleton: true, pinned: false, launchpadOrder: 14.5 },
  { id: "github-browser", name: "GitHub", icon: "github", category: "platform", component: () => import("@/apps/GitHubApp").then((m) => ({ default: m.GitHubApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: false, launchpadOrder: 15 },
  { id: "x-monitor", name: "X", icon: "at-sign", category: "platform", component: () => import("@/apps/XApp").then((m) => ({ default: m.XApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: false, launchpadOrder: 15.5 },
  { id: "agent-browsers", name: "Browsers", icon: "globe", category: "platform", component: () => import("@/apps/AgentBrowsersApp").then((m) => ({ default: m.AgentBrowsersApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: false, launchpadOrder: 16 },

  // OS apps
  { id: "weather", name: "Weather", icon: "cloud", category: "os", component: () => import("@/apps/WeatherApp").then((m) => ({ default: m.WeatherApp })), defaultSize: { w: 800, h: 600 }, minSize: { w: 400, h: 400 }, singleton: true, pinned: false, launchpadOrder: 19 },
  { id: "calculator", name: "Calculator", icon: "calculator", category: "os", component: () => import("@/apps/CalculatorApp").then((m) => ({ default: m.CalculatorApp })), defaultSize: { w: 320, h: 480 }, minSize: { w: 280, h: 400 }, singleton: true, pinned: false, launchpadOrder: 20 },
  { id: "calendar", name: "Calendar", icon: "calendar", category: "os", component: () => import("@/apps/CalendarApp").then((m) => ({ default: m.CalendarApp })), defaultSize: { w: 900, h: 600 }, minSize: { w: 600, h: 400 }, singleton: true, pinned: false, launchpadOrder: 21 },
  { id: "contacts", name: "Contacts", icon: "contact", category: "os", component: () => import("@/apps/ContactsApp").then((m) => ({ default: m.ContactsApp })), defaultSize: { w: 700, h: 500 }, minSize: { w: 400, h: 300 }, singleton: true, pinned: false, launchpadOrder: 22 },
  { id: "browser", name: "Browser", icon: "globe", category: "os", component: () => import("@/apps/BrowserApp").then((m) => ({ default: m.BrowserApp })), defaultSize: { w: 1024, h: 700 }, minSize: { w: 600, h: 400 }, singleton: false, pinned: false, launchpadOrder: 23 },
  { id: "media-player", name: "Media Player", icon: "play-circle", category: "os", component: () => import("@/apps/MediaPlayerApp").then((m) => ({ default: m.MediaPlayerApp })), defaultSize: { w: 800, h: 500 }, minSize: { w: 400, h: 300 }, singleton: false, pinned: false, launchpadOrder: 24 },
  { id: "text-editor", name: "Text Editor", icon: "file-text", category: "os", component: () => import("@/apps/TextEditorApp").then((m) => ({ default: m.TextEditorApp })), defaultSize: { w: 800, h: 550 }, minSize: { w: 400, h: 300 }, singleton: false, pinned: false, launchpadOrder: 25 },
  { id: "image-viewer", name: "Image Viewer", icon: "eye", category: "os", component: () => import("@/apps/ImageViewerApp").then((m) => ({ default: m.ImageViewerApp })), defaultSize: { w: 800, h: 600 }, minSize: { w: 400, h: 300 }, singleton: false, pinned: false, launchpadOrder: 26 },
  { id: "terminal", name: "Terminal", icon: "terminal", category: "os", component: () => import("@/apps/TerminalApp").then((m) => ({ default: m.TerminalApp })), defaultSize: { w: 800, h: 500 }, minSize: { w: 400, h: 250 }, singleton: false, pinned: false, launchpadOrder: 27 },

  // Games
  { id: "chess", name: "Chess", icon: "crown", category: "game", component: () => import("@/apps/ChessApp").then((m) => ({ default: m.ChessApp })), defaultSize: { w: 700, h: 700 }, minSize: { w: 500, h: 500 }, singleton: true, pinned: false, launchpadOrder: 40 },
  { id: "wordle", name: "Wordle", icon: "spell-check", category: "game", component: () => import("@/apps/WordleApp").then((m) => ({ default: m.WordleApp })), defaultSize: { w: 500, h: 650 }, minSize: { w: 400, h: 550 }, singleton: true, pinned: false, launchpadOrder: 41 },
  { id: "crosswords", name: "Crosswords", icon: "grid-3x3", category: "game", component: () => import("@/apps/CrosswordsApp").then((m) => ({ default: m.CrosswordsApp })), defaultSize: { w: 700, h: 600 }, minSize: { w: 500, h: 450 }, singleton: true, pinned: false, launchpadOrder: 42 },
];

export function getApp(id: string): AppManifest | undefined {
  return apps.find((a) => a.id === id);
}

export function getAppsByCategory(category: AppManifest["category"]): AppManifest[] {
  return apps.filter((a) => a.category === category);
}

export function getAllApps(): AppManifest[] {
  return [...apps].sort((a, b) => a.launchpadOrder - b.launchpadOrder);
}

const prefetched = new Set<string>();
// Tracks apps that failed prefetch; not retried to avoid repeated errors on every hover.
const prefetchFailed = new Set<string>();

/**
 * Warm the dynamic-import chunk for an app so a later cold-open feels instant.
 *
 * Best-effort and memoized: each app is only prefetched once per session and
 * errors are swallowed (a failed prefetch must never affect the UI). Works for
 * any registered manifest, including service/userspace apps (`service:*`).
 */
export function prefetchApp(appId: string): void {
  if (prefetched.has(appId) || prefetchFailed.has(appId)) return;
  const app = getApp(appId);
  if (!app) return;
  prefetched.add(appId);
  try {
    void Promise.resolve(app.component()).catch((err) => {
      console.warn("Failed to prefetch app:", appId, err);
      prefetched.delete(appId);
      prefetchFailed.add(appId);
    });
  } catch (err) {
    console.warn("Failed to prefetch app:", appId, err);
    prefetched.delete(appId);
    prefetchFailed.add(appId);
  }
}

/**
 * Register or return a dynamic app manifest for an installed service.
 *
 * Each installed service gets its own appId of the form `service:{app_id}`
 * so that multiple services can be open simultaneously as independent windows.
 * The manifest is registered lazily on first call and persists for the session.
 */
export function getOrRegisterServiceApp(
  appId: string,
  displayName: string,
): AppManifest {
  const dynId = `service:${appId}`;
  const existing = apps.find((a) => a.id === dynId);
  if (existing) return existing;

  const manifest: AppManifest = {
    id: dynId,
    name: displayName,
    icon: "layout-grid",
    category: "platform",
    component: () =>
      import("@/apps/ServiceAppWindow").then((m) => ({ default: m.ServiceAppWindow })),
    defaultSize: { w: 1100, h: 750 },
    minSize: { w: 600, h: 400 },
    singleton: true,
    pinned: false,
    launchpadOrder: 999,
  };
  apps.push(manifest);
  return manifest;
}
