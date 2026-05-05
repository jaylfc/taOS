import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { openParentWs, DRIVING_DECAY_MS, type AgentWsBridgeOptions } from "./agent-ws-bridge";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";

/**
 * In the new architecture (post-Opus whole-branch review), the parent does
 * NOT open its own WebSocket. Instead, copilot.js (running in the iframe)
 * forwards server events to the parent via postMessage as
 *   { type: "taos-copilot:server-event", agentId, message }
 *
 * openParentWs registers a window message listener that filters by source
 * iframe + agentId, normalises message.event → AgentEvent.kind, and calls
 * the supplied onEvent callback.
 */

// ─── Helpers ──────────────────────────────────────────────────────────────────

interface FakeIframe {
  contentWindow: object;
}

function makeIframe(): HTMLIFrameElement {
  const fakeWin = {};
  return { contentWindow: fakeWin } as unknown as HTMLIFrameElement;
}

function makeOpts(overrides?: Partial<AgentWsBridgeOptions>): AgentWsBridgeOptions {
  return {
    windowId: "win-1",
    tabId: "tab-1",
    agentId: "agent-1",
    iframe: makeIframe(),
    onEvent: vi.fn(),
    onOpen: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
}

/** Dispatch a message event with a chosen source. JSDOM's MessageEvent
 * constructor doesn't honour the `source` option, so we override via
 * Object.defineProperty before dispatch. */
function dispatchMessageFrom(source: object | null, data: unknown): void {
  const ev = new MessageEvent("message", { data });
  Object.defineProperty(ev, "source", { value: source, configurable: true });
  window.dispatchEvent(ev);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("openParentWs (postMessage-based)", () => {
  it("isOpen is true after creation (no async ticket round-trip)", () => {
    const opts = makeOpts();
    const handle = openParentWs(opts);
    expect(handle.isOpen).toBe(true);
  });

  it("does NOT open a WebSocket (sanity: no fetch/WS APIs touched)", () => {
    // The whole point of the new design is no WebSocket on the parent.
    // If a WebSocket constructor gets called we'll see it via global mock.
    const ctor = vi.fn();
    (global as unknown as Record<string, unknown>).WebSocket = ctor;
    const opts = makeOpts();
    openParentWs(opts);
    expect(ctor).not.toHaveBeenCalled();
  });

  it("calls onEvent when iframe forwards a page-changed event", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: {
        event: "page-changed",
        url: "https://example.com/",
        title: "Example",
        timestamp: 12345,
      },
    });

    expect(opts.onEvent).toHaveBeenCalledOnce();
    const received = vi.mocked(opts.onEvent).mock.calls[0][0];
    expect(received.kind).toBe("page-changed");
    expect(received.url).toBe("https://example.com/");
    expect(received.title).toBe("Example");
    expect(received.timestamp).toBe(12345);
  });

  it("transforms url-changed and scroll kinds", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "url-changed", url: "https://a.com/" },
    });
    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "scroll" },
    });

    const calls = vi.mocked(opts.onEvent).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][0].kind).toBe("url-changed");
    expect(calls[1][0].kind).toBe("scroll");
  });

  it("rejects messages whose source is not the iframe (cross-frame protection)", () => {
    const opts = makeOpts();
    openParentWs(opts);

    // A message from a DIFFERENT window object — must be ignored
    dispatchMessageFrom({}, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "page-changed", url: "https://attacker.example/" },
    });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("rejects messages with mismatched agentId (multi-pin isolation)", () => {
    const opts = makeOpts({ agentId: "agent-1" });
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-2",
      message: { event: "page-changed", url: "https://example.com/" },
    });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("ignores messages with unknown event kinds", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "totally-unknown" },
    });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("ignores messages without the expected type field", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "something-else",
      agentId: "agent-1",
      message: { event: "page-changed" },
    });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("ignores malformed payloads (string data, missing message field)", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, "not-an-object");
    dispatchMessageFrom(opts.iframe.contentWindow, { type: "taos-copilot:server-event", agentId: "agent-1" });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("falls back to Date.now() when server event has no timestamp", () => {
    const beforeTs = Date.now();
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "scroll" },
    });

    const afterTs = Date.now();
    const received = vi.mocked(opts.onEvent).mock.calls[0][0];
    expect(received.timestamp).toBeGreaterThanOrEqual(beforeTs);
    expect(received.timestamp).toBeLessThanOrEqual(afterTs);
  });

  it("handle.close() removes the listener and fires onClose", () => {
    const opts = makeOpts();
    const handle = openParentWs(opts);

    handle.close();
    expect(handle.isOpen).toBe(false);
    expect(opts.onClose).toHaveBeenCalledOnce();

    // Subsequent messages should be ignored
    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "page-changed" },
    });
    expect(opts.onEvent).not.toHaveBeenCalled();
  });

  it("close() is idempotent — second call is a no-op", () => {
    const opts = makeOpts();
    const handle = openParentWs(opts);

    handle.close();
    handle.close();
    expect(opts.onClose).toHaveBeenCalledOnce();
  });
});

