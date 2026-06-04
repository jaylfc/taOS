/**
 * Tests for the system-wide memory-model control (MemoryModelSection inside
 * MemorySettings). Tests render MemorySettings directly to avoid tab-visibility
 * complexity in jsdom, and verify that:
 * - GET /api/memory/model drives the rendered UI
 * - Selecting a model issues PUT /api/memory/model
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";

// Stub heavy deps that MemorySettings pulls in transitively
vi.mock("@/components/ModelPickerFlow", () => ({ ModelPickerFlow: () => null }));
vi.mock("@/components/ModelPickerModal", () => ({
  ModelPickerModal: ({
    open,
    onSelect,
    title,
  }: {
    open: boolean;
    onSelect: (id: string) => void;
    title?: string;
  }) =>
    open ? (
      <div data-testid="model-picker-modal" aria-label={title}>
        <button
          data-testid="picker-select-model"
          onClick={() => onSelect("ollama:qwen3:4b")}
        >
          Pick ollama:qwen3:4b
        </button>
      </div>
    ) : null,
}));
vi.mock("@/components/memory/SchemaFormRenderer", () => ({
  SchemaFormRenderer: () => null,
}));

import { MemorySettings } from "@/components/memory/MemorySettings";

function makeFetch(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);

    if (u === "/api/memory/model" && (!init || !init.method || init.method.toUpperCase() === "GET")) {
      const v = overrides["GET /api/memory/model"] ?? { model: "ollama:qwen3:4b", supported: true };
      return {
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => v,
      };
    }
    if (u === "/api/memory/model" && init?.method?.toUpperCase() === "PUT") {
      const v = overrides["PUT /api/memory/model"] ?? { model: "ollama:qwen3:4b" };
      return {
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => v,
      };
    }
    if (u.includes("/api/memory/backend/settings-schema")) {
      return { ok: true, headers: { get: () => "application/json" }, json: async () => ({}) };
    }
    if (u.includes("/api/memory/settings")) {
      return { ok: true, headers: { get: () => "application/json" }, json: async () => ({}) };
    }
    if (u.includes("/api/providers/models")) {
      return {
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => ({ data: [{ id: "ollama:qwen3:4b" }] }),
      };
    }
    return { ok: true, headers: { get: () => "application/json" }, json: async () => ({}) };
  });
}

describe("MemorySettings — memory model section", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders current model from GET /api/memory/model", async () => {
    global.fetch = makeFetch({ "GET /api/memory/model": { model: "ollama:qwen3:4b", supported: true } }) as typeof fetch;

    render(<MemorySettings />);

    await waitFor(() => {
      expect(screen.getByText("ollama:qwen3:4b")).toBeDefined();
    });
  });

  it("shows Built-in default when model is null", async () => {
    global.fetch = makeFetch({ "GET /api/memory/model": { model: null, supported: true } }) as typeof fetch;

    render(<MemorySettings />);

    await waitFor(() => {
      expect(screen.getByText("Built-in default")).toBeDefined();
    });
  });

  it("shows unsupported note when supported=false", async () => {
    global.fetch = makeFetch({ "GET /api/memory/model": { model: null, supported: false } }) as typeof fetch;

    render(<MemorySettings />);

    await waitFor(() => {
      expect(screen.getByLabelText("Memory model not supported")).toBeDefined();
    });
  });

  it("PUT /api/memory/model is called when a model is selected via picker", async () => {
    const fetchMock = makeFetch({
      "GET /api/memory/model": { model: null, supported: true },
      "PUT /api/memory/model": { model: "ollama:qwen3:4b" },
    });
    global.fetch = fetchMock as typeof fetch;

    render(<MemorySettings />);

    const changeBtn = await screen.findByRole("button", { name: /change memory model/i });
    fireEvent.click(changeBtn);

    const pickBtn = await screen.findByTestId("picker-select-model");
    fireEvent.click(pickBtn);

    await waitFor(() => {
      const putCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([u, init]) => String(u) === "/api/memory/model" && init?.method?.toUpperCase() === "PUT",
      );
      expect(putCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(putCalls[0]![1]!.body as string);
      expect(body.model).toBe("ollama:qwen3:4b");
    });
  });
});
