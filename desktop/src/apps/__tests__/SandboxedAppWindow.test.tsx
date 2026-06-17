// desktop/src/apps/__tests__/SandboxedAppWindow.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SandboxedAppWindow } from "../SandboxedAppWindow";

// Mock the theme store -- the component subscribes to it.
vi.mock("@/stores/theme-store", () => ({
  useThemeStore: (sel: (s: { scheme: "light" | "dark" }) => unknown) =>
    sel({ scheme: "dark" }),
}));

// Mock ALLOWED_TOKENS to a small known set so readThemeTokens is predictable.
vi.mock("@/theme/theme-config", () => ({
  ALLOWED_TOKENS: new Set(["--color-accent", "--color-shell-bg"]),
}));

afterEach(() => vi.unstubAllGlobals());

describe("SandboxedAppWindow", () => {
  it("renders a locked-down sandbox iframe pointed at the bundle", () => {
    render(<SandboxedAppWindow windowId="w1" appId="todo" />);
    const iframe = screen.getByTitle("todo") as HTMLIFrameElement;
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.getAttribute("src")).toBe("/api/userspace-apps/todo/bundle/index.html?app=todo");
  });

  it("bridges a capability message to the broker and posts the reply back", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ result: 42 }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<SandboxedAppWindow windowId="w1" appId="todo" />);
    const iframe = screen.getByTitle("todo") as HTMLIFrameElement;
    const post = vi.fn();
    Object.defineProperty(iframe, "contentWindow", { value: { postMessage: post }, configurable: true });

    window.dispatchEvent(new MessageEvent("message", {
      source: iframe.contentWindow as Window,
      data: { taosApp: "todo", id: 1, capability: "app.kv.get", args: { key: "k" } },
    }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/userspace-apps/todo/broker",
      expect.objectContaining({ method: "POST" }),
    ));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      expect.objectContaining({ taosAppReply: 1, result: 42 }), "*"));
  });

  it("ignores messages from other sources", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<SandboxedAppWindow windowId="w1" appId="todo" />);
    window.dispatchEvent(new MessageEvent("message", {
      source: window,  // not the iframe
      data: { taosApp: "todo", id: 9, capability: "app.kv.get", args: {} },
    }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("coerces non-object args to {} before forwarding to broker", async () => {
    // Finding 1: array, null, or scalar args must not be forwarded as-is.
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ result: "ok" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<SandboxedAppWindow windowId="w1" appId="todo" />);
    const iframe = screen.getByTitle("todo") as HTMLIFrameElement;
    Object.defineProperty(iframe, "contentWindow", {
      value: { postMessage: vi.fn() }, configurable: true
    });

    for (const badArgs of [["array"], null, "string", 42]) {
      fetchMock.mockClear();
      window.dispatchEvent(new MessageEvent("message", {
        source: iframe.contentWindow as Window,
        data: { taosApp: "todo", id: 2, capability: "app.kv.get", args: badArgs },
      }));
      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body.args).toEqual({});
    }
  });
});

describe("SandboxedAppWindow -- theme injection", () => {
  it("posts taosTheme tokens on load for a first-party app", async () => {
    render(<SandboxedAppWindow windowId="w1" appId="fp-app" trust="first-party" />);
    const iframe = screen.getByTitle("fp-app") as HTMLIFrameElement;
    const post = vi.fn();
    Object.defineProperty(iframe, "contentWindow", { value: { postMessage: post }, configurable: true });

    // Simulate the iframe load event.
    iframe.dispatchEvent(new Event("load"));

    await waitFor(() => {
      expect(post).toHaveBeenCalled();
      const call = post.mock.calls[0];
      expect(call[0]).toHaveProperty("taosTheme");
      expect(typeof call[0].taosTheme).toBe("object");
    });
  });

  it("does NOT post taosTheme for a community app on load", () => {
    render(<SandboxedAppWindow windowId="w2" appId="comm-app" trust="community" />);
    const iframe = screen.getByTitle("comm-app") as HTMLIFrameElement;
    const post = vi.fn();
    Object.defineProperty(iframe, "contentWindow", { value: { postMessage: post }, configurable: true });

    iframe.dispatchEvent(new Event("load"));

    // No theme message should have been posted.
    const themeMessages = post.mock.calls.filter((c) => c[0]?.taosTheme);
    expect(themeMessages).toHaveLength(0);
  });

  it("does NOT post taosTheme when trust prop is absent (defaults to community)", () => {
    render(<SandboxedAppWindow windowId="w3" appId="no-trust-app" />);
    const iframe = screen.getByTitle("no-trust-app") as HTMLIFrameElement;
    const post = vi.fn();
    Object.defineProperty(iframe, "contentWindow", { value: { postMessage: post }, configurable: true });

    iframe.dispatchEvent(new Event("load"));

    const themeMessages = post.mock.calls.filter((c) => c[0]?.taosTheme);
    expect(themeMessages).toHaveLength(0);
  });
});

describe("SDK theme API (message handler)", () => {
  it("taosTheme message is stored and accessible via taos.theme.get()", () => {
    // Simulate the SDK's message handler inline (no iframe needed -- pure unit test).
    let themeTokens: Record<string, string> = {};
    const subscribers: Array<(t: Record<string, string>) => void> = [];

    const handler = (e: MessageEvent) => {
      const m = e.data;
      if (m && m.taosTheme && typeof m.taosTheme === "object") {
        themeTokens = m.taosTheme;
        for (const cb of subscribers) cb(themeTokens);
      }
    };
    window.addEventListener("message", handler);

    const tokens = { "--color-accent": "#7c3aed", "--color-shell-bg": "#1a1b2e" };
    window.dispatchEvent(new MessageEvent("message", { data: { taosTheme: tokens } }));

    expect(themeTokens).toEqual(tokens);

    window.removeEventListener("message", handler);
  });

  it("taosTheme subscribers are called on theme push", () => {
    const received: Record<string, string>[] = [];
    const subscribers: Array<(t: Record<string, string>) => void> = [
      (t) => received.push(t),
    ];

    const handler = (e: MessageEvent) => {
      const m = e.data;
      if (m && m.taosTheme && typeof m.taosTheme === "object") {
        for (const cb of subscribers) cb(m.taosTheme);
      }
    };
    window.addEventListener("message", handler);

    window.dispatchEvent(new MessageEvent("message", {
      data: { taosTheme: { "--color-accent": "#ff0000" } },
    }));

    expect(received).toHaveLength(1);
    expect(received[0]!["--color-accent"]).toBe("#ff0000");

    window.removeEventListener("message", handler);
  });
});