// ─── driving-state event ──────────────────────────────────────────────────────

describe("driving-state event", () => {
  beforeEach(() => {
    // Reset the store's drivingState before each test.
    useBrowserAgentStore.setState({ drivingState: {} });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("flips drivingState to driving when state=driving received", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "driving-state", state: "driving" },
    });

    const key = "win-1:tab-1:agent-1";
    expect(useBrowserAgentStore.getState().drivingState[key]).toBe("driving");
  });

  it("flips drivingState to idle when state=idle received", () => {
    const opts = makeOpts();
    // Pre-seed store with driving state.
    useBrowserAgentStore.setState({
      drivingState: { "win-1:tab-1:agent-1": "driving" },
    });
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "driving-state", state: "idle" },
    });

    const key = "win-1:tab-1:agent-1";
    expect(useBrowserAgentStore.getState().drivingState[key]).toBe("idle");
  });

  it("auto-decays back to idle after DRIVING_DECAY_MS", () => {
    vi.useFakeTimers();
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "driving-state", state: "driving" },
    });

    const key = "win-1:tab-1:agent-1";
    expect(useBrowserAgentStore.getState().drivingState[key]).toBe("driving");

    vi.advanceTimersByTime(DRIVING_DECAY_MS + 100);
    expect(useBrowserAgentStore.getState().drivingState[key]).toBe("idle");
  });

  it("rapid successive driving events reset the decay timer", () => {
    vi.useFakeTimers();
    const opts = makeOpts();
    openParentWs(opts);

    const drivingMsg = {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "driving-state", state: "driving" },
    };

    dispatchMessageFrom(opts.iframe.contentWindow, drivingMsg);
    // Advance 25 000 ms (< 30 000 ms decay)
    vi.advanceTimersByTime(25_000);
    // Re-dispatch — resets the timer
    dispatchMessageFrom(opts.iframe.contentWindow, drivingMsg);
    // Advance another 10 000 ms (35 000 total but only 10 000 since last event)
    vi.advanceTimersByTime(10_000);

    const key = "win-1:tab-1:agent-1";
    // Timer was reset at 25s, so 10s since last event < 30s → still driving
    expect(useBrowserAgentStore.getState().drivingState[key]).toBe("driving");
  });

  it("close() cancels the decay timer — no setDrivingState after close", () => {
    vi.useFakeTimers();
    const opts = makeOpts();
    const handle = openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "driving-state", state: "driving" },
    });

    handle.close();

    const key = "win-1:tab-1:agent-1";
    const stateBeforeAdvance = useBrowserAgentStore.getState().drivingState[key];

    vi.advanceTimersByTime(DRIVING_DECAY_MS + 100);

    // State should not have been changed to idle after close
    expect(useBrowserAgentStore.getState().drivingState[key]).toBe(stateBeforeAdvance);
  });

  it("does NOT call opts.onEvent for driving-state (handled separately)", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: { event: "driving-state", state: "driving" },
    });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });
});

// ─── capability-needed event ──────────────────────────────────────────────────

describe("capability-needed event", () => {
  it("dispatches taos-browser:capability-prompt with the right detail", () => {
    const opts = makeOpts();
    openParentWs(opts);

    let capturedEvent: CustomEvent | null = null;
    const handler = (e: Event) => { capturedEvent = e as CustomEvent; };
    window.addEventListener("taos-browser:capability-prompt", handler);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: {
        event: "capability-needed",
        profile_id: "profile-42",
        agent_name: "TestAgent",
        permission: "clipboard-read",
        host: "example.com",
        full_url: "https://example.com/page",
      },
    });

    window.removeEventListener("taos-browser:capability-prompt", handler);

    expect(capturedEvent).not.toBeNull();
    const detail = (capturedEvent as CustomEvent).detail;
    expect(detail.profileId).toBe("profile-42");
    expect(detail.agentId).toBe("agent-1");
    expect(detail.agentName).toBe("TestAgent");
    expect(detail.permission).toBe("clipboard-read");
    expect(detail.host).toBe("example.com");
    expect(detail.fullUrl).toBe("https://example.com/page");
  });

  it("does NOT call opts.onEvent for capability-needed (it's not an AgentEvent)", () => {
    const opts = makeOpts();
    openParentWs(opts);

    dispatchMessageFrom(opts.iframe.contentWindow, {
      type: "taos-copilot:server-event",
      agentId: "agent-1",
      message: {
        event: "capability-needed",
        permission: "camera",
        host: "example.com",
      },
    });

    expect(opts.onEvent).not.toHaveBeenCalled();
  });
});
