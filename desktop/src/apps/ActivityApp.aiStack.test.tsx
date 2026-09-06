import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AiStackRecovery } from "./ActivityApp.aiStack";

describe("AiStackRecovery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requires confirmation before restarting", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<AiStackRecovery />);
    fireEvent.click(screen.getByRole("button", { name: /restart ai services/i }));
    // The confirm dialog is shown; no request has been made yet.
    expect(screen.getByText(/restart ai services\?/i)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("posts the restart and reports per-service results on confirm", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "ok",
          restarted: ["rkllama.service", "qmd.service"],
          failed: [],
          results: [
            { unit: "rkllama.service", ok: true, scope: "user" },
            { unit: "qmd.service", ok: true, scope: "system" },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const onRecovered = vi.fn();
    render(<AiStackRecovery onRecovered={onRecovered} />);
    fireEvent.click(screen.getByRole("button", { name: /restart ai services/i }));
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));

    await waitFor(() => expect(screen.getByText(/ai services restarted/i)).toBeInTheDocument());
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/system/ai-stack/restart",
      expect.objectContaining({ method: "POST" }),
    );
    expect(onRecovered).toHaveBeenCalled();
    expect(screen.getByText("rkllama")).toBeInTheDocument();
    expect(screen.getByText("qmd")).toBeInTheDocument();
  });

  it("surfaces a per-service failure detail on partial recovery", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "partial",
          restarted: ["rkllama.service"],
          failed: [{ unit: "qmd.service", ok: false, detail: "Interactive authentication required" }],
          results: [
            { unit: "rkllama.service", ok: true, scope: "user" },
            { unit: "qmd.service", ok: false, detail: "Interactive authentication required" },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<AiStackRecovery />);
    fireEvent.click(screen.getByRole("button", { name: /restart ai services/i }));
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));
    // Partial recovery must not read as full success: amber heading + the
    // per-service failure detail + the "could not be restarted" note.
    await waitFor(() =>
      expect(screen.getByText(/some ai services restarted/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/interactive authentication required/i)).toBeInTheDocument();
    expect(screen.getByText(/some services could not be restarted/i)).toBeInTheDocument();
  });

  it("shows an admin-only message on 403", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "forbidden" }), { status: 403 }),
    );
    render(<AiStackRecovery />);
    fireEvent.click(screen.getByRole("button", { name: /restart ai services/i }));
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));
    await waitFor(() => expect(screen.getByText(/admin access is required/i)).toBeInTheDocument());
  });
});
