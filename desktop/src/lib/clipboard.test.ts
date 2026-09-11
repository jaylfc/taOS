import { describe, it, expect, vi, beforeEach } from "vitest";
import { copyText } from "./clipboard";

describe("copyText", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, "isSecureContext", {
      value: false,
      configurable: true,
    });
  });

  it("uses navigator.clipboard.writeText when available and returns true, even when isSecureContext is false", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    const ok = await copyText("hello");
    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("falls back to execCommand when navigator.clipboard is undefined", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
      writable: true,
    });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
      writable: true,
    });

    const ok = await copyText("hello");
    expect(ok).toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("returns false when execCommand throws, without rejecting", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(document, "execCommand", {
      value: () => {
        throw new Error("copy failed");
      },
      configurable: true,
      writable: true,
    });

    const ok = await copyText("hello");
    expect(ok).toBe(false);
  });
});
