import { describe, it, expect, beforeEach, vi } from "vitest";
import type { AgentWsBridgeOptions } from "./agent-ws-bridge";

// vi.mock must be at module top-level (hoisted)
vi.mock("@/lib/browser-agent-api", () => ({
  mintCopilotTicket: vi.fn(),
}));

// Import after mock declaration
import { openParentWs } from "./agent-ws-bridge";
import * as browserAgentApi from "@/lib/browser-agent-api";

// ─── Minimal WebSocket mock ───────────────────────────────────────────────────

interface MockWsInstance {
  url: string;
  readyState: number;
  listeners: Record<string, ((payload: unknown) => void)[]>;
  addEventListener(type: string, fn: (payload: unknown) => void): void;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  /** Trigger registered listeners for a given event type. */
  fire(type: string, payload?: unknown): void;
}

let mockWsInstances: MockWsInstance[];
let MockWebSocket: ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockWsInstances = [];

  MockWebSocket = vi.fn().mockImplementation(function (this: MockWsInstance, url: string) {
    this.url = url;
    this.readyState = 0; // CONNECTING
    this.listeners = {};
    this.addEventListener = function (type: string, fn: (payload: unknown) => void) {
      if (!this.listeners[type]) this.listeners[type] = [];
      this.listeners[type].push(fn);
    };
    this.send = vi.fn();
    this.close = vi.fn().mockImplementation(function (this: MockWsInstance) {
      this.readyState = 3; // CLOSED
      this.fire("close");
    });
    this.fire = function (type: string, payload?: unknown) {
      (this.listeners[type] ?? []).forEach((fn) => fn(payload ?? {}));
    };
    mockWsInstances.push(this);
  });

  (global as unknown as Record<string, unknown>).WebSocket = MockWebSocket;

  // Reset to the default "success" implementation before each test
  vi.mocked(browserAgentApi.mintCopilotTicket).mockResolvedValue({
    ticket: "fake-token",
    ttl_seconds: 60,
  });
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeOpts(overrides?: Partial<AgentWsBridgeOptions>): AgentWsBridgeOptions {
  return {
    windowId: "win-1",
    tabId: "tab-1",
    agentId: "agent-1",
    profileId: "profile-1",
    onEvent: vi.fn(),
    onOpen: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("openParentWs", () => {
  it("opens a WebSocket with the ticket in the URL", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    expect(MockWebSocket).toHaveBeenCalledOnce();
    const [url] = MockWebSocket.mock.calls[0] as [string];
    expect(url).toContain("/api/desktop/browser/copilot");
    expect(url).toContain("ticket=fake-token");
    expect(url).toMatch(/^ws(s)?:\/\//);
  });

  it("calls onOpen when the WS open event fires", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    const ws = mockWsInstances[0];
    expect(opts.onOpen).not.toHaveBeenCalled();

    ws.fire("open");
    expect(opts.onOpen).toHaveBeenCalledOnce();
  });

  it("isOpen is true after onOpen fires, false before", async () => {
    const opts = makeOpts();
    const handle = await openParentWs(opts);

    expect(handle.isOpen).toBe(false);
    mockWsInstances[0].fire("open");
    expect(handle.isOpen).toBe(true);
  });

  it("calls onEvent with transformed AgentEvent when server sends { event: 'page-changed' }", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    const ws = mockWsInstances[0];
    ws.fire("message", {
      data: JSON.stringify({
        event: "page-changed",
        url: "https://example.com/",
        title: "Example",
        timestamp: 12345,
      }),
    });

    expect(opts.onEvent).toHaveBeenCalledOnce();
    const received = vi.mocked(opts.onEvent).mock.calls[0][0];
    expect(received.kind).toBe("page-changed");
    expect(received.url).toBe("https://example.com/");
    expect(received.title).toBe("Example");
    expect(received.timestamp).toBe(12345);
    // Server's `event` field should not appear in the normalised event
    expect((received as Record<string, unknown>).event).toBeUndefined();
  });

  it("transforms { event: 'url-changed' } and { event: 'scroll' } correctly", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    const ws = mockWsInstances[0];

    ws.fire("message", { data: JSON.stringify({ event: "url-changed", url: "https://a.com/" }) });
    ws.fire("message", { data: JSON.stringify({ event: "scroll" }) });

    const calls = vi.mocked(opts.onEvent).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][0].kind).toBe("url-changed");
    expect(calls[1][0].kind).toBe("scroll");
  });

  it("ignores messages with unknown event kinds", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    const ws = mockWsInstances[0];
    ws.fire("message", { data: JSON.stringify({ event: "totally-unknown-event" }) });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("ignores malformed messages (non-JSON, no event field)", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    const ws = mockWsInstances[0];
    ws.fire("message", { data: "not-json" });
    ws.fire("message", { data: JSON.stringify({ noEventField: true }) });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("calls onClose when WS close fires", async () => {
    const opts = makeOpts();
    await openParentWs(opts);

    expect(opts.onClose).not.toHaveBeenCalled();
    mockWsInstances[0].fire("close");
    expect(opts.onClose).toHaveBeenCalledOnce();
  });

  it("sets isOpen to false after close fires", async () => {
    const opts = makeOpts();
    const handle = await openParentWs(opts);

    mockWsInstances[0].fire("open");
    expect(handle.isOpen).toBe(true);

    mockWsInstances[0].fire("close");
    expect(handle.isOpen).toBe(false);
  });

  it("handle.close() shuts the underlying WebSocket", async () => {
    const opts = makeOpts();
    const handle = await openParentWs(opts);

    handle.close();
    expect(mockWsInstances[0].close).toHaveBeenCalled();
  });

  it("uses missing timestamp → Date.now() fallback when timestamp absent from server event", async () => {
    const beforeTs = Date.now();
    const opts = makeOpts();
    await openParentWs(opts);

    mockWsInstances[0].fire("message", {
      data: JSON.stringify({ event: "scroll" }),
    });

    const afterTs = Date.now();
    const received = vi.mocked(opts.onEvent).mock.calls[0][0];
    expect(received.timestamp).toBeGreaterThanOrEqual(beforeTs);
    expect(received.timestamp).toBeLessThanOrEqual(afterTs);
  });

  it("if mintCopilotTicket returns null, calls onClose immediately and isOpen stays false", async () => {
    vi.mocked(browserAgentApi.mintCopilotTicket).mockResolvedValue(null);

    const opts = makeOpts();
    const handle = await openParentWs(opts);

    expect(MockWebSocket).not.toHaveBeenCalled();
    expect(opts.onClose).toHaveBeenCalledOnce();
    expect(handle.isOpen).toBe(false);
  });
});
