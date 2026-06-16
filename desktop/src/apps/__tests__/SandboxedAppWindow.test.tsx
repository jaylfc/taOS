// desktop/src/apps/__tests__/SandboxedAppWindow.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SandboxedAppWindow } from "../SandboxedAppWindow";

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
