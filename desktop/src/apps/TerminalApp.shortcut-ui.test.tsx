import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import TerminalApp from "./TerminalApp";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  sentMessages: unknown[] = [];
  readyState = WebSocket.CONNECTING;
  constructor(url: string) { this.url = url; MockWebSocket.instances.push(this); }
  send(d: unknown) { this.sentMessages.push(d); }
  close() {}
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("TerminalApp shortcut mode UI", () => {
  it("does not render connection mode toggle when shortcut prop is set", () => {
    render(
      <TerminalApp
        shortcut={{ wsUrl: "wss://worker.local/shortcut/terminal/openclaw/0?ticket=tok", ticket: "tok" }}
      />,
    );
    expect(screen.queryByRole("radio", { name: /local/i })).toBeNull();
    expect(screen.queryByRole("radio", { name: /ssh/i })).toBeNull();
  });

  it("does not render host/port form fields when shortcut prop is set", () => {
    render(
      <TerminalApp
        shortcut={{ wsUrl: "wss://worker.local/shortcut/terminal/openclaw/0?ticket=tok", ticket: "tok" }}
      />,
    );
    expect(screen.queryByLabelText(/host/i)).toBeNull();
    expect(screen.queryByLabelText(/port/i)).toBeNull();
  });

  it("shows a status indicator mentioning shortcut mode when shortcut prop is set", () => {
    render(
      <TerminalApp
        shortcut={{ wsUrl: "wss://worker.local/shortcut/terminal/openclaw/0?ticket=tok", ticket: "tok" }}
      />,
    );
    expect(screen.getByText(/connecting to.*shortcut/i)).toBeInTheDocument();
  });

  it("renders connection mode toggle when shortcut prop is absent", () => {
    render(<TerminalApp />);
    const localOrSsh = screen.queryByRole("radio", { name: /local/i }) ??
                       screen.queryByRole("button", { name: /local/i });
    expect(localOrSsh).toBeInTheDocument();
  });
});
