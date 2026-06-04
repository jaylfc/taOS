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
