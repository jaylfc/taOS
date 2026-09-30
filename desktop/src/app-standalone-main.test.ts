import { describe, it, expect, vi, beforeEach } from "vitest";

// The standalone entry decides three things from the registry: whether the app
// renders at all, the document title, and whether the PWA manifest link is
// injected. Only an unknown id is refused; `pwa` gates only the manifest link.

const { getAppMock, renderMock } = vi.hoisted(() => ({
  getAppMock: vi.fn(),
  renderMock: vi.fn(),
}));

vi.mock("./registry/app-registry", () => ({ getApp: getAppMock }));
vi.mock("react-dom/client", () => ({
  createRoot: vi.fn(() => ({ render: renderMock })),
}));
vi.mock("./lib/auth-guard", () => ({ installAuthGuard: vi.fn() }));
vi.mock("./components/AppShell", () => ({ AppShell: () => null }));
vi.mock("./components/NotificationToast", () => ({ NotificationToasts: () => null }));
vi.mock("./AppStandalone", () => ({ AppStandalone: () => null }));
vi.mock("./stores/theme-store", () => ({
  restoreActiveTheme: vi.fn(),
  installWebkitRepaintGuards: vi.fn(),
}));

const REGISTRY: Record<string, { id: string; name: string; pwa?: boolean }> = {
  messages: { id: "messages", name: "Messages", pwa: true },
  files: { id: "files", name: "Files" },
};

async function boot(appId: string) {
  history.replaceState({}, "", `/app.html?app=${encodeURIComponent(appId)}`);
  await import("./app-standalone-main");
}

function manifestLink() {
  return document.head.querySelector('link[rel="manifest"]') as HTMLLinkElement | null;
}

function renderedText(): string {
  // The render argument is a React element tree; stringify its props to find
  // the not-available message without mounting it.
  const tree = renderMock.mock.calls[0]?.[0];
  return JSON.stringify(tree, (_k, v) => (typeof v === "function" ? undefined : v));
}

describe("app-standalone-main", () => {
  beforeEach(() => {
    vi.resetModules();
    renderMock.mockClear();
    getAppMock.mockImplementation((id: string) => REGISTRY[id]);
    document.head.innerHTML = "";
    document.body.innerHTML = '<div id="root"></div>';
    document.title = "";
  });

  it("renders a registered non-pwa app, titled by its name, with NO manifest link", async () => {
    await boot("files");
    expect(document.title).toBe("Files");
    expect(manifestLink()).toBeNull();
    expect(renderedText()).not.toContain("not available");
  });

  it("injects the manifest link for a pwa:true app", async () => {
    await boot("messages");
    expect(document.title).toBe("Messages");
    expect(manifestLink()?.getAttribute("href")).toBe("/manifest?app=messages");
    expect(renderedText()).not.toContain("not available");
  });

  it("shows the not-available message for an unknown app id, with no manifest link", async () => {
    await boot("does-not-exist");
    expect(document.title).toBe("Not installable");
    expect(manifestLink()).toBeNull();
    expect(renderedText()).toContain("not available");
  });
});
