import { describe, it, expect, vi, afterEach } from "vitest";
import { reportClientLog } from "./client-log";

describe("reportClientLog", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("POSTs the log to /api/client-logs", () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 201 }));
    reportClientLog("error", "boom-unique-1", { source: "Test", stack: "at x" });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/client-logs");
    expect((init as RequestInit)?.method).toBe("POST");
    const body = JSON.parse(String((init as RequestInit)?.body));
    expect(body.level).toBe("error");
    expect(body.message).toBe("boom-unique-1");
    expect(body.source).toBe("Test");
    expect(body.stack).toBe("at x");
  });

  it("never throws when fetch rejects", () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    expect(() => reportClientLog("error", "boom-unique-2")).not.toThrow();
  });

  it("drops an empty message", () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 201 }));
    reportClientLog("error", "   ".trim());
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("dedupes an identical report within the window", () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 201 }));
    reportClientLog("warn", "dupe-unique-3", { source: "S" });
    reportClientLog("warn", "dupe-unique-3", { source: "S" });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
