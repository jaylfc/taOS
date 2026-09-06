import { describe, it, expect } from "vitest";
import { deriveTerminalShortcutTarget } from "../shortcut-launch";

const BASE = "http://controller.local/";

describe("deriveTerminalShortcutTarget", () => {
  it("points the WebSocket at the PTY endpoint, not /redeem", () => {
    const t = deriveTerminalShortcutTarget(
      "http://worker.local/redeem?t=myticket42",
      "test-agent",
      1,
      BASE,
    );
    expect(t.ticket).toBe("myticket42");
    expect(t.wsUrl).toBe("ws://worker.local/shortcut/terminal/test-agent/1");
    expect(t.redeemUrl).toBe("http://worker.local/redeem?t=myticket42");
  });

  it("upgrades https redeem URLs to wss", () => {
    const t = deriveTerminalShortcutTarget(
      "https://worker.secure/redeem?t=secureticket",
      "test-agent",
      2,
      BASE,
    );
    expect(t.wsUrl).toBe("wss://worker.secure/shortcut/terminal/test-agent/2");
    expect(t.ticket).toBe("secureticket");
  });

  it("keeps the worker origin (host + port), not the SPA origin", () => {
    const t = deriveTerminalShortcutTarget(
      "http://worker.local:8443/redeem?t=abc",
      "agent",
      0,
      BASE,
    );
    expect(t.wsUrl).toBe("ws://worker.local:8443/shortcut/terminal/agent/0");
  });

  it("url-encodes agent ids with spaces or slashes", () => {
    const t = deriveTerminalShortcutTarget(
      "http://worker.local/redeem?t=x",
      "my agent/2",
      3,
      BASE,
    );
    expect(t.wsUrl).toBe(
      "ws://worker.local/shortcut/terminal/my%20agent%2F2/3",
    );
  });

  it("yields an empty ticket when t= is absent rather than throwing", () => {
    const t = deriveTerminalShortcutTarget(
      "http://worker.local/redeem",
      "agent",
      0,
      BASE,
    );
    expect(t.ticket).toBe("");
  });
});
