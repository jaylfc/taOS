import { describe, it, expect } from "vitest";
import { normalizeBackendName } from "../cluster";

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
});
