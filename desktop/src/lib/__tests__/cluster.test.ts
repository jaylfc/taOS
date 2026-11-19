import { describe, it, expect } from "vitest";
import { normalizeBackendName, workerStatus } from "../cluster";

describe("normalizeBackendName", () => {
  it("rewrites legacy localhost URL shape to type:port", () => {
    expect(normalizeBackendName("ollama@http://localhost:11434")).toBe("ollama:11434");
  });

  it("rewrites legacy 127.0.0.1 URL shape to type:port", () => {
    expect(normalizeBackendName("rkllama@http://127.0.0.1:8080")).toBe("rkllama:8080");
  });

  it("leaves already-new type:port format unchanged", () => {
    expect(normalizeBackendName("ollama:11434")).toBe("ollama:11434");
  });

  it("leaves unknown shapes unchanged", () => {
    expect(normalizeBackendName("backend")).toBe("backend");
    expect(normalizeBackendName("")).toBe("");
    expect(normalizeBackendName("ollama@http://10.0.0.5:11434")).toBe(
      "ollama@http://10.0.0.5:11434"
    );
  });

  it("rewrites legacy [::1] URL shape to type:port", () => {
    expect(normalizeBackendName("ollama@http://[::1]:11434")).toBe("ollama:11434");
  });

  it("rewrites legacy 0.0.0.0 URL shape to type:port", () => {
    expect(normalizeBackendName("ollama@http://0.0.0.0:11434")).toBe("ollama:11434");
  });

  it("leaves a non-loopback hostname unchanged", () => {
    expect(normalizeBackendName("ollama@http://myhost.local:11434")).toBe(
      "ollama@http://myhost.local:11434"
    );
  });

  it("leaves a URL without an explicit port unchanged (no crash)", () => {
    expect(normalizeBackendName("ollama@http://localhost")).toBe("ollama@http://localhost");
  });
});

describe("workerStatus", () => {
  const now = 1_700_000_000;

  it("falls back to worker.status when last_heartbeat is absent", () => {
    expect(workerStatus({ name: "w", url: "", status: "online" }, now)).toBe("online");
  });

  it("reports offline from worker.status when last_heartbeat is absent", () => {
    expect(workerStatus({ name: "w", url: "", status: "offline" }, now)).toBe("offline");
  });

  it("maps draining/updating/update-available to online", () => {
    expect(workerStatus({ name: "w", url: "", status: "draining" }, now)).toBe("online");
    expect(workerStatus({ name: "w", url: "", status: "updating" }, now)).toBe("online");
    expect(workerStatus({ name: "w", url: "", status: "update-available" }, now)).toBe("online");
  });

  it("returns unknown when neither last_heartbeat nor status is present", () => {
    expect(workerStatus({ name: "w", url: "" }, now)).toBe("unknown");
    expect(workerStatus({ name: "w", url: "", status: "bogus" }, now)).toBe("unknown");
  });

  it("keeps heartbeat-age logic when last_heartbeat is present", () => {
    expect(workerStatus({ name: "w", url: "", last_heartbeat: now - 10 }, now)).toBe("online");
    expect(workerStatus({ name: "w", url: "", last_heartbeat: now - 200 }, now)).toBe("stale");
    expect(workerStatus({ name: "w", url: "", last_heartbeat: now - 400 }, now)).toBe("offline");
    // last_heartbeat wins over a conflicting worker.status
    expect(
      workerStatus({ name: "w", url: "", status: "offline", last_heartbeat: now - 10 }, now)
    ).toBe("online");
  });
});
