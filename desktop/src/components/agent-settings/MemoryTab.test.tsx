/**
 * MemoryTab — memory_mode sent alongside memory_plugin when skipping (tsk-m23asr).
 *
 * When the plugin is switched to "none" the tab must also send
 * memory_mode: "framework" so the stored pair stays coherent.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/lib/framework-api", () => ({
  fetchFrameworkState: vi.fn(async () => ({
    framework: "hermes",
    installed: { tag: "v1", sha: "abc" },
    latest: null,
    update_available: false,
    update_status: "idle",
  })),
  fetchPermittedModels: vi.fn(async () => ({
    permitted: ["llama3", "qwen3"],
    current: "llama3",
  })),
}));

import { MemoryTab } from "./MemoryTab";

const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; vi.clearAllMocks(); });

describe("MemoryTab — plugin/memory_mode coherence", () => {
  it("sends memory_mode: framework when switching plugin to none", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => ({}),
    })) as unknown as typeof fetch;

    render(<MemoryTab agent={{ name: "alpha", memory_plugin: "taosmd" }} onUpdated={() => {}} />);

    const select = await screen.findByLabelText(/memory plugin/i);
    fireEvent.change(select, { target: { value: "none" } });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/agents/alpha/memory",
        expect.objectContaining({
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ memory_plugin: "none", memory_mode: "framework" }),
        }),
      );
    });
  });

  it("does not send memory_mode when switching back to taosmd", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => ({}),
    })) as unknown as typeof fetch;

    render(<MemoryTab agent={{ name: "alpha", memory_plugin: "none" }} onUpdated={() => {}} />);

    const select = await screen.findByLabelText(/memory plugin/i);
    fireEvent.change(select, { target: { value: "taosmd" } });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/agents/alpha/memory",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ memory_plugin: "taosmd" }),
        }),
      );
    });
  });
});
