import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import TerminalApp from "./TerminalApp";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  sentMessages: unknown[] = [];
  readyState = WebSocket.CONNECTING;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  send(data: unknown) {
    this.sentMessages.push(data);
  }
  close() {}
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TerminalApp shortcut mode WebSocket", () => {
  it("connects to wsUrl from shortcut prop instead of local endpoint", async () => {
    render(
      <TerminalApp
        shortcut={{ wsUrl: "wss://worker.local/shortcut/terminal/openclaw/0?ticket=tok123", ticket: "tok123" }}
      />,
    );
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain("wss://worker.local/shortcut/terminal/openclaw/0");
  });

  it("sends the ticket frame as the first message after connect", async () => {
    render(
      <TerminalApp
        shortcut={{ wsUrl: "wss://worker.local/shortcut/terminal/openclaw/0?ticket=tok123", ticket: "tok123" }}
      />,
    );
    const ws = MockWebSocket.instances[0];
    await act(async () => {
      ws.readyState = WebSocket.OPEN;
      ws.onopen?.();
    });
    expect(ws.sentMessages).toHaveLength(1);
    const firstMsg = JSON.parse(ws.sentMessages[0] as string);
    expect(firstMsg).toEqual({ type: "ticket", ticket: "tok123" });
  });
});
