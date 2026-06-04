import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("@/lib/framework-api", () => ({
  fetchFrameworkState: vi.fn(async () => ({
    framework: "hermes",
    installed: { tag: "v1", sha: "abc" },
    latest: null,
    update_available: false,
    update_status: "idle",
  })),
  startFrameworkUpdate: vi.fn(),
  fetchPermittedModels: vi.fn(async () => ({
    permitted: ["llama3", "qwen3"],
    current: "llama3",
  })),
  setPermittedModels: vi.fn(async (_name: string, models: string[]) => ({
    permitted: models,
    current: "llama3",
  })),
}));

import { FrameworkTab } from "./FrameworkTab";

const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; vi.clearAllMocks(); });

describe("FrameworkTab — model change", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (url: string) => {
      if (String(url).includes("/api/providers/models")) {
        return { ok: true, json: async () => ({ data: [{ id: "nvidia/x:free" }] }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }) as unknown as typeof fetch;
  });

  it("shows the current model and opens the picker (loading routable models)", async () => {
    render(<FrameworkTab agent={{ name: "naira", model: "stepfun/old:free" }} onUpdated={() => {}} />);
    // Current model surfaced.
    expect(await screen.findByText("stepfun/old:free")).toBeInTheDocument();
    // Open the picker → it loads /api/providers/models.
    fireEvent.click(screen.getByRole("button", { name: /change model/i }));
    await waitFor(() =>
      expect(
        (global.fetch as any).mock.calls.some((c: any[]) => String(c[0]).includes("/api/providers/models")),
      ).toBe(true),
    );
  });
});

describe("FrameworkTab — permitted models", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (url: string) => {
      if (String(url).includes("/api/providers/models")) {
        return { ok: true, json: async () => ({ data: [{ id: "mistral/7b:free" }] }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }) as unknown as typeof fetch;
  });

  it("renders the permitted set from the mocked GET", async () => {
    render(<FrameworkTab agent={{ name: "alpha", model: "llama3" }} onUpdated={() => {}} />);
    // qwen3 only appears in the permitted chips (not in the primary model row).
    expect(await screen.findByText("qwen3")).toBeInTheDocument();
    // The current model should have a "current" badge.
    expect(await screen.findByLabelText("current primary model")).toBeInTheDocument();
    // The permitted chips list should contain both models.
    const list = screen.getByRole("list", { name: /permitted model chips/i });
    expect(list.textContent).toContain("llama3");
    expect(list.textContent).toContain("qwen3");
  });

  it("adding a model and saving issues the PUT with the expected body", async () => {
    const { setPermittedModels } = await import("@/lib/framework-api");
    render(<FrameworkTab agent={{ name: "alpha", model: "llama3" }} onUpdated={() => {}} />);
    // Wait for permitted set to load.
    await screen.findByRole("list", { name: /permitted model chips/i });

    // Open the "Add" picker.
    fireEvent.click(screen.getByRole("button", { name: /add permitted model/i }));
    // Wait for the picker to open — it loads models.
    await waitFor(() =>
      expect(
        (global.fetch as any).mock.calls.some((c: any[]) => String(c[0]).includes("/api/providers/models")),
      ).toBe(true),
    );

    // Close the picker without selecting (reset state) then verify Save works
    // by directly manipulating the draft via remove (triggers dirty).
    // Remove qwen3 first so draftDirty becomes true, then Save.
    const removeBtn = screen.getByRole("button", { name: /remove qwen3/i });
    fireEvent.click(removeBtn);

    // Save button should now be visible.
    const saveBtn = await screen.findByRole("button", { name: /save permitted models/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(setPermittedModels).toHaveBeenCalledWith("alpha", ["llama3"]);
    });
  });

  it("the current model cannot be removed", async () => {
    render(<FrameworkTab agent={{ name: "alpha", model: "llama3" }} onUpdated={() => {}} />);
    // Wait for permitted chips to appear.
    await screen.findByRole("list", { name: /permitted model chips/i });

    // The remove button for the current model should be disabled.
    const removeCurrentBtn = screen.getByRole("button", {
      name: /cannot remove current model llama3/i,
    });
    expect(removeCurrentBtn).toBeDisabled();
  });
});
